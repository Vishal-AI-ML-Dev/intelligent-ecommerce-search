"""Cross-record validators. Pure and offline: they read records (and, where stated, the
read-only catalog index) and raise a `ContractError` naming the first problem found.

None of these functions creates, prefills, trims or repairs anything: a validator reports and
the caller stops.

Wiring status (Session C1): the `queries_frozen` freeze runner
(`evals.runners.freeze_queries`) calls the query, gold-constraint, product-intent, attestation,
extraction and pilot validators before it writes anything. The pool, judgement, review-set and
correction validators are still enforced only by their unit tests; wiring them into the runners
of later stages is later-session work.
"""

import hashlib
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from ecommerce_search.catalog.taxonomy import SUBCATEGORIES, Category
from ecommerce_search.filtering.policy import FilterOperator
from evals.contracts.canonical import ContractError
from evals.contracts.catalog import CatalogIndex
from evals.contracts.constants import (
    BLIND_ORDER_VERSION,
    ELIGIBILITY_COMPLETION_CAP,
    MAX_POOL_SIZE_PER_QUERY,
    MIN_CONSISTENCY_DELAY,
    NEGATIVES_PER_QUERY,
    POOL_DEPTH,
    SINGLE_ANNOTATOR_HANDLE,
    Label,
)
from evals.contracts.extraction import derive_query_extraction
from evals.contracts.manifest import (
    STAGE_ORDER,
    Manifest,
    Role,
    Stage,
    decode_role,
    validate_chain,
    validate_succession,
    verify_entry,
)
from evals.contracts.models import (
    FIELD_CATEGORIES,
    AuthoringAttestation,
    ConsistencySample,
    EligibilityRecord,
    FinalJudgement,
    GoldConstraint,
    GoldField,
    Hardness,
    InclusionReason,
    Judgement,
    LabelChange,
    LabelChangeKind,
    OverallStatus,
    PartitionAssignment,
    PoolItem,
    ProductIntent,
    ProvenanceEntry,
    QueryExtraction,
    QueryRecord,
    QueueItem,
    ReviewReason,
    Round,
    SourceSystem,
)
from evals.contracts.review_sets import (
    build_consistency_sample,
    consistency_disagreements,
    derive_final_judgements,
)

Pair = tuple[str, str]


def _pairs(records: Iterable[PoolItem | Judgement | EligibilityRecord]) -> list[Pair]:
    return [(r.query_id, r.product_id) for r in records]


def _unique(pairs: list[Pair], what: str) -> set[Pair]:
    duplicates = sorted(p for p, n in Counter(pairs).items() if n > 1)
    if duplicates:
        raise ContractError(f"duplicate {what}: {duplicates[:5]!r}")
    return set(pairs)


# ---- queries and partitions -----------------------------------------------------------------


def validate_queries(queries: Sequence[QueryRecord]) -> None:
    ids = [q.query_id for q in queries]
    if len(set(ids)) != len(ids):
        raise ContractError("query ids must be unique")
    texts = Counter(q.text.casefold() for q in queries)
    repeated = sorted(t for t, n in texts.items() if n > 1)
    if repeated:
        raise ContractError(f"query texts must be unique (case-insensitive): {repeated!r}")


def validate_partitions(
    queries: Sequence[QueryRecord], partitions: Sequence[PartitionAssignment]
) -> None:
    """Every query has exactly one assignment equal to its own partition, and no query seen in
    development (or with a pilot text) is in the test partition (D13)."""
    validate_queries(queries)
    by_id = {q.query_id: q for q in queries}
    assigned = {p.query_id: p.partition for p in partitions}
    if len(assigned) != len(partitions):
        raise ContractError("each query must have exactly one partition assignment")
    if set(assigned) != set(by_id):
        raise ContractError("partition assignments must cover exactly the query set")
    for query_id, partition in assigned.items():
        if by_id[query_id].partition is not partition:
            raise ContractError(f"{query_id}: assignment differs from the query record")
        if by_id[query_id].seen_in_development and partition.value == "test":
            raise ContractError(f"{query_id}: a query seen in development cannot be in test")


# ---- gold constraints and product intents ---------------------------------------------------


def _category_constraint(constraints: Iterable[GoldConstraint]) -> Category | None:
    for c in constraints:
        if c.field is GoldField.CATEGORY:
            return Category(str(c.value))
    return None


