"""The approved 20-query development/rubric-calibration pilot (M9 decisions D6, D13, D14 and the
Session C1 corrections) and its validators.

The pilot is development data only: it is never final held-out test evidence, and none of its
queries may enter the sealed test partition. Its query texts and gold records were drafted by
an AI assistant, reviewed and confirmed by the single human annotator `ann-1`, and were not
authored blind to the Session A qu-1/fp-1 inspection; the authoring attestation records this.

`APPROVED_PILOT` fixes the approved query identities and metadata. Gold constraints and product
intents are human-confirmed records validated by the generic validators plus the query-specific
rules in `validate_pilot`; they are not duplicated here.

The input file (`PilotInput`) holds the AI draft without any annotator handle or timestamp. Only
the human-run freeze command turns it into recorded artifacts, stamping `ann-1` and the
confirmation time.
"""

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictStr

from ecommerce_search.filtering.policy import FilterOperator
from evals.contracts.canonical import ContractError
from evals.contracts.catalog import CatalogIndex
from evals.contracts.constants import GOLDEN_ID, RUBRIC_VERSION_PATTERN
from evals.contracts.eligibility import summarize
from evals.contracts.models import (
    FP1_GOLD_FIELDS,
    AuthoringAttestation,
    AuthoringPurpose,
    DraftedBy,
    ExpectedEligibility,
    FreeText,
    GoldConstraint,
    GoldField,
    GoldValue,
    Hardness,
    IntentFamily,
    LanguageStyle,
    Partition,
    ProductIntent,
    ProductIntentKind,
    QueryId,
    QueryRecord,
)

PILOT_QUERY_COUNT = 20
PILOT_HINGLISH_COUNT = 3


@dataclass(frozen=True)
class ApprovedQuery:
    query_id: str
    text: str
    language_style: LanguageStyle
    intent_family: IntentFamily
    expected_eligibility: ExpectedEligibility
    seen_in_development: bool


_E, _H, _N = LanguageStyle.ENGLISH, LanguageStyle.HINGLISH, LanguageStyle.NOISY
_F = IntentFamily
_NONEMPTY, _EMPTY = ExpectedEligibility.EXPECTED_NONEMPTY, ExpectedEligibility.EXPECTED_EMPTY

# seen_in_development is true exactly for the texts used verbatim as search queries in committed
# M0-M8 tests, scripts or docs (evidence in docs/golden-dataset-m9.md).
APPROVED_PILOT: tuple[ApprovedQuery, ...] = (
    ApprovedQuery("gq-p01", "headphones", _E, _F.BROAD_CATEGORY, _NONEMPTY, True),
    ApprovedQuery("gq-p02", "dell laptop", _E, _F.BRAND_CATEGORY, _NONEMPTY, True),
    ApprovedQuery("gq-p03", "adidas shoes", _E, _F.BRAND_CATEGORY, _NONEMPTY, False),
    ApprovedQuery("gq-p04", "lenovo lumen ryzen 7 laptop", _E, _F.EXACT_PRODUCT, _NONEMPTY, False),
    ApprovedQuery("gq-p05", "phone under 20k", _E, _F.PRICE, _NONEMPTY, False),
    ApprovedQuery("gq-p06", "bose headphones under 15000", _E, _F.PRICE, _NONEMPTY, False),
    ApprovedQuery("gq-p07", "laptop 16gb ram 512gb ssd", _E, _F.SPECIFICATION, _NONEMPTY, False),
    ApprovedQuery("gq-p08", "1tb nvme laptop", _E, _F.SPECIFICATION, _NONEMPTY, False),
    ApprovedQuery("gq-p09", "12gb ram phone 256gb storage", _E, _F.SPECIFICATION, _NONEMPTY, False),
    ApprovedQuery("gq-p10", "hp laptop 8gb 256 ssd under 40k", _E, _F.COMBINED, _NONEMPTY, True),
    ApprovedQuery("gq-p11", "gaming laptop", _E, _F.SEMANTIC_INTENT, _NONEMPTY, True),
    ApprovedQuery("gq-p12", "women running shoes", _E, _F.SPECIFICATION, _NONEMPTY, False),
    ApprovedQuery("gq-p13", "50k ke andar gaming laptop", _H, _F.COMBINED, _NONEMPTY, False),
    ApprovedQuery("gq-p14", "sasta phone 8gb ram wala", _H, _F.SPECIFICATION, _NONEMPTY, False),
    ApprovedQuery(
        "gq-p15", "office ke liye laptop 16gb ram", _H, _F.SEMANTIC_INTENT, _NONEMPTY, False
    ),
    ApprovedQuery("gq-p16", "samsng phone 128gb", _N, _F.COMBINED, _NONEMPTY, False),
    ApprovedQuery("gq-p17", "noise cancelling headfones", _N, _F.SPECIFICATION, _NONEMPTY, False),
    ApprovedQuery("gq-p18", "apple", _E, _F.AMBIGUOUS, _NONEMPTY, True),
    ApprovedQuery("gq-p19", "laptop 64gb ram", _E, _F.ZERO_ELIGIBLE, _EMPTY, False),
    ApprovedQuery("gq-p20", "smartwatch", _E, _F.OUT_OF_CATALOG, _EMPTY, False),
)

