"""The review-2 reviewer bundle: the minimal, separate workspace for the blind second review.

`load_sealed` reads a verified `primary_sealed` stage. With `primary_labels=False` (the bundle
path) it never opens `primary_judgements.jsonl` or `adjudication_queue.jsonl`: it verifies the
manifest chain and the sample's bytes against their manifest entries only. With
`primary_labels=True` (the second-review seal, run after the blind round) it also verifies the
primary judgements and regenerates the sample and the queue byte for byte.

`build_bundle` returns the bundle's files: `bundle.json` (one canonical JSON object: the sample
pairs in cs-1 blind order, each with its query text, interpretations, frozen gold constraints
and soft intents, the rendered `card-1` card and its hash, and the two hard-constraint flags the
primary round's tool also used only to refuse `relevant` and to require a rationale), the rubric
file supplied by the operator, the stdlib-only tool `review.py` and a README. It never contains
a primary label, the sample's per-(label, query) allocation, a machine proposal or weak label,
the provenance, a source, rank, score, agreement figure or adjudication queue. The bytes are
deterministic, so the seal can regenerate and compare them.

Blindness is procedural (the bundle simply omits these), not cryptographic isolation.
"""

import json
from dataclasses import dataclass
from pathlib import Path

from evals.contracts.canonical import (
    decode_json_object,
    decode_jsonl,
    encode_json_object,
    encode_jsonl,
    sha256_hex,
)
from evals.contracts.cards import card_sha256, render_card
from evals.contracts.catalog import CatalogIndex, load_catalog_index
from evals.contracts.constants import (
    CARD_VERSION,
    REVIEW_PROTOCOL_2,
    REVIEWER_BUNDLE_VERSION,
    SECOND_REVIEWER_HANDLE,
)
from evals.contracts.manifest import (
    Manifest,
    Role,
    Stage,
    decode_manifest,
    validate_transition,
    verify_entry,
)
from evals.contracts.models import (
    ConsistencySample,
    EligibilityRecord,
    GoldConstraint,
    Judgement,
    OverallStatus,
    PoolItem,
    ProductIntent,
    QueryRecord,
    Round,
    canonical_gold_value,
)
from evals.contracts.review_sets import build_adjudication_queue, build_consistency_sample
from evals.runners import build_pool
from evals.runners.golden_io import FreezeError, read_frozen, refuse_stale_staging, safe_golden_dir

PRIMARY_SEALED_MANIFEST = "manifests/3-primary_sealed.json"
TOOL_SOURCE = Path(__file__).with_name("reviewer_tool.py")
BUNDLE_FILE = "bundle.json"
TOOL_FILE = "review.py"
README_FILE = "README.txt"
RUBRIC_STEM = "rubric"
# Paths that must never appear in a bundle (checked by name as defence in depth).
FORBIDDEN_NAMES = frozenset(
    {
        "primary_judgements.jsonl",
        "consistency_sample.json",
        "adjudication_queue.jsonl",
        "agreement_report.json",
        "pool.jsonl",
        "pool_report.json",
        "eligibility.jsonl",
        "provenance",
    }
)


class BundleError(FreezeError):
    """The reviewer bundle cannot be built or verified; nothing was written."""


@dataclass(frozen=True)
class SealedPrimary:
    golden_dir: Path
    manifest: Manifest
    queries: dict[str, QueryRecord]
    constraints: tuple[GoldConstraint, ...]
    intents: tuple[ProductIntent, ...]
    pool: dict[tuple[str, str], PoolItem]
    eligibility: dict[tuple[str, str], EligibilityRecord]
    sample: ConsistencySample
    catalog: CatalogIndex
    primaries: tuple[Judgement, ...] | None  # None on the blind (bundle) path