def validate_gold_constraints(
    queries: Sequence[QueryRecord],
    constraints: Sequence[GoldConstraint],
    catalog: CatalogIndex | None = None,
) -> None:
    query_ids = {q.query_id for q in queries}
    keys = Counter(c.sort_key() for c in constraints)
    duplicated = sorted(k for k, n in keys.items() if n > 1)
    if duplicated:
        raise ContractError(f"one constraint per (query, field, operator): {duplicated!r}")
    by_query: dict[str, list[GoldConstraint]] = defaultdict(list)
    for constraint in constraints:
        if constraint.query_id not in query_ids:
            raise ContractError(f"constraint for unknown query {constraint.query_id!r}")
        by_query[constraint.query_id].append(constraint)

    processors = catalog.processors() if catalog is not None else None
    for query_id, items in sorted(by_query.items()):
        category = _category_constraint(items)
        fields = {c.field: c for c in items}
        for c in items:
            allowed = FIELD_CATEGORIES.get(c.field)
            if category is not None and allowed is not None and category not in allowed:
                raise ContractError(
                    f"{query_id}: {c.field.value} does not apply to category {category.value}"
                )
            if (
                c.field is GoldField.SUBCATEGORY
                and category is not None
                and c.value not in SUBCATEGORIES[category]
            ):
                raise ContractError(
                    f"{query_id}: subcategory {c.value!r} is not a {category.value}"
                )
            if (
                processors is not None
                and c.field is GoldField.PROCESSOR
                and c.value not in processors
            ):
                raise ContractError(f"{query_id}: processor {c.value!r} is not in the catalog")
        interface = fields.get(GoldField.STORAGE_INTERFACE)
        medium = fields.get(GoldField.STORAGE_TYPE)
        nvme = interface is not None and interface.value == "NVME"
        if nvme and medium is not None and medium.value != "SSD":
            raise ContractError(f"{query_id}: an NVME interface requires storage_type SSD")
        bounds = {c.operator: c.price_value() for c in items if c.field is GoldField.PRICE}
        low, high = bounds.get(FilterOperator.GTE), bounds.get(FilterOperator.LTE)
        if low is not None and high is not None and low > high:
            raise ContractError(f"{query_id}: the minimum price exceeds the maximum price")


def validate_product_intents(
    queries: Sequence[QueryRecord],
    intents: Sequence[ProductIntent],
    catalog: CatalogIndex | None = None,
) -> None:
    query_ids = {q.query_id for q in queries}
    keys = Counter(i.sort_key() for i in intents)
    if any(n > 1 for n in keys.values()):
        raise ContractError("product intents must be unique per (query, kind, value)")
    for intent in intents:
        if intent.query_id not in query_ids:
            raise ContractError(f"product intent for unknown query {intent.query_id!r}")
        if catalog is not None and not catalog.has_title_token(intent.value):
            raise ContractError(
                f"{intent.query_id}: {intent.value!r} is not a token of any catalog title"
            )


def validate_attestation(
    queries: Sequence[QueryRecord],
    constraints: Sequence[GoldConstraint],
    intents: Sequence[ProductIntent],
    attestation: AuthoringAttestation,
) -> None:
    """The attestation covers exactly this query set, was confirmed by the approved annotator,
    and every gold record carries that annotator and the one confirmation time."""
    if attestation.confirmed_by != SINGLE_ANNOTATOR_HANDLE:
        raise ContractError(
            f"expected confirmation by {SINGLE_ANNOTATOR_HANDLE!r}, "
            f"found {attestation.confirmed_by!r}"
        )
    if attestation.query_count != len(queries):
        raise ContractError("the attestation query_count differs from the query set")
    if any(q.partition is not attestation.partition for q in queries):
        raise ContractError("every query must be in the attested partition")
    for record in (*constraints, *intents):
        if record.annotator_handle != attestation.confirmed_by:
            raise ContractError(f"{record.query_id}: gold record from another annotator")
        if record.recorded_at != attestation.confirmed_at:
            raise ContractError(f"{record.query_id}: gold record time differs from confirmation")


def validate_query_extraction(
    queries: Sequence[QueryRecord],
    constraints: Sequence[GoldConstraint],
    intents: Sequence[ProductIntent],
    extraction: Sequence[QueryExtraction],
) -> None:
    """The extraction records are exactly the deterministic regeneration from qu-1/fp-1."""
    if list(extraction) != derive_query_extraction(queries, constraints, intents):
        raise ContractError("query extraction does not match its deterministic regeneration")


