"""Typed, frozen Golden Dataset records. One model per concept; concepts never share a file.

Every record is `extra="forbid"`, frozen, carries `schema_version`, and declares `sort_key()`,
the canonical file order (see `canonical`). Single-record checks live here; checks that need
other records or the catalog live in `validation`.

Human-recorded records carry a pseudonymous `annotator_handle` and a UTC timestamp. Generated
records (pool, provenance, eligibility, consistency sample, adjudication queue, final labels)
carry no wall-clock time, machine path or handle, so a regeneration from the same inputs can be
checked byte for byte; the consistency sample's only time is the seal time it is bound to.
"""

import hashlib
import json
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
from ecommerce_search.query_understanding.models import Intent
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
    # Soft-only signals (never hard, never fp-1, never part of eligibility). Their vocabularies
    # are qu-1's closed sets, so a later analysis can compare them with what qu-1 extracts.
    SEMANTIC_INTENT = "semantic_intent"
    PRICE_PREFERENCE = "price_preference"


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

# Fields that may only be recorded as soft signals.
SOFT_ONLY_FIELDS: frozenset[GoldField] = frozenset(
    {GoldField.SEMANTIC_INTENT, GoldField.PRICE_PREFERENCE}
)
# qu-1's price-preference vocabulary (`QueryAttributes.price_preference`; `sasta` -> `low`).
PRICE_PREFERENCE_VALUES: frozenset[str] = frozenset({"low"})

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
    GoldField.SEMANTIC_INTENT: frozenset(i.value for i in Intent),
    GoldField.PRICE_PREFERENCE: PRICE_PREFERENCE_VALUES,
}
_INT_FIELDS = frozenset({GoldField.RAM_GB, GoldField.STORAGE_GB})
_BOOL_FIELDS = frozenset({GoldField.ANC, GoldField.WIRELESS})
_INT_MAX = 65536


class Hardness(StrEnum):
    HARD = "hard"
    SOFT = "soft"


GoldValue = StrictBool | StrictInt | StrictStr


def canonical_gold_value(value: GoldValue) -> str:
    """The canonical evidence string of a gold value ("true", "16", "40000.00", "NVME")."""
    if type(value) is bool:
        return "true" if value else "false"
    return str(value)


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
        if self.field in SOFT_ONLY_FIELDS and self.hardness is not Hardness.SOFT:
            raise ValueError(f"{self.field.value} can only be a soft signal")
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
    """A soft exact series/model intent (for example `Lumen`), separate from semantic intent. It
    never feeds eligibility (D14): same-series products are never hard-eligible because of it."""

    query_id: QueryId
    kind: ProductIntentKind
    value: Annotated[StrictStr, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9-]{0,63}$")]
    evidence: Literal["title_token"] = "title_token"
    hardness: Literal["soft"] = "soft"
    annotator_handle: AnnotatorHandle
    recorded_at: UtcTimestamp

    def sort_key(self) -> tuple[str, ...]:
        return (self.query_id, self.kind.value, self.value.casefold())


# ---- what qu-1 extracts from each pilot query (generated) ----------------------------------


class ExtractionTarget(StrEnum):
    GOLD_CONSTRAINT = "gold_constraint"
    PRODUCT_INTENT = "product_intent"


class ExtractionStatus(StrEnum):
    """What qu-1 returned for one gold item of one exact query. This is separate from
    `fp1_filterable`, which only says whether fp-1 could enforce the field once supplied."""

    EXTRACTED = "extracted"  # qu-1 returned the gold value
    EXTRACTED_OTHER_VALUE = "extracted_other_value"  # qu-1 returned a different value
    NOT_EXTRACTED = "not_extracted"  # qu-1 has the field but returned nothing for it
    NO_PARSER_FIELD = "no_parser_field"  # qu-1 has no such field at all


