"""Typed, frozen Golden Dataset records. One model per concept; concepts never share a file.

Every record is `extra="forbid"`, frozen, carries `schema_version`, and declares `sort_key()`,
the canonical file order (see `canonical`). Single-record checks live here; checks that need
other records or the catalog live in `validation`.

Human-recorded records carry a pseudonymous `annotator_handle` and a UTC timestamp. Generated
records (pool, provenance, eligibility, consistency sample, adjudication queue, final labels)
carry no wall-clock time, machine path or handle, so a regeneration from the same inputs can be
checked byte for byte; the consistency sample's only time is the seal time it is bound to.
"""

import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any, Literal, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    PlainSerializer,
    PlainValidator,
    StrictBool,
    StrictInt,
    StrictStr,
    model_validator,
)

from ecommerce_search.catalog.evidence import EvidenceError, validate_free_text
from ecommerce_search.catalog.taxonomy import (
    BRAND_CATEGORIES,
    SUBCATEGORIES,
    Category,
    Connectivity,
    Gender,
    StorageInterface,
    StorageType,
)
from ecommerce_search.filtering.policy import FilterOperator
from ecommerce_search.search.query import QueryError, normalize_query
from evals.contracts.constants import (
    ALLOWED_RELATIONS,
    ANNOTATOR_HANDLE_PATTERN,
    CONSISTENCY_ALLOCATION_METHOD,
    CONSISTENCY_SAMPLE_RATE_PERCENT,
    CONSISTENCY_SAMPLE_VERSION,
    CONTRACT_SCHEMA_VERSION,
    FREE_TEXT_MAX,
    LABEL_ORDER,
    POOL_DEPTH,
    QUERY_MAX_LENGTH,
    RUBRIC_VERSION_PATTERN,
    Label,
    Relation,
)

SHA256_PATTERN = r"^[0-9a-f]{64}$"
PRODUCT_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"
QUERY_ID_PATTERN = r"^gq-[pt][0-9]{2}$"
TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"
_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$")
_MACHINE_PATH_RE = re.compile(r"(?:\b[A-Za-z]:[\\/])|(?:\\\\)|(?:(?:^|\s)/(?:Users|home)/)")
_PRICE_RE = re.compile(r"^[1-9][0-9]{0,8}\.[0-9]{2}$")
_SCORE_RE = re.compile(r"^-?[0-9]+(?:\.[0-9]+)?(?:[eE][-+]?[0-9]+)?$")


# ---- primitive field types -----------------------------------------------------------------


def _parse_timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("timestamps must be timezone-aware UTC")
        return value.astimezone(UTC)
    if isinstance(value, str) and _TIMESTAMP_RE.fullmatch(value):
        try:
            return datetime.strptime(value, TIMESTAMP_FORMAT).replace(tzinfo=UTC)
        except ValueError:
            raise ValueError(f"invalid timestamp {value!r}") from None
    raise ValueError("timestamps must be UTC strings of the form YYYY-MM-DDTHH:MM:SS.ffffffZ")


def format_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).strftime(TIMESTAMP_FORMAT)


UtcTimestamp = Annotated[
    datetime,
    PlainValidator(_parse_timestamp),
    PlainSerializer(format_timestamp, return_type=str),
]


def _free_text(value: str) -> str:
    if value != value.strip() or not value:
        raise ValueError("free text must be non-empty with no leading or trailing whitespace")
    try:
        validate_free_text("free text", value)
    except EvidenceError as exc:
        raise ValueError(str(exc)) from None
    if _MACHINE_PATH_RE.search(value):
        raise ValueError("free text must not contain machine-specific paths")
    return value


FreeText = Annotated[StrictStr, Field(max_length=FREE_TEXT_MAX), AfterValidator(_free_text)]
Sha256 = Annotated[StrictStr, Field(pattern=SHA256_PATTERN)]
QueryId = Annotated[StrictStr, Field(pattern=QUERY_ID_PATTERN)]
ProductId = Annotated[StrictStr, Field(pattern=PRODUCT_ID_PATTERN)]
AnnotatorHandle = Annotated[StrictStr, Field(pattern=ANNOTATOR_HANDLE_PATTERN)]
RubricVersion = Annotated[StrictStr, Field(pattern=RUBRIC_VERSION_PATTERN)]
Position = Annotated[StrictInt, Field(ge=1)]
Count = Annotated[StrictInt, Field(ge=0)]


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _Record(_Frozen):
    schema_version: Literal[1] = CONTRACT_SCHEMA_VERSION