# ---- pool, pool cap and provenance ----------------------------------------------------------


def blind_order_key(query_id: str, product_id: str) -> str:
    return hashlib.sha256(f"{BLIND_ORDER_VERSION}|{query_id}|{product_id}".encode()).hexdigest()


@dataclass(frozen=True)
class PoolCapDetail:
    query_id: str
    pool_size: int
    union_by_system: tuple[tuple[str, int], ...]  # products with that system's rank <= depth
    by_inclusion: tuple[tuple[str, int], ...]


class PoolCapExceeded(ContractError):
    """At least one query's pool is larger than the agreed cap. Annotation export must stop; the
    pool is never trimmed automatically and the pool policy is never changed here."""

    def __init__(self, details: tuple[PoolCapDetail, ...], cap: int) -> None:
        self.details = details
        self.cap = cap
        lines = [
            f"{d.query_id}: {d.pool_size} > {cap}; by system {dict(d.union_by_system)}; "
            f"by inclusion {dict(d.by_inclusion)}"
            for d in details
        ]
        super().__init__("pool cap exceeded; annotation export refused:\n" + "\n".join(lines))


def check_pool_cap(
    pool: Sequence[PoolItem],
    provenance: Sequence[ProvenanceEntry] | None = None,
    cap: int = MAX_POOL_SIZE_PER_QUERY,
) -> None:
    sizes = Counter(item.query_id for item in pool)
    over = sorted(q for q, n in sizes.items() if n > cap)
    if not over:
        return
    details = []
    for query_id in over:
        entries = [e for e in provenance or () if e.query_id == query_id]
        systems = Counter(
            hit.system.value for e in entries for hit in e.sources if hit.rank <= POOL_DEPTH
        )
        reasons = Counter(e.added_by.value for e in entries)
        details.append(
            PoolCapDetail(
                query_id=query_id,
                pool_size=sizes[query_id],
                union_by_system=tuple((s.value, systems.get(s.value, 0)) for s in SourceSystem),
                by_inclusion=tuple(sorted(reasons.items())),
            )
        )
    raise PoolCapExceeded(tuple(details), cap)


def validate_pool(
    queries: Sequence[QueryRecord], pool: Sequence[PoolItem], catalog: CatalogIndex
) -> None:
    """Pool items reference known queries and catalog products with their current content
    hashes; positions follow the deterministic blind order; and every pool fits the cap."""
    query_ids = {q.query_id for q in queries}
    _unique(_pairs(pool), "pool items")
    by_query: dict[str, list[PoolItem]] = defaultdict(list)
    for item in pool:
        if item.query_id not in query_ids:
            raise ContractError(f"pool item for unknown query {item.query_id!r}")
        product = catalog.products.get(item.product_id)
        if product is None:
            raise ContractError(f"pool item for unknown product {item.product_id!r}")
        if product.content_sha256 != item.product_content_sha256:
            raise ContractError(f"{item.product_id}: product content hash differs from the catalog")
        by_query[item.query_id].append(item)
    for query_id, items in by_query.items():
        expected = sorted(items, key=lambda i: blind_order_key(query_id, i.product_id))
        positions = {i.product_id: i.blind_position for i in items}
        for position, item in enumerate(expected, start=1):
            if positions[item.product_id] != position:
                raise ContractError(
                    f"{query_id}: blind positions do not follow {BLIND_ORDER_VERSION}"
                )
    check_pool_cap(pool)