class QueryExtraction(_Record):
    """Generated from the gold records and the deterministic qu-1/fp-1 code. It records parser
    behaviour only: it is never a gold constraint, an eligibility status or a relevance label."""

    query_id: QueryId
    target: ExtractionTarget
    field: StrictStr
    operator: FilterOperator | None
    gold_value: Annotated[StrictStr, Field(min_length=1)]
    parser_version: Literal["qu-1"] = "qu-1"
    lexicon_version: Literal["1"] = "1"
    filter_policy_version: Literal["fp-1"] = "fp-1"
    status: ExtractionStatus
    parsed_value: StrictStr | None
    fp1_filterable: StrictBool
    fp1_applied: StrictBool

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.target is ExtractionTarget.GOLD_CONSTRAINT:
            if self.field not in {f.value for f in GoldField} or self.operator is None:
                raise ValueError("a gold_constraint extraction names a gold field and operator")
            if self.fp1_filterable != (GoldField(self.field) in FP1_GOLD_FIELDS):
                raise ValueError(f"fp1_filterable is wrong for {self.field}")
        else:
            if self.field not in {k.value for k in ProductIntentKind} or self.operator is not None:
                raise ValueError("a product_intent extraction names an intent kind, no operator")
            if self.status is not ExtractionStatus.NO_PARSER_FIELD or self.fp1_filterable:
                raise ValueError("qu-1 and fp-1 have no product-intent field")
        found = self.status in (ExtractionStatus.EXTRACTED, ExtractionStatus.EXTRACTED_OTHER_VALUE)
        if found != (self.parsed_value is not None):
            raise ValueError("parsed_value is set exactly when qu-1 extracted a value")
        if self.status is ExtractionStatus.EXTRACTED and self.parsed_value != self.gold_value:
            raise ValueError("an extracted value must equal the gold value")
        if (
            self.status is ExtractionStatus.EXTRACTED_OTHER_VALUE
            and self.parsed_value == self.gold_value
        ):
            raise ValueError("extracted_other_value needs a value different from the gold value")
        if self.fp1_applied and not (self.fp1_filterable and found):
            raise ValueError("fp1_applied needs an fp-1 field that qu-1 extracted")
        return self

    def sort_key(self) -> tuple[str, ...]:
        operator = "" if self.operator is None else self.operator.value
        return (self.query_id, self.target.value, self.field, operator)


# ---- authoring attestation (recorded) -------------------------------------------------------


class DraftedBy(StrEnum):
    AI_ASSISTANT = "ai_assistant"
    HUMAN = "human"


class AuthoringPurpose(StrEnum):
    DEVELOPMENT_RUBRIC_CALIBRATION = "development_rubric_calibration"
    HELD_OUT_TEST = "held_out_test"


class AuthoringAttestation(_Record):
    """How a query set and its gold records were authored, and who confirmed them. Written only
    by the human-run freeze command; the confirmation time is the gold records' `recorded_at`."""

    partition: Partition
    purpose: AuthoringPurpose
    drafted_by: DraftedBy
    confirmed_by: AnnotatorHandle
    confirmed_at: UtcTimestamp
    blind_to_parser_output: StrictBool
    final_test_evidence: StrictBool
    input_sha256: Sha256
    query_count: Annotated[StrictInt, Field(ge=1)]

    @model_validator(mode="after")
    def _check(self) -> Self:
        dev = self.purpose is AuthoringPurpose.DEVELOPMENT_RUBRIC_CALIBRATION
        if dev != (self.partition is Partition.PILOT_DEV):
            raise ValueError("development purpose belongs to pilot_dev, held-out test to test")
        if dev and self.final_test_evidence:
            raise ValueError("development/rubric-calibration data is never final test evidence")
        return self

    def sort_key(self) -> tuple[str, ...]:
        return (self.input_sha256,)


# ---- candidate pool and its private provenance ----------------------------------------------


class PoolItem(_Record):
    """One (query, product) pair shown to the annotator. No source, rank, score or inclusion
    reason is stored here (D4); those live only in the sealed provenance.

    `product_content_sha256` is the catalog's normalized record hash and `card_sha256` the hash
    of the exact `card-1` projection shown to the annotator (`evals.contracts.cards`)."""

    query_id: QueryId
    product_id: ProductId
    product_content_sha256: Sha256
    card_sha256: Sha256
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


class NamedValue(_Frozen):
    """One named run fact (a setting, a model pin, a library version, a tree hash)."""

    name: Annotated[StrictStr, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_./:-]{0,127}$")]
    value: Annotated[StrictStr, Field(max_length=200)]


def _check_named(values: tuple[NamedValue, ...], what: str) -> None:
    names = [v.name for v in values]
    if names != sorted(set(names)):
        raise ValueError(f"{what} must have unique names, sorted by name")