def load_sealed(
    golden_dir: Path,
    *,
    allowed_root: Path = build_pool.ALLOWED_ROOT,
    catalog: CatalogIndex | None = None,
    primary_labels: bool,
) -> SealedPrimary:
    catalog = catalog or load_catalog_index()
    verified = build_pool.verify_pool(
        golden_dir, allowed_root=allowed_root, public_only=True, catalog=catalog
    )
    golden = safe_golden_dir(golden_dir, allowed_root)
    refuse_stale_staging(golden)
    sealed = decode_manifest(read_frozen(golden, PRIMARY_SEALED_MANIFEST))
    if sealed.stage is not Stage.PRIMARY_SEALED or sealed.review_protocol is not None:
        raise BundleError("expected the unchanged primary_sealed manifest")
    validate_transition(verified.manifest, sealed)
    sample_entry = sealed.entry(Role.CONSISTENCY_SAMPLE)
    sample_bytes = read_frozen(golden, sample_entry.path)
    verify_entry(sample_entry, sample_bytes)
    sample = decode_json_object(sample_bytes, ConsistencySample)
    if (sample.primary_seal_sha256, sample.primary_sealed_at) != (
        sealed.primary_seal_sha256(),
        sealed.primary_sealed_at,
    ):
        raise BundleError("the sample is not bound to the sealed primary round")
    queries = decode_jsonl(read_frozen(golden, "queries.jsonl"), QueryRecord)
    constraints = decode_jsonl(read_frozen(golden, "gold_constraints.jsonl"), GoldConstraint)
    intents = decode_jsonl(read_frozen(golden, "product_intents.jsonl"), ProductIntent)
    pool = decode_jsonl(read_frozen(golden, sealed.entry(Role.POOL).path), PoolItem)
    eligibility = decode_jsonl(
        read_frozen(golden, sealed.entry(Role.ELIGIBILITY).path), EligibilityRecord
    )
    primaries = None
    if primary_labels:
        primary_entry = sealed.entry(Role.PRIMARY_JUDGEMENTS)
        data = read_frozen(golden, primary_entry.path)
        verify_entry(primary_entry, data)
        primaries = tuple(decode_jsonl(data, Judgement))
        if any(j.round is not Round.PRIMARY for j in primaries):
            raise BundleError("the sealed primary file holds a non-primary judgement")
        regenerated = build_consistency_sample(
            primaries,
            sealed.primary_seal_sha256(),
            sealed.primary_sealed_at,  # type: ignore[arg-type]
        )
        verify_entry(sample_entry, sample_bytes, regenerated=encode_json_object(regenerated))
        queue_entry = sealed.entry(Role.ADJUDICATION_QUEUE)
        queue = build_adjudication_queue(primaries, queries, eligibility)
        verify_entry(
            queue_entry, read_frozen(golden, queue_entry.path), regenerated=encode_jsonl(queue)
        )
    return SealedPrimary(
        golden_dir=golden,
        manifest=sealed,
        queries={q.query_id: q for q in queries},
        constraints=tuple(constraints),
        intents=tuple(intents),
        pool={(i.query_id, i.product_id): i for i in pool},
        eligibility={(e.query_id, e.product_id): e for e in eligibility},
        sample=sample,
        catalog=catalog,
        primaries=primaries,
    )


def _constraint_lines(sealed: SealedPrimary, query_id: str) -> list[str]:
    own = sorted(
        (c for c in sealed.constraints if c.query_id == query_id), key=lambda c: c.sort_key()
    )
    return [
        f"{c.hardness.value} gold constraint: {c.field.value} {c.operator.value} "
        f"{canonical_gold_value(c.value)}"
        for c in own
    ]


def _intent_lines(sealed: SealedPrimary, query_id: str) -> list[str]:
    own = sorted((i for i in sealed.intents if i.query_id == query_id), key=lambda i: i.sort_key())
    return [f"soft {i.kind.value} intent: {i.value}" for i in own]


def bundle_items(sealed: SealedPrimary) -> list[dict]:
    items = []
    for sample_item in sealed.sample.items:
        pair = (sample_item.query_id, sample_item.product_id)
        pooled = sealed.pool.get(pair)
        if pooled is None:
            raise BundleError(f"{pair!r}: a sampled pair is not in the frozen pool")
        product = sealed.catalog.products[sample_item.product_id]
        if card_sha256(product) != pooled.card_sha256:
            raise BundleError(f"{pair!r}: the product card changed after the pool was frozen")
        query = sealed.queries[sample_item.query_id]
        status = sealed.eligibility[pair].hard_overall
        items.append(
            {
                "card_sha256": pooled.card_sha256,
                "card_text": render_card(product),
                "constraints": _constraint_lines(sealed, query.query_id),
                "hard_unknown": status is OverallStatus.UNKNOWN,
                "hard_violated": status is OverallStatus.VIOLATED,
                "intents": _intent_lines(sealed, query.query_id),
                "interpretations": list(query.interpretations),
                "position": sample_item.blind_position,
                "product_id": sample_item.product_id,
                "query_id": query.query_id,
                "query_text": query.text,
            }
        )
    return items