def validate_provenance(
    pool: Sequence[PoolItem],
    provenance: Sequence[ProvenanceEntry],
    constraints: Sequence[GoldConstraint],
    catalog: CatalogIndex,
) -> None:
    pool_pairs = _unique(_pairs(pool), "pool items")
    prov_pairs = _unique([(e.query_id, e.product_id) for e in provenance], "provenance entries")
    if pool_pairs != prov_pairs:
        raise ContractError("provenance must cover exactly the pool")
    hard_category = {
        c.query_id: Category(str(c.value))
        for c in constraints
        if c.field is GoldField.CATEGORY and c.hardness is Hardness.HARD
    }
    counts: dict[str, Counter[InclusionReason]] = defaultdict(Counter)
    for entry in provenance:
        counts[entry.query_id][entry.added_by] += 1
        product = catalog.products.get(entry.product_id)
        if product is None:
            raise ContractError(f"provenance entry for unknown product {entry.product_id!r}")
        category = product.category
        wanted = hard_category.get(entry.query_id)
        if entry.added_by is InclusionReason.NEGATIVE_SAME_CATEGORY and category is not wanted:
            raise ContractError(
                f"{entry.query_id}: same-category negative outside the gold category"
            )
        if (
            entry.added_by is InclusionReason.NEGATIVE_OTHER_CATEGORY
            and wanted is not None
            and category is wanted
        ):
            raise ContractError(
                f"{entry.query_id}: other-category negative inside the gold category"
            )
    for query_id, reasons in counts.items():
        if reasons[InclusionReason.ELIGIBILITY_COMPLETION] > ELIGIBILITY_COMPLETION_CAP:
            raise ContractError(f"{query_id}: more than {ELIGIBILITY_COMPLETION_CAP} completions")
        negatives = (
            reasons[InclusionReason.NEGATIVE_SAME_CATEGORY]
            + reasons[InclusionReason.NEGATIVE_OTHER_CATEGORY]
        )
        if negatives > NEGATIVES_PER_QUERY:
            raise ContractError(f"{query_id}: more than {NEGATIVES_PER_QUERY} negatives")
    check_pool_cap(pool, provenance)


# ---- eligibility ----------------------------------------------------------------------------


def validate_eligibility(
    pool: Sequence[PoolItem],
    constraints: Sequence[GoldConstraint],
    eligibility: Sequence[EligibilityRecord],
) -> None:
    """One record per pooled pair, checking exactly the query's hard gold constraints. Soft
    constraints and product intents never take part in eligibility."""
    if _unique(_pairs(eligibility), "eligibility records") != _unique(_pairs(pool), "pool items"):
        raise ContractError("eligibility must cover exactly the pool")
    hard: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for c in constraints:
        if c.hardness is Hardness.HARD:
            hard[c.query_id].add((c.field.value, c.operator.value))
    for record in eligibility:
        checked = {(c.field.value, c.operator.value) for c in record.checks}
        if checked != hard.get(record.query_id, set()):
            raise ContractError(
                f"{record.query_id}/{record.product_id}: checks differ from the hard constraints"
            )


# ---- judgements -----------------------------------------------------------------------------


def validate_judgements_against_eligibility(
    judgements: Sequence[Judgement], eligibility: Sequence[EligibilityRecord]
) -> None:
    """A hard-constraint violation never becomes `relevant`; `relevant` with an unknown hard
    attribute needs a written rationale."""
    status = {(e.query_id, e.product_id): e.hard_overall for e in eligibility}
    for j in judgements:
        pair = (j.query_id, j.product_id)
        if pair not in status:
            raise ContractError(f"{pair!r}: no eligibility record")
        if j.label is Label.RELEVANT and status[pair] is OverallStatus.VIOLATED:
            raise ContractError(
                f"{pair!r} ({j.round.value}): relevant but a hard constraint is violated"
            )
        if (
            j.label is Label.RELEVANT
            and status[pair] is OverallStatus.UNKNOWN
            and j.rationale is None
        ):
            raise ContractError(
                f"{pair!r} ({j.round.value}): relevant with an unknown hard attribute "
                "needs a rationale"
            )


def validate_single_annotator(*rounds: Sequence[Judgement]) -> str:
    """Every judgement in every round comes from the one approved human annotator (D5 final)."""
    handles = {j.annotator_handle for records in rounds for j in records}
    if len(handles) != 1:
        raise ContractError(f"expected exactly one annotator handle, found {sorted(handles)!r}")
    handle = handles.pop()
    if handle != SINGLE_ANNOTATOR_HANDLE:
        raise ContractError(
            f"expected the approved annotator {SINGLE_ANNOTATOR_HANDLE!r}, found {handle!r}"
        )
    return handle


def _round_only(judgements: Sequence[Judgement], round_: Round) -> dict[Pair, Judgement]:
    for j in judgements:
        if j.round is not round_:
            raise ContractError(f"{j.query_id}/{j.product_id}: expected round {round_.value}")
    pairs = _pairs(judgements)
    _unique(pairs, f"{round_.value} judgements")
    return dict(zip(pairs, judgements, strict=True))


