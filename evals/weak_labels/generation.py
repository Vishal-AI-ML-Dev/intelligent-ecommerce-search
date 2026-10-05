"""Build and verify the `weak_dev_v1` proposal generation (everything except model inference).

`build_generation` turns the verified frozen facts and the recomputed cross-encoder logits into
four deterministic byte strings:

* private `signals.jsonl` (`WeakSignal`: logits, pool ranks, retrieval hits; git-ignored);
* public `proposals.jsonl` (`WeakLabelProposal`, bands and short reasons only);
* public `audit_queue.jsonl` (`AuditQueueItem`, `weak-audit-1`);
* public `proposal_manifest.json` (`ProposalManifest`; no wall-clock time).

Before anything is built, every V3 reranker score in the verified private pool provenance must
be reproduced by the recomputed logit within `RERANKER_TOLERANCE`, and the provenance's
reranker pins must equal the pinned spec; otherwise generation stops.

`verify_generation` re-derives the signals' ranks and retrieval hits from the stored logits and
the provenance, regenerates proposals, queue and manifest without any model, and compares
every byte.
"""

import math
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from evals.contracts.canonical import (
    ContractError,
    decode_json_object,
    decode_jsonl,
    encode_json_object,
    encode_jsonl,
    sha256_hex,
)
from evals.contracts.catalog import ROOT, CatalogIndex, load_catalog_index
from evals.contracts.constants import LABEL_ORDER
from evals.contracts.manifest import Manifest, Role
from evals.contracts.models import (
    EligibilityRecord,
    GoldConstraint,
    PoolItem,
    PoolProvenance,
    ProductIntent,
    QueryRecord,
    SourceSystem,
)
from evals.runners import build_pool, freeze_queries
from evals.runners.golden_io import read_frozen, safe_golden_dir
from evals.weak_labels.contracts import (
    CONFIDENCE_ORDER,
    RERANKER_TOLERANCE,
    AuditQueueItem,
    CountEntry,
    NamedHash,
    ProposalManifest,
    WeakLabelProposal,
    WeakSignal,
)
from evals.weak_labels.policy import propose, query_facts
from evals.weak_labels.sampling import build_queue, validate_queue

WEAKSET_ROOT = ROOT / "evals" / "weaksets"
DEFAULT_WEAK_DIR = WEAKSET_ROOT / "internal-search" / "v1"
PRIVATE_ROOT = ROOT / "data" / "processed" / "weak_labels"
PROPOSALS_FILE = "proposals.jsonl"
QUEUE_FILE = "audit_queue.jsonl"
MANIFEST_FILE = "proposal_manifest.json"
RUN_FILE = "generation_run.json"
SIGNALS_FILE = "signals.jsonl"
PUBLIC_FILES = (PROPOSALS_FILE, QUEUE_FILE)
V3_SYSTEMS = frozenset({SourceSystem.V3_RERANKED_D10, SourceSystem.V3_RERANKED_D20})

Pair = tuple[str, str]


class WeakLabelError(ContractError):
    """The weak-label workflow cannot proceed; nothing was written (or it was rolled back)."""


@dataclass(frozen=True)
class RerankerPin:
    model_id: str
    revision: str
    config_sha256: str
    document_version: str


@dataclass(frozen=True)
class FrozenFacts:
    queries_manifest_sha256: str
    pool_manifest: Manifest
    queries: tuple[QueryRecord, ...]
    constraints: tuple[GoldConstraint, ...]
    intents: tuple[ProductIntent, ...]
    pool: tuple[PoolItem, ...]
    eligibility: Mapping[Pair, EligibilityRecord]
    catalog: CatalogIndex
    rubric_version: str
    provenance: PoolProvenance | None


def pinned_reranker() -> RerankerPin:
    from ecommerce_search.reranking.documents import RERANK_DOCUMENT_VERSION
    from ecommerce_search.reranking.spec import DEFAULT_RERANKER_MODEL_ID, RERANKER_MODELS

    spec = RERANKER_MODELS[DEFAULT_RERANKER_MODEL_ID]
    return RerankerPin(spec.model_id, spec.revision, spec.config_sha256(), RERANK_DOCUMENT_VERSION)