class RunContext(_Frozen):
    """Everything a pool run depends on. It carries no wall-clock time, machine path, database
    name or random identifier, so two independent runs of the same inputs have the same
    context and therefore the same `run_id`."""

    queries_manifest_sha256: Sha256
    dataset_id: Annotated[StrictStr, Field(min_length=1)]
    dataset_version: Annotated[StrictStr, Field(min_length=1)]
    dataset_checksum_sha256: Sha256
    pool_policy: Annotated[StrictStr, Field(min_length=1)]
    blind_order: Annotated[StrictStr, Field(min_length=1)]
    source_top_k: Annotated[StrictInt, Field(ge=1, le=50)]
    settings: tuple[NamedValue, ...]
    model_pins: tuple[NamedValue, ...]
    component_versions: tuple[NamedValue, ...]
    library_versions: tuple[NamedValue, ...]
    runtime_trees: tuple[NamedValue, ...]

    @model_validator(mode="after")
    def _check(self) -> Self:
        for name in (
            "settings",
            "model_pins",
            "component_versions",
            "library_versions",
            "runtime_trees",
        ):
            _check_named(getattr(self, name), name)
        return self


class RerankerMode(_Frozen):
    enabled: StrictBool
    depth: Annotated[StrictInt, Field(ge=1, le=50)]
    status: Annotated[StrictStr, Field(pattern=r"^[a-z_]{1,64}$")]
    reranked_count: Count


class SourceRun(_Frozen):
    """One source's response to one query: its ranked product ids and how it was produced.
    Private (part of the sealed provenance), never shown to the annotator."""

    query_id: QueryId
    system: SourceSystem
    endpoint: Annotated[StrictStr, Field(pattern=r"^/search(?:/[a-z]+)?$")]
    search_version: Annotated[StrictStr, Field(pattern=r"^[a-z0-9_]{1,64}$")]
    top_k: Annotated[StrictInt, Field(ge=1, le=50)]
    product_ids: tuple[ProductId, ...]
    applied_filters: tuple[Annotated[StrictStr, Field(max_length=500)], ...] = ()
    ignored_constraints: tuple[Annotated[StrictStr, Field(max_length=500)], ...] = ()
    parser_version: StrictStr | None = None
    filter_policy_version: StrictStr | None = None
    dense_status: StrictStr | None = None
    reranker: RerankerMode | None = None

    @model_validator(mode="after")
    def _check(self) -> Self:
        if len(self.product_ids) > self.top_k:
            raise ValueError("a source returned more products than its top_k")
        if len(set(self.product_ids)) != len(self.product_ids):
            raise ValueError("a source returned a product twice")
        is_v3 = self.system in (SourceSystem.V3_RERANKED_D10, SourceSystem.V3_RERANKED_D20)
        if is_v3 != (self.reranker is not None):
            raise ValueError("exactly the V3 sources carry a reranker mode")
        return self

    def sort_key(self) -> tuple[str, ...]:
        return (self.query_id, f"{SOURCE_ORDER.index(self.system):02d}")


def run_id_for(context: RunContext) -> str:
    """The deterministic run identity: the first 16 hex digits of the context's SHA-256."""
    body = context.model_dump(mode="json")
    text = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


class PoolProvenance(_Record):
    """The private, sealed pool provenance (D12): the run context, every source's ranked
    response, and why each pooled product is in the pool. Only its file SHA-256 is bound
    (`provenance_binding`) until labels freeze; it becomes a manifest entry only at
    `provenance_unsealed`."""

    provenance_version: Literal["prov-1"] = "prov-1"
    run_id: Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{16}$")]
    run_context: RunContext
    source_runs: tuple[SourceRun, ...]
    entries: tuple[ProvenanceEntry, ...]

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.run_id != run_id_for(self.run_context):
            raise ValueError("run_id does not match the run context")
        run_keys = [r.sort_key() for r in self.source_runs]
        if run_keys != sorted(set(run_keys)):
            raise ValueError("source runs must be unique and sorted by (query, system)")
        entry_keys = [e.sort_key() for e in self.entries]
        if entry_keys != sorted(set(entry_keys)):
            raise ValueError("entries must be unique and sorted by (query, product)")
        expected: dict[tuple[str, str], list[tuple[SourceSystem, int]]] = {}
        for run in self.source_runs:
            if run.top_k != self.run_context.source_top_k:
                raise ValueError(f"{run.query_id}/{run.system.value}: unexpected top_k")
            for rank, product_id in enumerate(run.product_ids, start=1):
                expected.setdefault((run.query_id, product_id), []).append((run.system, rank))
        for entry in self.entries:
            hits = [(h.system, h.rank) for h in entry.sources]
            if hits != expected.pop((entry.query_id, entry.product_id), []):
                raise ValueError(
                    f"{entry.query_id}/{entry.product_id}: sources differ from the source runs"
                )
        if expected:
            raise ValueError("a source hit has no provenance entry")
        return self

    def sort_key(self) -> tuple[str, ...]:
        return (self.run_id,)