def validate_primary_round(pool: Sequence[PoolItem], primaries: Sequence[Judgement]) -> None:
    """A sealable primary round judges every pooled pair exactly once and nothing else."""
    judged = set(_round_only(primaries, Round.PRIMARY))
    pooled = _unique(_pairs(pool), "pool items")
    if judged != pooled:
        missing, extra = sorted(pooled - judged), sorted(judged - pooled)
        raise ContractError(
            f"primary round incomplete or foreign: missing {missing[:5]!r}, extra {extra[:5]!r}"
        )
    validate_single_annotator(primaries)


def validate_primary_seal_time(primaries: Sequence[Judgement], primary_sealed_at: datetime) -> None:
    """The seal cannot predate the primary labels it seals, so the 72-hour delay is measured
    from a time at or after the last primary judgement."""
    if primaries and primary_sealed_at < max(j.annotated_at for j in primaries):
        raise ContractError("primary_sealed_at is earlier than the latest primary judgement")


def validate_consistency_round(
    primaries: Sequence[Judgement],
    sample: ConsistencySample,
    consistency: Sequence[Judgement],
    primary_seal_sha256: str,
    primary_sealed_at: datetime,
) -> None:
    """The sample is exactly the one regenerated from the sealed primary round; repeats come
    only from the sample, from the same annotator, no earlier than the 72-hour delay after a seal
    that does not predate the primary labels, and every sampled item is repeated exactly once."""
    if sample.primary_seal_sha256 != primary_seal_sha256:
        raise ContractError("the consistency sample is bound to a different primary seal")
    if sample.primary_sealed_at != primary_sealed_at:
        raise ContractError("the consistency sample records a different seal time")
    validate_primary_seal_time(primaries, primary_sealed_at)
    if build_consistency_sample(primaries, primary_seal_sha256, primary_sealed_at) != sample:
        raise ContractError("the consistency sample does not match its deterministic regeneration")
    repeats = _round_only(consistency, Round.CONSISTENCY)
    sampled = {(i.query_id, i.product_id) for i in sample.items}
    foreign = sorted(set(repeats) - sampled)
    if foreign:
        raise ContractError(f"consistency judgements outside the sample: {foreign[:5]!r}")
    earliest = primary_sealed_at + MIN_CONSISTENCY_DELAY
    for pair, j in sorted(repeats.items()):
        if j.annotated_at < earliest:
            raise ContractError(
                f"{pair!r}: consistency judgement before the {MIN_CONSISTENCY_DELAY} delay"
            )
    if consistency:
        validate_single_annotator(primaries, consistency)
    missing = sorted(sampled - set(repeats))
    if missing:
        raise ContractError(
            f"{len(missing)} sampled items are missing from the consistency round: {missing[:5]!r}"
        )