def load_facts(
    golden_dir: Path = build_pool.DEFAULT_GOLDEN_DIR,
    *,
    allowed_root: Path = build_pool.ALLOWED_ROOT,
    private_root: Path = build_pool.PRIVATE_ROOT,
    with_provenance: bool = True,
    catalog: CatalogIndex | None = None,
) -> FrozenFacts:
    """Read only a verified `pool_frozen` stage (read-only; the strict files are never
    written). With `with_provenance`, the private provenance is verified and decoded too."""
    catalog = catalog or load_catalog_index()
    verified = build_pool.verify_pool(
        golden_dir,
        allowed_root=allowed_root,
        private_root=private_root,
        public_only=not with_provenance,
        catalog=catalog,
    )
    golden = freeze_queries.safe_golden_dir(golden_dir, allowed_root)
    queries_manifest = build_pool.load_frozen_queries(
        golden, allowed_root=allowed_root, catalog=catalog
    ).manifest
    provenance = None
    if verified.provenance_path is not None:
        provenance = decode_json_object(verified.provenance_path.read_bytes(), PoolProvenance)
    pool = decode_jsonl(read_frozen(golden, build_pool.POOL_PATHS[Role.POOL]), PoolItem)
    eligibility = decode_jsonl(
        read_frozen(golden, build_pool.POOL_PATHS[Role.ELIGIBILITY]), EligibilityRecord
    )
    return FrozenFacts(
        queries_manifest_sha256=queries_manifest.manifest_sha256,
        pool_manifest=verified.manifest,
        queries=tuple(decode_jsonl(read_frozen(golden, "queries.jsonl"), QueryRecord)),
        constraints=tuple(
            decode_jsonl(read_frozen(golden, "gold_constraints.jsonl"), GoldConstraint)
        ),
        intents=tuple(decode_jsonl(read_frozen(golden, "product_intents.jsonl"), ProductIntent)),
        pool=tuple(sorted(pool, key=lambda i: (i.query_id, i.product_id))),
        eligibility={(e.query_id, e.product_id): e for e in eligibility},
        catalog=catalog,
        rubric_version=verified.manifest.versions.rubric,
        provenance=provenance,
    )


def _require_provenance(facts: FrozenFacts) -> PoolProvenance:
    if facts.provenance is None:
        raise WeakLabelError("the verified private pool provenance is required")
    return facts.provenance


def check_reranker_pins(provenance: PoolProvenance, pin: RerankerPin) -> None:
    pins = {v.name: v.value for v in provenance.run_context.model_pins}
    components = {v.name: v.value for v in provenance.run_context.component_versions}
    expected = {
        "reranker_model_id": pin.model_id,
        "reranker_revision": pin.revision,
        "reranker_config_sha256": pin.config_sha256,
    }
    if any(pins.get(k) != v for k, v in expected.items()) or (
        components.get("rerank_document_version") != pin.document_version
    ):
        raise WeakLabelError("the pinned reranker differs from the one recorded in provenance")


def check_logits_against_provenance(
    provenance: PoolProvenance, logits: Mapping[Pair, float]
) -> tuple[float, int]:
    """Every V3 score in the provenance must be reproduced within the tolerance. Returns the
    largest absolute difference and the number of scores checked."""
    worst, checked = 0.0, 0
    for entry in provenance.entries:
        for hit in entry.sources:
            if hit.system not in V3_SYSTEMS or hit.score is None:
                continue
            recomputed = logits.get((entry.query_id, entry.product_id))
            if recomputed is None or not math.isfinite(recomputed):
                raise WeakLabelError("a provenance reranker score has no recomputed logit")
            difference = abs(float(hit.score) - recomputed)
            if difference > RERANKER_TOLERANCE:
                raise WeakLabelError(
                    "the recomputed cross-encoder logits do not reproduce the provenance "
                    f"reranker scores (difference {difference:.3g} > {RERANKER_TOLERANCE})"
                )
            worst, checked = max(worst, difference), checked + 1
    if checked == 0:
        raise WeakLabelError("the provenance holds no reranker score to check against")
    return worst, checked


def build_signals(facts: FrozenFacts, logits: Mapping[Pair, float]) -> list[WeakSignal]:
    provenance = _require_provenance(facts)
    pairs = {(i.query_id, i.product_id) for i in facts.pool}
    if set(logits) != pairs:
        raise WeakLabelError("logits must cover exactly the frozen pool")
    entries = {(e.query_id, e.product_id): e for e in provenance.entries}
    if set(entries) != pairs:
        raise WeakLabelError("the provenance entries differ from the frozen pool")
    by_query: dict[str, list[str]] = {}
    for query_id, product_id in sorted(pairs):
        by_query.setdefault(query_id, []).append(product_id)
    signals = []
    for query_id, product_ids in by_query.items():
        ranked = sorted(product_ids, key=lambda p: (-logits[(query_id, p)], p))
        for rank, product_id in enumerate(ranked, start=1):
            value = logits[(query_id, product_id)]
            if not math.isfinite(value):
                raise WeakLabelError("a cross-encoder logit is not finite")
            entry = entries[(query_id, product_id)]
            signals.append(
                WeakSignal(
                    query_id=query_id,
                    product_id=product_id,
                    reranker_logit=repr(float(value)),
                    reranker_rank_in_pool=rank,
                    pool_size=len(product_ids),
                    added_by=entry.added_by,
                    source_hits=entry.sources,
                )
            )
    return sorted(signals, key=lambda s: s.sort_key())