# ---- queries and partitions -----------------------------------------------------------------


class LanguageStyle(StrEnum):
    ENGLISH = "english"
    HINGLISH = "hinglish"
    NOISY = "noisy"


class IntentFamily(StrEnum):
    BROAD_CATEGORY = "broad_category"
    BRAND_CATEGORY = "brand_category"
    EXACT_PRODUCT = "exact_product"
    PRICE = "price"
    SPECIFICATION = "specification"
    COMBINED = "combined"
    SEMANTIC_INTENT = "semantic_intent"
    AMBIGUOUS = "ambiguous"
    ZERO_ELIGIBLE = "zero_eligible"
    OUT_OF_CATALOG = "out_of_catalog"


class ExpectedEligibility(StrEnum):
    """An authoring expectation only; it is never a label and never checked as one."""

    EXPECTED_NONEMPTY = "expected_nonempty"
    EXPECTED_EMPTY = "expected_empty"
    UNKNOWN = "unknown"


class Partition(StrEnum):
    PILOT_DEV = "pilot_dev"
    TEST = "test"


PARTITION_PREFIX: dict[Partition, str] = {Partition.PILOT_DEV: "gq-p", Partition.TEST: "gq-t"}


def _check_partition_prefix(query_id: str, partition: Partition) -> None:
    if not query_id.startswith(PARTITION_PREFIX[partition]):
        raise ValueError(
            f"query_id {query_id!r} does not match partition {partition.value!r} "
            f"(expected prefix {PARTITION_PREFIX[partition]!r})"
        )


class QueryRecord(_Record):
    query_id: QueryId
    text: StrictStr
    language_style: LanguageStyle
    intent_family: IntentFamily
    interpretations: tuple[FreeText, ...] = ()
    expected_eligibility: ExpectedEligibility
    seen_in_development: StrictBool
    partition: Partition
    notes: FreeText | None = None

    @model_validator(mode="after")
    def _check(self) -> Self:
        try:
            normalized = normalize_query(self.text, QUERY_MAX_LENGTH)
        except QueryError as exc:
            raise ValueError(f"query text is not acceptable: {exc}") from None
        if normalized != self.text:
            raise ValueError("query text must already be whitespace-normalized")
        _check_partition_prefix(self.query_id, self.partition)
        if self.seen_in_development and self.partition is Partition.TEST:
            raise ValueError("a query seen in development must not enter the test partition")
        if len(set(self.interpretations)) != len(self.interpretations):
            raise ValueError("interpretations must be unique")
        if self.intent_family is IntentFamily.AMBIGUOUS and len(self.interpretations) < 2:
            raise ValueError("an ambiguous query must list at least two interpretations")
        return self

    def sort_key(self) -> tuple[str, ...]:
        return (self.query_id,)


class PartitionAssignment(_Record):
    query_id: QueryId
    partition: Partition

    @model_validator(mode="after")
    def _check(self) -> Self:
        _check_partition_prefix(self.query_id, self.partition)
        return self

    def sort_key(self) -> tuple[str, ...]:
        return (self.query_id,)


# ---- gold constraints and product intents ---------------------------------------------------


class GoldField(StrEnum):
    CATEGORY = "category"
    BRAND = "brand"
    RAM_GB = "ram_gb"
    STORAGE_GB = "storage_gb"
    STORAGE_TYPE = "storage_type"
    STORAGE_INTERFACE = "storage_interface"
    PRICE = "price"
    PROCESSOR = "processor"
    SUBCATEGORY = "subcategory"
    GENDER = "gender"
    ANC = "anc"
    WIRELESS = "wireless"
    CONNECTIVITY = "connectivity"


# Gold fields that fp-1 can filter on (`price` covers fp-1's min_price and max_price).
FP1_GOLD_FIELDS: frozenset[GoldField] = frozenset(
    {
        GoldField.CATEGORY,
        GoldField.BRAND,
        GoldField.RAM_GB,
        GoldField.STORAGE_GB,
        GoldField.STORAGE_TYPE,
        GoldField.STORAGE_INTERFACE,
        GoldField.PRICE,
    }
)