def validate_adjudication_round(
    primaries: Sequence[Judgement],
    consistency: Sequence[Judgement],
    queue: Sequence[QueueItem],
    adjudications: Sequence[Judgement],
    label_changes: Sequence[LabelChange],
    golden_version: int,
    *,
    primary_sealed_at: datetime,
) -> None:
    """Adjudications resolve only queued items or consistency disagreements, come from the one
    annotator, are made after the primary seal (and after the repeat they resolve), and every
    changed label has exactly one matching, equally ordered LabelChange."""
    validate_primary_seal_time(primaries, primary_sealed_at)
    primary = _round_only(primaries, Round.PRIMARY)
    decided = _round_only(adjudications, Round.ADJUDICATION)
    repeated = _round_only(consistency, Round.CONSISTENCY)
    queued = {(i.query_id, i.product_id): set(i.review_reasons) for i in queue}
    disagreed = consistency_disagreements(primaries, consistency)
    unjudged = sorted(set(decided) - set(primary))
    if unjudged:
        raise ContractError(f"adjudications with no primary judgement: {unjudged[:5]!r}")
    for pair, j in sorted(decided.items()):
        allowed = set(queued.get(pair, set()))
        if pair in disagreed:
            allowed.add(ReviewReason.CONSISTENCY_DISAGREEMENT)
        if not allowed:
            raise ContractError(f"{pair!r}: adjudicated but neither queued nor a disagreement")
        if not set(j.review_reasons) <= allowed:
            raise ContractError(
                f"{pair!r}: review reasons not supported by the queue or disagreement"
            )
        if j.annotated_at < primary_sealed_at:
            raise ContractError(f"{pair!r}: adjudicated before the primary seal")
        if pair in repeated and j.annotated_at < repeated[pair].annotated_at:
            raise ContractError(f"{pair!r}: adjudicated before the consistency judgement")
        if j.changes_label != (j.label is not primary[pair].label):
            raise ContractError(f"{pair!r}: changes_label does not match the primary label")

    changes = [c for c in label_changes if c.kind is LabelChangeKind.ADJUDICATION]
    by_pair = Counter((c.query_id, c.product_id) for c in changes)
    if any(n > 1 for n in by_pair.values()):
        raise ContractError("at most one adjudication label change per pair")
    changed = {pair for pair, j in decided.items() if j.changes_label}
    if set(by_pair) != changed:
        raise ContractError("every changed label needs exactly one adjudication LabelChange")
    for c in changes:
        pair = (c.query_id, c.product_id)
        if c.previous_label is not primary[pair].label or c.new_label is not decided[pair].label:
            raise ContractError(f"{pair!r}: LabelChange does not match primary → adjudicated")
        if c.changed_at < primary_sealed_at:
            raise ContractError(f"{pair!r}: LabelChange recorded before the primary seal")
        if c.changed_at < decided[pair].annotated_at:
            raise ContractError(f"{pair!r}: LabelChange recorded before its adjudication")
        if c.from_golden_version != golden_version:
            raise ContractError(f"{pair!r}: LabelChange golden version differs")
    handle = validate_single_annotator(primaries, adjudications)
    strangers = sorted({c.changed_by for c in changes} - {handle})
    if strangers:
        raise ContractError(
            f"expected exactly one annotator handle, found {[handle, *strangers]!r}"
        )


def validate_final_judgements(
    primaries: Sequence[Judgement],
    adjudications: Sequence[Judgement],
    finals: Sequence[FinalJudgement],
) -> None:
    if list(finals) != derive_final_judgements(primaries, adjudications):
        raise ContractError("final judgements do not match their derivation")


def _verified_records(manifest: Manifest, files: Mapping[Role, bytes], role: Role) -> list:
    """The records of `role`, read only from bytes that match the manifest entry."""
    entry = manifest.entry(role)
    data = files.get(role)
    if data is None:
        raise ContractError(f"golden version {manifest.golden_version}: no file for {role.value}")
    verify_entry(entry, data)
    return decode_role(role, data)


_FROZEN_RECORD_ROLES = (
    Role.PRIMARY_JUDGEMENTS,
    Role.CONSISTENCY_JUDGEMENTS,
    Role.ADJUDICATION_JUDGEMENTS,
    Role.LABEL_CHANGES,
)


def validate_labels_frozen_at(manifest: Manifest, files: Mapping[Role, bytes]) -> datetime:
    """A `labels_frozen` (or later) manifest's freeze time is no earlier than its primary seal or
    any primary, consistency or adjudication judgement or label change it includes (read only
    from bytes that match the manifest). Returns the verified freeze time."""
    frozen_at = manifest.labels_frozen_at
    if frozen_at is None:
        raise ContractError(f"stage {manifest.stage.value} has no labels_frozen_at")
    for role in _FROZEN_RECORD_ROLES:
        for record in _verified_records(manifest, files, role):
            at = record.changed_at if isinstance(record, LabelChange) else record.annotated_at
            if frozen_at < at:
                raise ContractError(
                    f"labels_frozen_at is earlier than a {role.value} record "
                    f"({record.query_id}/{record.product_id})"
                )
    return frozen_at