class CompletionStatus(StrEnum):
    """Why eligibility completion did or did not add products to a query's pool."""

    APPLIED = "applied"  # 1..cap products satisfy every hard constraint; the missing were added
    NO_HARD_CONSTRAINTS = "no_hard_constraints"
    ZERO_ELIGIBLE = "zero_eligible"
    OVER_COMPLETION_CAP = "over_completion_cap"  # never truncated: nothing is added


class SystemCount(_Frozen):
    system: SourceSystem
    count: Count


class QueryPoolSummary(_Frozen):
    """Aggregate counts for one query's pool. No product id, rank, score or per-product
    inclusion reason appears here."""

    query_id: QueryId
    hard_constraint_count: Count
    eligible_count: Count | None  # None when the query has no hard constraint
    unknown_count: Count | None
    completion: CompletionStatus
    completion_added: Count
    source_result_counts: tuple[SystemCount, ...]
    union_size: Count
    negatives_same_category: Count
    negatives_other_category: Count
    negative_shortfall: Count
    pool_size: Count
    within_cap: StrictBool

    @model_validator(mode="after")
    def _check(self) -> Self:
        if [c.system for c in self.source_result_counts] != list(SOURCE_ORDER):
            raise ValueError("source_result_counts list every system once, in system order")
        negatives = self.negatives_same_category + self.negatives_other_category
        if self.pool_size != self.union_size + self.completion_added + negatives:
            raise ValueError("pool_size must equal union + completion + negatives")
        if (self.eligible_count is None) != (self.hard_constraint_count == 0):
            raise ValueError("eligible_count is null exactly when there is no hard constraint")
        if self.completion_added > 0 and self.completion is not CompletionStatus.APPLIED:
            raise ValueError("only an applied completion adds products")
        return self


class PoolReport(_Record):
    """The deterministic, public aggregate report of a frozen pool (role `pool_report`)."""

    report_version: Literal["pool-report-1"] = "pool-report-1"
    pool_policy: Annotated[StrictStr, Field(min_length=1)]
    source_top_k: Annotated[StrictInt, Field(ge=1, le=50)]
    max_pool_size: Annotated[StrictInt, Field(ge=1)]
    completion_cap: Annotated[StrictInt, Field(ge=0)]
    negatives_per_query: Annotated[StrictInt, Field(ge=0)]
    queries: tuple[QueryPoolSummary, ...]
    total_pool_items: Count
    all_within_cap: StrictBool

    @model_validator(mode="after")
    def _check(self) -> Self:
        ids = [q.query_id for q in self.queries]
        if ids != sorted(set(ids)):
            raise ValueError("query summaries must be unique and sorted by query_id")
        if self.total_pool_items != sum(q.pool_size for q in self.queries):
            raise ValueError("total_pool_items must be the sum of the pool sizes")
        if self.all_within_cap != all(q.within_cap for q in self.queries):
            raise ValueError("all_within_cap must follow from the per-query flags")
        for q in self.queries:
            if q.within_cap != (q.pool_size <= self.max_pool_size):
                raise ValueError(f"{q.query_id}: within_cap does not match the cap")
            found = q.negatives_same_category + q.negatives_other_category
            if found + q.negative_shortfall != self.negatives_per_query:
                raise ValueError(f"{q.query_id}: negatives and shortfall must add up")
            if q.completion_added > self.completion_cap:
                raise ValueError(f"{q.query_id}: completion exceeds its cap")
        return self

    def sort_key(self) -> tuple[str, ...]:
        return (self.report_version,)


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