# Categories a field can apply to; a missing key means every category.
FIELD_CATEGORIES: dict[GoldField, frozenset[Category]] = {
    GoldField.RAM_GB: frozenset({Category.LAPTOP, Category.PHONE}),
    GoldField.STORAGE_GB: frozenset({Category.LAPTOP, Category.PHONE}),
    GoldField.STORAGE_TYPE: frozenset({Category.LAPTOP}),
    GoldField.STORAGE_INTERFACE: frozenset({Category.LAPTOP}),
    GoldField.PROCESSOR: frozenset({Category.LAPTOP}),
    GoldField.GENDER: frozenset({Category.SHOES}),
    GoldField.ANC: frozenset({Category.HEADPHONES}),
    GoldField.WIRELESS: frozenset({Category.HEADPHONES}),
    GoldField.CONNECTIVITY: frozenset({Category.HEADPHONES}),
}

_ENUM_VALUES: dict[GoldField, frozenset[str]] = {
    GoldField.CATEGORY: frozenset(c.value for c in Category),
    GoldField.BRAND: frozenset(BRAND_CATEGORIES),
    GoldField.STORAGE_TYPE: frozenset(s.value for s in StorageType),
    GoldField.STORAGE_INTERFACE: frozenset(s.value for s in StorageInterface),
    GoldField.SUBCATEGORY: frozenset().union(*SUBCATEGORIES.values()),
    GoldField.GENDER: frozenset(g.value for g in Gender),
    GoldField.CONNECTIVITY: frozenset(c.value for c in Connectivity),
}
_INT_FIELDS = frozenset({GoldField.RAM_GB, GoldField.STORAGE_GB})
_BOOL_FIELDS = frozenset({GoldField.ANC, GoldField.WIRELESS})
_INT_MAX = 65536


class Hardness(StrEnum):
    HARD = "hard"
    SOFT = "soft"


GoldValue = StrictBool | StrictInt | StrictStr


def _check_gold_value(field: GoldField, value: GoldValue) -> None:
    if field in _BOOL_FIELDS:
        if type(value) is not bool:
            raise ValueError(f"{field.value} requires a boolean value")
    elif field in _INT_FIELDS:
        if type(value) is not int or not 1 <= value <= _INT_MAX:
            raise ValueError(f"{field.value} requires a positive integer number of GB")
    elif field is GoldField.PRICE:
        if type(value) is not str or not _PRICE_RE.fullmatch(value):
            raise ValueError(
                "price requires a positive decimal string with 2 places, e.g. 40000.00"
            )
    elif field is GoldField.PROCESSOR:
        if type(value) is not str or not value or value != value.strip() or len(value) > 100:
            raise ValueError("processor requires a non-empty, trimmed string")
    elif type(value) is not str or value not in _ENUM_VALUES[field]:
        allowed = ", ".join(sorted(_ENUM_VALUES[field]))
        raise ValueError(f"{field.value} value {value!r} is not allowed; allowed: {allowed}")


class GoldConstraint(_Record):
    """A human-authored constraint for one query, recorded before parser output or results are
    seen. `fp1_filterable` is validated against the field, never chosen freely (D7)."""

    query_id: QueryId
    field: GoldField
    operator: FilterOperator
    value: GoldValue
    hardness: Hardness
    fp1_filterable: StrictBool
    annotator_handle: AnnotatorHandle
    recorded_at: UtcTimestamp
    note: FreeText | None = None

    @model_validator(mode="after")
    def _check(self) -> Self:
        _check_gold_value(self.field, self.value)
        if self.field is GoldField.PRICE:
            if self.operator not in (FilterOperator.LTE, FilterOperator.GTE):
                raise ValueError("price constraints use the lte or gte operator")
        elif self.operator is not FilterOperator.EQ:
            raise ValueError(f"{self.field.value} constraints use the eq operator")
        if self.fp1_filterable != (self.field in FP1_GOLD_FIELDS):
            raise ValueError(
                f"fp1_filterable must be {self.field in FP1_GOLD_FIELDS} for {self.field.value}"
            )
        return self

    def price_value(self) -> Decimal:
        return Decimal(str(self.value))

    def sort_key(self) -> tuple[str, ...]:
        return (self.query_id, self.field.value, self.operator.value)


class ProductIntentKind(StrEnum):
    SERIES = "series"
    MODEL_CODE = "model_code"