def build_proposals(
    facts: FrozenFacts, signals: list[WeakSignal], pin: RerankerPin
) -> list[WeakLabelProposal]:
    by_pair = {(s.query_id, s.product_id): s for s in signals}
    if set(by_pair) != {(i.query_id, i.product_id) for i in facts.pool}:
        raise WeakLabelError("signals must cover exactly the frozen pool")
    proposals = []
    for query in sorted(facts.queries, key=lambda q: q.query_id):
        items = [i for i in facts.pool if i.query_id == query.query_id]
        pool_products = [
            (facts.catalog.products[i.product_id], facts.eligibility[(i.query_id, i.product_id)])
            for i in items
        ]
        qf = query_facts(query, facts.constraints, facts.intents, pool_products)
        for item in items:
            pair = (item.query_id, item.product_id)
            product = facts.catalog.products[item.product_id]
            eligibility = facts.eligibility[pair]
            result = propose(qf, product, eligibility, by_pair[pair])
            proposals.append(
                WeakLabelProposal(
                    query_id=item.query_id,
                    product_id=item.product_id,
                    proposed_label=result.label,
                    proposed_relation=result.relation,
                    proposal_confidence=result.confidence,
                    proposal_rule=result.rule,
                    hard_constraint_status=eligibility.hard_overall,
                    violated_fields=result.violated,
                    unknown_fields=result.unknown,
                    reranker_band=result.reranker_band,
                    retrieval_consensus=result.consensus,
                    signal_conflict=result.conflict,
                    requires_human_review=(
                        result.conflict or result.confidence is not CONFIDENCE_ORDER[-1]
                    ),
                    reason=result.reason,
                    pool_manifest_sha256=facts.pool_manifest.manifest_sha256,
                    product_content_sha256=item.product_content_sha256,
                    card_sha256=item.card_sha256,
                    catalog_checksum_sha256=facts.catalog.checksum_sha256,
                    reranker_model_id=pin.model_id,
                    reranker_revision=pin.revision,
                    rerank_document_version=pin.document_version,
                    rubric_version=facts.rubric_version,
                )
            )
    return sorted(proposals, key=lambda p: p.sort_key())


def _counts(values: list[str], order: list[str]) -> tuple[CountEntry, ...]:
    return tuple(CountEntry(key=k, count=values.count(k)) for k in order)


def build_generation(
    facts: FrozenFacts, logits: Mapping[Pair, float], pin: RerankerPin
) -> dict[str, bytes]:
    """The four deterministic files of one generation (see the module docstring)."""
    provenance = _require_provenance(facts)
    check_reranker_pins(provenance, pin)
    check_logits_against_provenance(provenance, logits)
    return files_from_signals(facts, build_signals(facts, logits), pin)


def files_from_signals(
    facts: FrozenFacts, signals: list[WeakSignal], pin: RerankerPin
) -> dict[str, bytes]:
    signals_bytes = encode_jsonl(signals)
    proposals = build_proposals(facts, signals, pin)
    queue = build_queue(proposals, facts.queries)
    public = {PROPOSALS_FILE: encode_jsonl(proposals), QUEUE_FILE: encode_jsonl(queue)}
    provenance_binding = facts.pool_manifest.provenance_binding
    if provenance_binding is None:  # pragma: no cover - pool_frozen always binds it
        raise WeakLabelError("the pool manifest has no provenance binding")
    manifest = ProposalManifest(
        queries_manifest_sha256=facts.queries_manifest_sha256,
        pool_manifest_sha256=facts.pool_manifest.manifest_sha256,
        provenance_binding=provenance_binding,
        signals_binding=sha256_hex(signals_bytes),
        catalog_checksum_sha256=facts.catalog.checksum_sha256,
        reranker_model_id=pin.model_id,
        reranker_revision=pin.revision,
        reranker_config_sha256=pin.config_sha256,
        rerank_document_version=pin.document_version,
        rubric_version=facts.rubric_version,
        files=tuple(NamedHash(path=p, sha256=sha256_hex(public[p])) for p in PUBLIC_FILES),
        proposal_count=len(proposals),
        audit_count=len(queue),
        by_label=_counts(
            [p.proposed_label.value for p in proposals], [label.value for label in LABEL_ORDER]
        ),
        by_confidence=_counts(
            [p.proposal_confidence.value for p in proposals],
            [c.value for c in reversed(CONFIDENCE_ORDER)],
        ),
        requires_review_count=sum(p.requires_human_review for p in proposals),
    )
    return {**public, MANIFEST_FILE: encode_json_object(manifest), SIGNALS_FILE: signals_bytes}


# ---- reading and verifying a stored generation ----------------------------------------------