def validate_post_freeze_corrections(
    previous_terminal: Manifest,
    previous_files: Mapping[Role, bytes],
    next_chain: Sequence[Manifest],
    next_files: Mapping[Role, bytes],
) -> None:
    """A correction-only golden version N+1 against the frozen version N it supersedes.

    Semantics (Session B supports label corrections only):

    * the baseline is version N's frozen final judgements, read from bytes that match N's
      terminal manifest; nothing in version N+1 (its primary, consistency or adjudication files)
      can redefine that baseline;
    * the final (query_id, product_id) key set is identical in both versions;
    * every changed final label has exactly one POST_FREEZE_CORRECTION from N to N+1 by the
      approved annotator, naming the old and new final labels; an unchanged label has none;
    * replaying the corrections onto version N's finals reproduces version N+1's finals exactly:
      a corrected pair takes the correction's label, and every other final record is identical,
      so metadata-only changes (relation, rubric version, ...) are refused;
    * a correction is dated no earlier than version N's verified `labels_frozen_at` and no
      earlier than a version N+1 adjudication of the same pair, if there is one. A correction
      needs no new adjudication: the LabelChange is itself the human audit record.

    How version N+1's artifacts are materialized (referenced or copied) is a runner concern for
    Session C or later and does not relax these rules. A completely new annotation round is not
    a correction-only version; it needs a separately defined future workflow.
    """
    validate_chain(next_chain)
    validate_succession(previous_terminal, next_chain[0])
    current = next_chain[-1]
    if STAGE_ORDER.index(current.stage) < STAGE_ORDER.index(Stage.LABELS_FROZEN):
        raise ContractError("corrections are checked against a labels_frozen (or later) manifest")
    old_version, new_version = previous_terminal.golden_version, current.golden_version
    frozen_at = validate_labels_frozen_at(previous_terminal, previous_files)
    validate_labels_frozen_at(current, next_files)

    previous_finals = _verified_records(previous_terminal, previous_files, Role.FINAL_JUDGEMENTS)
    current_finals = _verified_records(current, next_files, Role.FINAL_JUDGEMENTS)
    baseline = {(f.query_id, f.product_id): f for f in previous_finals}
    successor = {(f.query_id, f.product_id): f for f in current_finals}
    added, deleted = sorted(set(successor) - set(baseline)), sorted(set(baseline) - set(successor))
    if added or deleted:
        raise ContractError(
            "a correction-only version must keep the final key set: "
            f"added {added[:5]!r}, deleted {deleted[:5]!r}"
        )
    previous = {pair: f.label for pair, f in baseline.items()}
    labels = {pair: f.label for pair, f in successor.items()}

    adjudicated = {
        (j.query_id, j.product_id): j.annotated_at
        for j in _verified_records(current, next_files, Role.ADJUDICATION_JUDGEMENTS)
    }
    corrections = [
        c
        for c in _verified_records(current, next_files, Role.LABEL_CHANGES)
        if c.kind is LabelChangeKind.POST_FREEZE_CORRECTION
    ]
    for c in corrections:
        pair = (c.query_id, c.product_id)
        if (c.from_golden_version, c.to_golden_version) != (old_version, new_version):
            raise ContractError(
                f"{pair!r}: a correction must go from golden version {old_version} to {new_version}"
            )
        if c.changed_by != SINGLE_ANNOTATOR_HANDLE:
            raise ContractError(
                f"{pair!r}: expected the approved annotator {SINGLE_ANNOTATOR_HANDLE!r}, "
                f"found {c.changed_by!r}"
            )
        if pair not in previous:
            raise ContractError(f"{pair!r}: not in the superseded version's final labels")
        if previous[pair] is labels[pair]:
            raise ContractError(f"{pair!r}: a correction for an unchanged label")
        if c.previous_label is not previous[pair]:
            raise ContractError(f"{pair!r}: previous_label differs from the superseded final label")
        if c.new_label is not labels[pair]:
            raise ContractError(f"{pair!r}: new_label differs from the new final label")
        if c.changed_at < frozen_at:
            raise ContractError(
                f"{pair!r}: correction predates labels_frozen_at of golden version {old_version}"
            )
        if pair in adjudicated and c.changed_at < adjudicated[pair]:
            raise ContractError(f"{pair!r}: correction recorded before its adjudication")
    counted = Counter((c.query_id, c.product_id) for c in corrections)
    if any(n > 1 for n in counted.values()):
        raise ContractError("at most one post-freeze correction per pair")
    uncorrected = sorted(
        p for p, label in labels.items() if previous[p] is not label and p not in counted
    )
    if uncorrected:
        raise ContractError(
            f"final labels changed without a post-freeze correction: {uncorrected[:5]!r}"
        )

    replayed = dict(previous)
    for c in corrections:
        replayed[(c.query_id, c.product_id)] = c.new_label
    unreproduced = sorted(
        pair
        for pair, record in successor.items()
        if replayed[pair] is not record.label or (pair not in counted and record != baseline[pair])
    )
    if unreproduced:
        raise ContractError(
            "the new final judgements cannot be reproduced by applying the corrections to the "
            f"previous finals (metadata-only changes are not supported): {unreproduced[:5]!r}"
        )