class ProductIntent(_Record):
    """A soft series/model intent (for example `Lumen`). It never feeds eligibility (D14)."""

    query_id: QueryId
    kind: ProductIntentKind
    value: Annotated[StrictStr, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9-]{0,63}$")]
    evidence: Literal["title_token"] = "title_token"
    hardness: Literal["soft"] = "soft"
    annotator_handle: AnnotatorHandle
    recorded_at: UtcTimestamp

    def sort_key(self) -> tuple[str, ...]:
        return (self.query_id, self.kind.value, self.value.casefold())


# ---- candidate pool and its private provenance ----------------------------------------------


class PoolItem(_Record):
    """One (query, product) pair shown to the annotator. No source, rank, score or inclusion
    reason is stored here (D4); those live only in the sealed provenance."""

    query_id: QueryId
    product_id: ProductId
    product_content_sha256: Sha256
    blind_position: Position

    def sort_key(self) -> tuple[str, ...]:
        return (self.query_id, self.product_id)


class SourceSystem(StrEnum):
    V0_LEXICAL = "v0_lexical"
    DENSE_ONLY = "dense_only"
    V1_HYBRID = "v1_hybrid"
    V2_FILTERED = "v2_filtered"
    V3_RERANKED_D10 = "v3_reranked_d10"
    V3_RERANKED_D20 = "v3_reranked_d20"


SOURCE_ORDER: tuple[SourceSystem, ...] = tuple(SourceSystem)


class SourceHit(_Frozen):
    system: SourceSystem
    rank: Annotated[StrictInt, Field(ge=1, le=50)]
    score: Annotated[StrictStr, Field(pattern=_SCORE_RE.pattern)] | None = None


class InclusionReason(StrEnum):
    SYSTEM_UNION = "system_union"
    ELIGIBILITY_COMPLETION = "eligibility_completion"
    NEGATIVE_SAME_CATEGORY = "negative_same_category"
    NEGATIVE_OTHER_CATEGORY = "negative_other_category"


class ProvenanceEntry(_Record):
    """Why a pooled product is in the pool. Sealed (checksum only) until labels freeze (D12)."""

    query_id: QueryId
    product_id: ProductId
    added_by: InclusionReason
    sources: tuple[SourceHit, ...] = ()

    @model_validator(mode="after")
    def _check(self) -> Self:
        systems = [hit.system for hit in self.sources]
        if systems != sorted(systems, key=SOURCE_ORDER.index) or len(set(systems)) != len(systems):
            raise ValueError("sources must be unique and in the fixed system order")
        within_depth = any(hit.rank <= POOL_DEPTH for hit in self.sources)
        if self.added_by is InclusionReason.SYSTEM_UNION and not within_depth:
            raise ValueError(f"a system_union item needs a source rank <= {POOL_DEPTH}")
        if self.added_by is not InclusionReason.SYSTEM_UNION and within_depth:
            raise ValueError(
                f"an item with a source rank <= {POOL_DEPTH} must be added_by system_union"
            )
        return self

    def sort_key(self) -> tuple[str, ...]:
        return (self.query_id, self.product_id)


# ---- derived constraint eligibility ---------------------------------------------------------


class ConstraintStatus(StrEnum):
    SATISFIED = "satisfied"
    VIOLATED = "violated"
    UNKNOWN = "unknown"  # the product's attribute value is missing
    NOT_APPLICABLE = "not_applicable"  # the attribute does not exist for the product's category


class OverallStatus(StrEnum):
    SATISFIED = "satisfied"
    VIOLATED = "violated"
    UNKNOWN = "unknown"
    NO_CONSTRAINTS = "no_constraints"


def overall_status(statuses: list[ConstraintStatus]) -> OverallStatus:
    """A hard constraint the product cannot have (`not_applicable`) is not satisfied, like an
    fp-1 spec filter on a missing spec row; it counts as a violation."""
    if not statuses:
        return OverallStatus.NO_CONSTRAINTS
    if any(s in (ConstraintStatus.VIOLATED, ConstraintStatus.NOT_APPLICABLE) for s in statuses):
        return OverallStatus.VIOLATED
    if any(s is ConstraintStatus.UNKNOWN for s in statuses):
        return OverallStatus.UNKNOWN
    return OverallStatus.SATISFIED


class ConstraintCheck(_Frozen):
    field: GoldField
    operator: FilterOperator
    fp1_filterable: StrictBool
    status: ConstraintStatus