# The one product that satisfies p04's hard category/brand/processor constraints (inspected in
# Session C planning against the committed seed; re-derived by `validate_pilot`).
P04_HARD_ELIGIBLE = ("SYN-LAP-0003",)


# ---- the AI-drafted input file (no handle, no timestamp) ------------------------------------


class _Draft(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DraftQuery(_Draft):
    query_id: QueryId
    text: StrictStr
    language_style: LanguageStyle
    intent_family: IntentFamily
    interpretations: tuple[FreeText, ...] = ()
    expected_eligibility: ExpectedEligibility
    seen_in_development: StrictBool
    notes: FreeText | None = None


class DraftConstraint(_Draft):
    query_id: QueryId
    field: GoldField
    operator: FilterOperator
    value: GoldValue
    hardness: Hardness
    note: FreeText | None = None


class DraftProductIntent(_Draft):
    query_id: QueryId
    kind: ProductIntentKind
    value: StrictStr


class PilotInput(_Draft):
    """The AI-drafted pilot, exactly as shown to the human annotator for confirmation."""

    schema_version: Literal[1] = 1
    golden_id: Literal["internal-search"] = GOLDEN_ID
    golden_version: Literal[1] = 1
    partition: Literal["pilot_dev"] = "pilot_dev"
    purpose: Literal["development_rubric_calibration"] = "development_rubric_calibration"
    drafted_by: Literal["ai_assistant"] = "ai_assistant"
    blind_to_parser_output: Literal[False] = False
    final_test_evidence: Literal[False] = False
    rubric_version: Annotated[StrictStr, Field(pattern=RUBRIC_VERSION_PATTERN)]
    queries: tuple[DraftQuery, ...]
    gold_constraints: tuple[DraftConstraint, ...]
    product_intents: tuple[DraftProductIntent, ...]


@dataclass(frozen=True)
class QueryStageRecords:
    queries: list[QueryRecord]
    constraints: list[GoldConstraint]
    intents: list[ProductIntent]
    attestation: AuthoringAttestation


def build_records(
    draft: PilotInput, *, annotator: str, confirmed_at: datetime, input_sha256: str
) -> QueryStageRecords:
    """Turn the confirmed draft into recorded records. Every gold record carries the confirming
    annotator and the one confirmation time; `fp1_filterable` follows from the field."""
    partition = Partition(draft.partition)
    queries = [
        QueryRecord(**q.model_dump(), partition=partition)
        for q in sorted(draft.queries, key=lambda q: q.query_id)
    ]
    constraints = [
        GoldConstraint(
            **c.model_dump(),
            fp1_filterable=c.field in FP1_GOLD_FIELDS,
            annotator_handle=annotator,
            recorded_at=confirmed_at,
        )
        for c in draft.gold_constraints
    ]
    intents = [
        ProductIntent(**i.model_dump(), annotator_handle=annotator, recorded_at=confirmed_at)
        for i in draft.product_intents
    ]
    attestation = AuthoringAttestation(
        partition=partition,
        purpose=AuthoringPurpose(draft.purpose),
        drafted_by=DraftedBy(draft.drafted_by),
        confirmed_by=annotator,
        confirmed_at=confirmed_at,
        blind_to_parser_output=draft.blind_to_parser_output,
        final_test_evidence=draft.final_test_evidence,
        input_sha256=input_sha256,
        query_count=len(queries),
    )
    return QueryStageRecords(
        queries=queries,
        constraints=sorted(constraints, key=lambda c: c.sort_key()),
        intents=sorted(intents, key=lambda i: i.sort_key()),
        attestation=attestation,
    )


# ---- pilot validation ----------------------------------------------------------------------


def _hard(constraints: Sequence[GoldConstraint], query_id: str) -> set[tuple[str, str, str]]:
    return {
        (c.field.value, c.operator.value, str(c.value))
        for c in constraints
        if c.query_id == query_id and c.hardness is Hardness.HARD
    }


def _soft(constraints: Sequence[GoldConstraint], query_id: str) -> set[tuple[str, str, str]]:
    return {
        (c.field.value, c.operator.value, str(c.value))
        for c in constraints
        if c.query_id == query_id and c.hardness is Hardness.SOFT
    }


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise ContractError(f"pilot: {message}")


def validate_pilot(
    queries: Sequence[QueryRecord],
    constraints: Sequence[GoldConstraint],
    intents: Sequence[ProductIntent],
    catalog: CatalogIndex,
) -> None:
    """The approved pilot, exactly: identities and metadata, partition, Hinglish count, and the
    query-specific D14 and Session C1 rules."""
    _expect(len(queries) == PILOT_QUERY_COUNT, f"expected {PILOT_QUERY_COUNT} queries")
    ids = [q.query_id for q in queries]
    _expect(len(set(ids)) == len(ids), "query ids must be unique")
    approved = {a.query_id: a for a in APPROVED_PILOT}
    _expect(set(ids) == set(approved), "query ids must be exactly gq-p01..gq-p20")
    for q in queries:
        a = approved[q.query_id]
        _expect(q.partition is Partition.PILOT_DEV, f"{q.query_id} must be pilot_dev")
        _expect(q.text == a.text, f"{q.query_id} text differs from the approved text")
        _expect(q.language_style is a.language_style, f"{q.query_id} language style differs")
        _expect(q.intent_family is a.intent_family, f"{q.query_id} intent family differs")
        _expect(
            q.expected_eligibility is a.expected_eligibility,
            f"{q.query_id} expected eligibility differs",
        )
        _expect(
            q.seen_in_development is a.seen_in_development,
            f"{q.query_id} seen_in_development differs",
        )
    styles = Counter(q.language_style for q in queries)
    _expect(styles[LanguageStyle.HINGLISH] == PILOT_HINGLISH_COUNT, "expected 3 Hinglish queries")

    by_id = {q.query_id: q for q in queries}
    for query_id in ids:
        if query_id != "gq-p20":
            _expect(bool(_hard(constraints, query_id)), f"{query_id} needs a hard constraint")
    _expect(
        {i.query_id for i in intents} <= {"gq-p04"}, "only gq-p04 has a product (series) intent"
    )

    # gq-p04: hard category/brand/processor; Lumen only as a typed series intent.
    _expect(
        _hard(constraints, "gq-p04")
        == {
            ("category", "eq", "laptop"),
            ("brand", "eq", "Lenovo"),
            ("processor", "eq", "AMD Ryzen 7"),
        },
        "gq-p04 hard constraints must be laptop, Lenovo, AMD Ryzen 7",
    )
    _expect(not _soft(constraints, "gq-p04"), "gq-p04 has no soft constraints")
    _expect(
        [(i.kind, i.value) for i in intents if i.query_id == "gq-p04"]
        == [(ProductIntentKind.SERIES, "Lumen")],
        "gq-p04 must record Lumen as its one series intent",
    )
    _expect(
        summarize("gq-p04", constraints, catalog).satisfied == P04_HARD_ELIGIBLE,
        f"gq-p04 must have exactly {P04_HARD_ELIGIBLE} hard-eligible",
    )
    # gq-p08: NVMe is the storage_interface field; SSD is not added.
    _expect(
        _hard(constraints, "gq-p08")
        == {
            ("category", "eq", "laptop"),
            ("storage_gb", "eq", "1024"),
            ("storage_interface", "eq", "NVME"),
        },
        "gq-p08 hard constraints must be laptop, 1024 GB, storage_interface NVME (no SSD)",
    )
    # gq-p12: strict women running shoes; comfort is not a gold requirement.
    _expect(
        _hard(constraints, "gq-p12")
        == {
            ("category", "eq", "shoes"),
            ("subcategory", "eq", "running"),
            ("gender", "eq", "women"),
        },
        "gq-p12 hard constraints must be shoes, running, women",
    )
    _expect(not _soft(constraints, "gq-p12"), "gq-p12 has no soft constraints (no comfort)")
    # gq-p14: sasta is a soft low-price preference; no numeric price is invented.
    _expect(
        _hard(constraints, "gq-p14") == {("category", "eq", "phone"), ("ram_gb", "eq", "8")},
        "gq-p14 hard constraints must be phone, 8 GB RAM",
    )
    _expect(
        _soft(constraints, "gq-p14") == {("price_preference", "eq", "low")},
        "gq-p14 must record sasta only as a soft low price preference",
    )
    # gq-p18: brand only, no forced category, several interpretations.
    _expect(_hard(constraints, "gq-p18") == {("brand", "eq", "Apple")}, "gq-p18 is brand only")
    _expect(not _soft(constraints, "gq-p18"), "gq-p18 has no soft constraints")
    _expect(len(by_id["gq-p18"].interpretations) >= 2, "gq-p18 needs its interpretations")
    # gq-p20: out-of-catalog category intent; no fake category or constraint.
    _expect(not any(c.query_id == "gq-p20" for c in constraints), "gq-p20 records no constraints")
    _expect(by_id["gq-p20"].notes is not None, "gq-p20 must explain the out-of-catalog intent")