def _readme(rubric_file: str, count: int) -> bytes:
    text = f"""Blind second review (review-2), annotator ann-2

This folder is the complete second-review workspace. It holds {count} query-product pairs in
blind order, the rubric ({rubric_file}) and the review tool (review.py, Python standard library
only). It does not hold the primary annotator's labels, any machine or AI label or proposal,
search sources, ranks or scores, sample allocation counts, agreement figures or adjudication
queues. Please do not look for them elsewhere until your review is sealed.

Review independently: do not view the project's AI-assistant conversations, the primary
labels or any machine proposals, before or during your review.

  python review.py annotate --annotator ann-2
  python review.py status
  python review.py finish

Each pair is asked step by step: label, relation, confidence, rationale (when required),
needs adjudication, catalog issue, then a summary that you confirm with y to save (anything
else discards that pair's answers and asks again). Experienced reviewers may instead add
--compact to type each decision on one line (? shows the codes; the trailing ! saves). q stops
at any prompt; a later run resumes. When every pair is judged, `finish` prints a SHA-256: you
confirm your own review by giving exactly that value to the seal command.

Blindness is procedural (this folder omits the labels); it is not cryptographic isolation.
"""
    return text.encode("utf-8")


def build_bundle(sealed: SealedPrimary, rubric: bytes, rubric_suffix: str) -> dict[str, bytes]:
    """The bundle's files (deterministic for the same sealed stage, rubric and tool)."""
    if not rubric:
        raise BundleError("the rubric file is empty")
    if not rubric_suffix.startswith(".") or "/" in rubric_suffix or "\\" in rubric_suffix:
        raise BundleError("the rubric suffix must be a plain extension such as .md")
    rubric_file = RUBRIC_STEM + rubric_suffix
    tool = TOOL_SOURCE.read_bytes()
    items = bundle_items(sealed)
    body = {
        "annotator": SECOND_REVIEWER_HANDLE,
        "bundle_version": REVIEWER_BUNDLE_VERSION,
        "card_version": CARD_VERSION,
        "golden_id": sealed.manifest.golden_id,
        "golden_version": sealed.manifest.golden_version,
        "item_count": len(items),
        "items": items,
        "primary_sealed_manifest_sha256": sealed.manifest.manifest_sha256,
        "review_protocol": REVIEW_PROTOCOL_2,
        "round": Round.SECOND_REVIEW.value,
        "rubric_file": rubric_file,
        "rubric_sha256": sha256_hex(rubric),
        "rubric_version": sealed.manifest.versions.rubric,
        "tool_sha256": sha256_hex(tool),
    }
    text = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
    files = {
        BUNDLE_FILE: text.encode("utf-8"),
        rubric_file: rubric,
        TOOL_FILE: tool,
        README_FILE: _readme(rubric_file, len(items)),
    }
    leaked = sorted(set(files) & FORBIDDEN_NAMES)
    if leaked:  # pragma: no cover - the names above are fixed
        raise BundleError(f"refusing to place {leaked!r} in a reviewer bundle")
    return files


def bundle_rubric(workspace: Path) -> tuple[bytes, str]:
    """The rubric bytes and suffix recorded by a delivered bundle (for regeneration)."""
    body = json.loads((workspace / BUNDLE_FILE).read_bytes())
    name = body.get("rubric_file", "")
    if not isinstance(name, str) or not name.startswith(RUBRIC_STEM + "."):
        raise BundleError("the bundle names no valid rubric file")
    return (workspace / name).read_bytes(), name[len(RUBRIC_STEM) :]


def is_inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True