class EligibilityRecord(_Record):
    """Derived deterministically from hard gold constraints and the normalized catalog. It is
    never a human label and is reported separately from relevance (D7)."""

    query_id: QueryId
    product_id: ProductId
    checks: tuple[ConstraintCheck, ...]
    hard_overall: OverallStatus
    fp1_overall: OverallStatus

    @model_validator(mode="after")
    def _check(self) -> Self:
        keys = [(c.field.value, c.operator.value) for c in self.checks]
        if keys != sorted(set(keys)):
            raise ValueError("checks must be unique and sorted by (field, operator)")
        for check in self.checks:
            if check.fp1_filterable != (check.field in FP1_GOLD_FIELDS):
                raise ValueError(f"fp1_filterable is wrong for {check.field.value}")
        if self.hard_overall is not overall_status([c.status for c in self.checks]):
            raise ValueError("hard_overall does not follow from the checks")
        fp1 = [c.status for c in self.checks if c.fp1_filterable]
        if self.fp1_overall is not overall_status(fp1):
            raise ValueError("fp1_overall does not follow from the fp-1 checks")
        return self

    def sort_key(self) -> tuple[str, ...]:
        return (self.query_id, self.product_id)


# ---- human judgements -----------------------------------------------------------------------


class Round(StrEnum):
    """Single-annotator workflow (D5 final). There is deliberately no second-reviewer round."""

    PRIMARY = "primary"
    CONSISTENCY = "consistency"
    ADJUDICATION = "adjudication"


class Confidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ReviewReason(StrEnum):
    LOW_CONFIDENCE = "low_confidence"
    NEEDS_ADJUDICATION = "needs_adjudication"
    AMBIGUOUS_QUERY = "ambiguous_query"
    BORDERLINE = "borderline"
    HARD_CONSTRAINT_UNKNOWN = "hard_constraint_unknown"
    CATALOG_ISSUE = "catalog_issue"
    CONSISTENCY_DISAGREEMENT = "consistency_disagreement"


def _check_relation(label: Label, relation: Relation) -> None:
    if relation not in ALLOWED_RELATIONS[label]:
        allowed = ", ".join(sorted(r.value for r in ALLOWED_RELATIONS[label]))
        raise ValueError(
            f"relation {relation.value!r} is not allowed for {label.value!r}: {allowed}"
        )


class Judgement(_Record):
    """One human relevance judgement of one pooled pair in one round. Records are human-only by
    contract: there is no field for an AI or model label source, and labels are never prefilled
    (an unjudged pair has no record)."""

    query_id: QueryId
    product_id: ProductId
    round: Round
    label: Label
    relation: Relation
    confidence: Confidence
    rationale: FreeText | None = None
    needs_adjudication: StrictBool = False
    catalog_issue: StrictBool = False
    annotator_handle: AnnotatorHandle
    annotated_at: UtcTimestamp
    rubric_version: RubricVersion
    review_reasons: tuple[ReviewReason, ...] = ()
    changes_label: StrictBool | None = None
    adjudication_reason: FreeText | None = None

    @model_validator(mode="after")
    def _check(self) -> Self:
        _check_relation(self.label, self.relation)
        if self.rationale is None and (
            self.label is Label.BORDERLINE or self.confidence is Confidence.LOW
        ):
            raise ValueError("a rationale is required for borderline or low-confidence labels")
        reasons = list(self.review_reasons)
        if self.round is Round.ADJUDICATION:
            if not reasons:
                raise ValueError("an adjudication needs at least one review reason")
            if reasons != sorted(set(reasons)):
                raise ValueError("review_reasons must be unique and sorted")
            if self.changes_label is None:
                raise ValueError("an adjudication must state changes_label")
            if self.changes_label and self.adjudication_reason is None:
                raise ValueError("a label change needs an adjudication_reason")
            if self.needs_adjudication:
                raise ValueError("an adjudication cannot itself need adjudication")
        elif reasons or self.changes_label is not None or self.adjudication_reason is not None:
            raise ValueError("review fields are only allowed in the adjudication round")
        return self

    def sort_key(self) -> tuple[str, ...]:
        return (self.query_id, self.product_id)


class LabelChangeKind(StrEnum):
    ADJUDICATION = "adjudication"
    POST_FREEZE_CORRECTION = "post_freeze_correction"