@dataclass(frozen=True)
class Generation:
    manifest: ProposalManifest
    manifest_sha256: str
    proposals: tuple[WeakLabelProposal, ...]
    queue: tuple[AuditQueueItem, ...]


def safe_weak_dir(weak_dir: Path, allowed_root: Path = WEAKSET_ROOT) -> Path:
    directory = safe_golden_dir(weak_dir, allowed_root)
    if directory.resolve().is_relative_to(build_pool.ALLOWED_ROOT.resolve()):
        raise WeakLabelError("weak-label artifacts never go into the strict Golden directory")
    return directory


def read_generation(
    facts: FrozenFacts, weak_dir: Path = DEFAULT_WEAK_DIR, *, allowed_root: Path = WEAKSET_ROOT
) -> Generation:
    """The public generation files, checked against the manifest, the frozen pool, the
    catalog cards and each other (no model; the private signals are not read)."""
    directory = safe_weak_dir(weak_dir, allowed_root)
    manifest_bytes = read_frozen(directory, MANIFEST_FILE)
    manifest = decode_json_object(manifest_bytes, ProposalManifest)
    if manifest.pool_manifest_sha256 != facts.pool_manifest.manifest_sha256:
        raise WeakLabelError("the proposals are bound to another pool_frozen manifest")
    if manifest.catalog_checksum_sha256 != facts.catalog.checksum_sha256:
        raise WeakLabelError("the proposals are bound to another catalog")
    if [f.path for f in manifest.files] != list(PUBLIC_FILES):
        raise WeakLabelError("unexpected proposal manifest file list")
    content = {}
    for entry in manifest.files:
        content[entry.path] = read_frozen(directory, entry.path)
        if sha256_hex(content[entry.path]) != entry.sha256:
            raise WeakLabelError(f"{entry.path} does not match the proposal manifest")
    proposals = decode_jsonl(content[PROPOSALS_FILE], WeakLabelProposal)
    queue = decode_jsonl(content[QUEUE_FILE], AuditQueueItem)
    pool = {(i.query_id, i.product_id): i for i in facts.pool}
    if {(p.query_id, p.product_id) for p in proposals} != set(pool) or len(proposals) != len(pool):
        raise WeakLabelError("the proposals do not cover exactly the frozen pool")
    for p in proposals:
        item = pool[(p.query_id, p.product_id)]
        if (p.card_sha256, p.product_content_sha256) != (
            item.card_sha256,
            item.product_content_sha256,
        ):
            raise WeakLabelError(f"{p.query_id}/{p.product_id}: product card changed")
        if p.pool_manifest_sha256 != facts.pool_manifest.manifest_sha256:
            raise WeakLabelError(f"{p.query_id}/{p.product_id}: bound to another pool")
    validate_queue(queue, proposals, facts.queries)
    return Generation(
        manifest=manifest,
        manifest_sha256=sha256_hex(manifest_bytes),
        proposals=tuple(proposals),
        queue=tuple(queue),
    )


def signals_path(facts: FrozenFacts, private_root: Path = PRIVATE_ROOT) -> Path:
    return private_root / facts.pool_manifest.manifest_sha256[:16] / SIGNALS_FILE


def verify_generation(
    facts: FrozenFacts,
    weak_dir: Path = DEFAULT_WEAK_DIR,
    *,
    allowed_root: Path = WEAKSET_ROOT,
    private_root: Path = PRIVATE_ROOT,
    pin: RerankerPin | None = None,
) -> Generation:
    """Full offline re-verification without a model: public files, the private signals'
    binding, every derived signal field against the provenance, and byte-identical
    regeneration of proposals, queue and manifest from the stored logits."""
    generation = read_generation(facts, weak_dir, allowed_root=allowed_root)
    provenance = _require_provenance(facts)
    pin = pin or pinned_reranker()
    check_reranker_pins(provenance, pin)
    path = signals_path(facts, private_root)
    if not path.is_file():
        raise WeakLabelError("the private weak-label signals are missing from this checkout")
    stored = path.read_bytes()
    if sha256_hex(stored) != generation.manifest.signals_binding:
        raise WeakLabelError("the private signals do not match signals_binding")
    signals = decode_jsonl(stored, WeakSignal)
    logits = {(s.query_id, s.product_id): float(s.reranker_logit) for s in signals}
    check_logits_against_provenance(provenance, logits)
    if encode_jsonl(build_signals(facts, logits)) != stored:
        raise WeakLabelError("the private signals are not consistent with the provenance")
    regenerated = files_from_signals(facts, signals, pin)
    directory = safe_weak_dir(weak_dir, allowed_root)
    for name in (*PUBLIC_FILES, MANIFEST_FILE):
        if regenerated[name] != read_frozen(directory, name):
            raise WeakLabelError(f"{name} does not regenerate byte for byte")
    return generation