class LabelChange(_Record):
    """Append-only record of a changed label. Labels are never edited in place."""

    query_id: QueryId
    product_id: ProductId
    kind: LabelChangeKind
    previous_label: Label
    new_label: Label
    reason: FreeText
    changed_by: AnnotatorHandle
    changed_at: UtcTimestamp
    from_golden_version: Annotated[StrictInt, Field(ge=1)]
    to_golden_version: Annotated[StrictInt, Field(ge=1)]

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.previous_label is self.new_label:
            raise ValueError("a label change must change the label")
        if self.kind is LabelChangeKind.ADJUDICATION:
            if self.to_golden_version != self.from_golden_version:
                raise ValueError("an adjudication change stays within one golden version")
        elif self.to_golden_version != self.from_golden_version + 1:
            raise ValueError("a post-freeze correction creates the next golden version")
        return self

    def sort_key(self) -> tuple[str, ...]:
        return (
            self.query_id,
            self.product_id,
            f"{self.from_golden_version:06d}",
            f"{self.to_golden_version:06d}",
            self.kind.value,
        )


# ---- generated review sets and final labels -------------------------------------------------


class AllocationCell(_Frozen):
    label: Label
    query_id: QueryId
    count: Annotated[StrictInt, Field(ge=1)]


class SampleItem(_Frozen):
    query_id: QueryId
    product_id: ProductId
    blind_position: Position


def consistency_target(population: int) -> int:
    """Exactly ceil(rate × population), with integer arithmetic."""
    return (population * CONSISTENCY_SAMPLE_RATE_PERCENT + 99) // 100


class ConsistencySample(_Record):
    """The statistical intra-annotator consistency sample: exactly ceil(20% × N) primary
    judgements, drawn after the primary round is sealed. It never contains previous labels."""

    sample_version: Literal["cs-1"] = CONSISTENCY_SAMPLE_VERSION
    allocation_method: Literal["rr-label-query-1"] = CONSISTENCY_ALLOCATION_METHOD
    rate_percent: Literal[20] = CONSISTENCY_SAMPLE_RATE_PERCENT
    primary_seal_sha256: Sha256
    primary_sealed_at: UtcTimestamp
    population_count: Count
    target_count: Count
    actual_count: Count
    allocation: tuple[AllocationCell, ...]
    items: tuple[SampleItem, ...]

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.target_count != consistency_target(self.population_count):
            raise ValueError("target_count must be ceil(rate × population_count)")
        if self.actual_count != self.target_count or len(self.items) != self.actual_count:
            raise ValueError("the sample must contain exactly target_count items")
        if sum(cell.count for cell in self.allocation) != self.actual_count:
            raise ValueError("the allocation must add up to actual_count")
        cells = [(LABEL_ORDER.index(c.label), c.query_id) for c in self.allocation]
        if cells != sorted(set(cells)):
            raise ValueError("allocation cells must be unique and sorted by (label, query)")
        pairs = [(i.query_id, i.product_id) for i in self.items]
        if len(set(pairs)) != len(pairs):
            raise ValueError("sample items must be unique")
        if [i.blind_position for i in self.items] != list(range(1, len(self.items) + 1)):
            raise ValueError("items must be listed in blind order with positions 1..n")
        return self

    def sort_key(self) -> tuple[str, ...]:
        return (self.primary_seal_sha256,)


class QueueItem(_Record):
    """A targeted adjudication item. Kept separate from the consistency sample: membership here
    never changes the consistency sample's population, target or denominator."""

    query_id: QueryId
    product_id: ProductId
    selection: Literal["targeted_review"] = "targeted_review"
    review_reasons: tuple[ReviewReason, ...]

    @model_validator(mode="after")
    def _check(self) -> Self:
        reasons = list(self.review_reasons)
        if not reasons or reasons != sorted(set(reasons)):
            raise ValueError("review_reasons must be non-empty, unique and sorted")
        if ReviewReason.CONSISTENCY_DISAGREEMENT in reasons:
            raise ValueError("consistency disagreements are not known when the queue is built")
        return self

    def sort_key(self) -> tuple[str, ...]:
        return (self.query_id, self.product_id)


class FinalJudgement(_Record):
    """The frozen label of a pair: the adjudicated label when one exists, else the primary."""

    query_id: QueryId
    product_id: ProductId
    label: Label
    relation: Relation
    decided_in: Literal["primary", "adjudication"]
    rubric_version: RubricVersion

    @model_validator(mode="after")
    def _check(self) -> Self:
        _check_relation(self.label, self.relation)
        return self

    def sort_key(self) -> tuple[str, ...]:
        return (self.query_id, self.product_id)
