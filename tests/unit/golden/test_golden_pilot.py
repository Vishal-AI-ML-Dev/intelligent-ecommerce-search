"""The approved pilot input, its records, the soft-signal contract, the qu-1 extraction record and
the authoring attestation (Session C1). The pilot input is the real AI draft; everything built
from it here uses a fixed fixture time and is never written to the repository."""

import json
from pathlib import Path

import pytest
from evals.contracts.canonical import CanonicalFormError, ContractError, decode_jsonl, encode_jsonl
from evals.contracts.eligibility import evaluate
from evals.contracts.extraction import derive_query_extraction
from evals.contracts.models import (
    AuthoringAttestation,
    AuthoringPurpose,
    DraftedBy,
    ExtractionStatus,
    ExtractionTarget,
    GoldConstraint,
    GoldField,
    Hardness,
    LanguageStyle,
    Partition,
    ProductIntent,
    ProductIntentKind,
    QueryExtraction,
    QueryRecord,
)
from evals.contracts.pilot import APPROVED_PILOT, PilotInput, build_records, validate_pilot
from evals.contracts.validation import (
    validate_attestation,
    validate_gold_constraints,
    validate_product_intents,
    validate_query_extraction,
)
from golden_support import T0, gold
from pydantic import ValidationError

from ecommerce_search.filtering.policy import FilterOperator

ROOT = Path(__file__).resolve().parents[3]
INPUT = ROOT / "evals" / "goldens" / "internal-search" / "v1" / "authoring" / "pilot_input.json"
SHA = "a" * 64

APPROVED_IDS = [f"gq-p{n:02d}" for n in range(1, 21)]
APPROVED_TEXTS = [
    "headphones",
    "dell laptop",
    "adidas shoes",
    "lenovo lumen ryzen 7 laptop",
    "phone under 20k",
    "bose headphones under 15000",
    "laptop 16gb ram 512gb ssd",
    "1tb nvme laptop",
    "12gb ram phone 256gb storage",
    "hp laptop 8gb 256 ssd under 40k",
    "gaming laptop",
    "women running shoes",
    "50k ke andar gaming laptop",
    "sasta phone 8gb ram wala",
    "office ke liye laptop 16gb ram",
    "samsng phone 128gb",
    "noise cancelling headfones",
    "apple",
    "laptop 64gb ram",
    "smartwatch",
]
SEEN = {"gq-p01", "gq-p02", "gq-p10", "gq-p11", "gq-p18"}


@pytest.fixture(scope="module")
def draft() -> PilotInput:
    return PilotInput.model_validate_json(INPUT.read_bytes(), strict=True)


@pytest.fixture(scope="module")
def records(draft):
    return build_records(draft, annotator="ann-1", confirmed_at=T0, input_sha256=SHA)


def _validate(queries, constraints, intents, catalog):
    validate_gold_constraints(queries, constraints, catalog)
    validate_product_intents(queries, intents, catalog)
    validate_pilot(queries, constraints, intents, catalog)


def _without(constraints, query_id, field):
    return [c for c in constraints if not (c.query_id == query_id and c.field is field)]


# ---- the approved pilot ---------------------------------------------------------------------


def test_the_draft_builds_a_valid_pilot(records, catalog):
    _validate(records.queries, records.constraints, records.intents, catalog)
    validate_attestation(records.queries, records.constraints, records.intents, records.attestation)


def test_exact_ids_texts_styles_and_seen_flags(records):
    queries = records.queries
    assert len(queries) == 20
    assert [q.query_id for q in queries] == APPROVED_IDS
    assert [q.text for q in queries] == APPROVED_TEXTS
    assert [a.text for a in APPROVED_PILOT] == APPROVED_TEXTS
    hinglish = [q.query_id for q in queries if q.language_style is LanguageStyle.HINGLISH]
    assert hinglish == ["gq-p13", "gq-p14", "gq-p15"]
    noisy = [q.query_id for q in queries if q.language_style is LanguageStyle.NOISY]
    assert noisy == ["gq-p16", "gq-p17"]
    assert {q.query_id for q in queries if q.seen_in_development} == SEEN
    assert {q.partition for q in queries} == {Partition.PILOT_DEV}


def _mutated(records, query_id, **changes):
    return [q.model_copy(update=changes) if q.query_id == query_id else q for q in records.queries]


@pytest.mark.parametrize(
    ("query_id", "changes", "message"),
    [
        ("gq-p05", {"text": "phone under 25k"}, "text differs"),
        ("gq-p05", {"language_style": LanguageStyle.NOISY}, "language style"),
        ("gq-p03", {"seen_in_development": True}, "seen_in_development"),
        ("gq-p01", {"seen_in_development": False}, "seen_in_development"),
        ("gq-p19", {"expected_eligibility": "unknown"}, "expected eligibility"),
    ],
)
def test_any_change_to_approved_metadata_is_refused(records, catalog, query_id, changes, message):
    queries = _mutated(records, query_id, **changes)
    with pytest.raises(ContractError, match=message):
        validate_pilot(queries, records.constraints, records.intents, catalog)


def test_count_uniqueness_and_partition_are_enforced(records, catalog):
    args = (records.constraints, records.intents, catalog)
    with pytest.raises(ContractError, match="expected 20"):
        validate_pilot(records.queries[:-1], *args)
    with pytest.raises(ContractError, match="unique"):
        validate_pilot([*records.queries[:-1], records.queries[0]], *args)
    test_record = QueryRecord(
        **{**records.queries[19].model_dump(), "query_id": "gq-t01", "partition": "test"}
    )
    with pytest.raises(ContractError, match="exactly gq-p01"):
        validate_pilot([*records.queries[:-1], test_record], *args)


def test_seen_queries_can_never_be_test_records():
    with pytest.raises(ValidationError, match="seen in development"):
        QueryRecord(
            query_id="gq-t01",
            text="headphones",
            language_style="english",
            intent_family="broad_category",
            expected_eligibility="expected_nonempty",
            seen_in_development=True,
            partition="test",
        )


# ---- query-specific rules ------------------------------------------------------------------


def test_p04_lumen_is_a_series_intent_and_never_hard(records, catalog):
    p04 = [c for c in records.constraints if c.query_id == "gq-p04"]
    assert {(c.field, c.value) for c in p04} == {
        (GoldField.CATEGORY, "laptop"),
        (GoldField.BRAND, "Lenovo"),
        (GoldField.PROCESSOR, "AMD Ryzen 7"),
    }
    assert all(c.hardness is Hardness.HARD for c in p04)
    assert [(i.kind, i.value) for i in records.intents] == [(ProductIntentKind.SERIES, "Lumen")]
    lumen = [p for p in catalog.products.values() if "Lumen" in p.title]
    eligible = {
        p.product_id
        for p in lumen
        if evaluate("gq-p04", records.constraints, p).hard_overall.value == "satisfied"
    }
    assert eligible == {"SYN-LAP-0003"}
    missing = _without(records.constraints, "gq-p04", GoldField.PROCESSOR)
    with pytest.raises(ContractError, match="gq-p04"):
        validate_pilot(records.queries, missing, records.intents, catalog)
    with pytest.raises(ContractError, match="Lumen"):
        validate_pilot(records.queries, records.constraints, [], catalog)


def test_p08_uses_storage_interface_and_adds_no_ssd(records, catalog):
    p08 = {(c.field, c.value) for c in records.constraints if c.query_id == "gq-p08"}
    assert p08 == {
        (GoldField.CATEGORY, "laptop"),
        (GoldField.STORAGE_GB, 1024),
        (GoldField.STORAGE_INTERFACE, "NVME"),
    }
    with_ssd = [*records.constraints, gold("gq-p08", GoldField.STORAGE_TYPE, "SSD")]
    with pytest.raises(ContractError, match="no SSD"):
        validate_pilot(records.queries, with_ssd, records.intents, catalog)


def test_p12_is_strict_and_has_no_comfort(records, catalog):
    comfort = gold("gq-p12", GoldField.SEMANTIC_INTENT, "comfort", hardness=Hardness.SOFT)
    with pytest.raises(ContractError, match="no comfort"):
        validate_pilot(records.queries, [*records.constraints, comfort], records.intents, catalog)
    soft_gender = [
        c.model_copy(update={"hardness": Hardness.SOFT})
        if (c.query_id, c.field) == ("gq-p12", GoldField.GENDER)
        else c
        for c in records.constraints
    ]
    with pytest.raises(ContractError, match="gq-p12"):
        validate_pilot(records.queries, soft_gender, records.intents, catalog)


def test_p14_sasta_is_soft_and_invents_no_price(records, catalog):
    p14 = [c for c in records.constraints if c.query_id == "gq-p14"]
    assert {(c.field, c.hardness) for c in p14} == {
        (GoldField.CATEGORY, Hardness.HARD),
        (GoldField.RAM_GB, Hardness.HARD),
        (GoldField.PRICE_PREFERENCE, Hardness.SOFT),
    }
    priced = [
        *records.constraints,
        gold("gq-p14", GoldField.PRICE, "20000.00", operator=FilterOperator.LTE),
    ]
    with pytest.raises(ContractError, match="gq-p14"):
        validate_pilot(records.queries, priced, records.intents, catalog)
    unpreferenced = _without(records.constraints, "gq-p14", GoldField.PRICE_PREFERENCE)
    with pytest.raises(ContractError, match="sasta"):
        validate_pilot(records.queries, unpreferenced, records.intents, catalog)


def test_p18_is_brand_only_with_several_interpretations(records, catalog):
    p18 = next(q for q in records.queries if q.query_id == "gq-p18")
    assert len(p18.interpretations) >= 2
    forced = [*records.constraints, gold("gq-p18", GoldField.CATEGORY, "phone")]
    with pytest.raises(ContractError, match="gq-p18"):
        validate_pilot(records.queries, forced, records.intents, catalog)


def test_p20_records_no_fake_category_or_constraint(records, catalog):
    assert not [c for c in records.constraints if c.query_id == "gq-p20"]
    with pytest.raises(ValidationError, match="not allowed"):
        gold("gq-p20", GoldField.CATEGORY, "smartwatch")
    faked = [*records.constraints, gold("gq-p20", GoldField.CATEGORY, "headphones")]
    with pytest.raises(ContractError, match="gq-p20"):
        validate_pilot(records.queries, faked, records.intents, catalog)


# ---- soft signals and product intents --------------------------------------------------------


def test_soft_signals_use_closed_vocabularies_and_are_never_hard_or_fp1():
    gold("gq-p11", GoldField.SEMANTIC_INTENT, "gaming", hardness=Hardness.SOFT)
    gold("gq-p14", GoldField.PRICE_PREFERENCE, "low", hardness=Hardness.SOFT)
    with pytest.raises(ValidationError, match="soft signal"):
        gold("gq-p11", GoldField.SEMANTIC_INTENT, "gaming")
    with pytest.raises(ValidationError, match="not allowed"):
        gold("gq-p11", GoldField.SEMANTIC_INTENT, "fast", hardness=Hardness.SOFT)
    with pytest.raises(ValidationError, match="not allowed"):
        gold("gq-p14", GoldField.PRICE_PREFERENCE, "high", hardness=Hardness.SOFT)
    with pytest.raises(ValidationError, match="fp1_filterable"):
        GoldConstraint(
            **{
                **gold(
                    "gq-p11", GoldField.SEMANTIC_INTENT, "gaming", hardness=Hardness.SOFT
                ).model_dump(),
                "fp1_filterable": True,
            }
        )


def test_series_intents_are_typed_and_must_be_catalog_title_tokens(records, catalog):
    with pytest.raises(ValidationError):
        ProductIntent(
            query_id="gq-p04",
            kind="series",
            value="Lumen 14",
            annotator_handle="ann-1",
            recorded_at=T0,
        )
    with pytest.raises(ValidationError):
        ProductIntent(
            query_id="gq-p04",
            kind="gaming",
            value="Lumen",
            annotator_handle="ann-1",
            recorded_at=T0,
        )
    unknown = ProductIntent(
        query_id="gq-p04",
        kind="series",
        value="Zenbookz",
        annotator_handle="ann-1",
        recorded_at=T0,
    )
    with pytest.raises(ContractError, match="not a token"):
        validate_product_intents(records.queries, [unknown], catalog)


# ---- canonical form --------------------------------------------------------------------------


def test_records_round_trip_canonically_and_refuse_duplicates(records):
    for items, model in (
        (records.queries, QueryRecord),
        (records.constraints, GoldConstraint),
        (records.intents, ProductIntent),
    ):
        data = encode_jsonl(items)
        assert encode_jsonl(decode_jsonl(data, model)) == data
        assert encode_jsonl(list(reversed(items))) == data  # stable canonical order
    with pytest.raises(CanonicalFormError, match="duplicate"):
        encode_jsonl([*records.constraints, records.constraints[0]])
    with pytest.raises(ContractError, match="one constraint per"):
        validate_gold_constraints(
            records.queries,
            [*records.constraints, records.constraints[0].model_copy(update={"value": "Dell"})],
        )


def test_input_refuses_handles_timestamps_and_unknown_fields(draft):
    body = json.loads(INPUT.read_bytes())
    body["gold_constraints"][0]["annotator_handle"] = "ann-1"
    with pytest.raises(ValidationError):
        PilotInput.model_validate_json(json.dumps(body), strict=True)
    body = json.loads(INPUT.read_bytes())
    body["blind_to_parser_output"] = True
    with pytest.raises(ValidationError):
        PilotInput.model_validate_json(json.dumps(body), strict=True)


# ---- qu-1 extraction (generated) -----------------------------------------------------------


def _extraction(records):
    return derive_query_extraction(records.queries, records.constraints, records.intents)


def _status(extraction, query_id, field):
    return next(e for e in extraction if (e.query_id, e.field) == (query_id, field))


def test_extraction_keeps_filterable_and_extracted_apart(records):
    extraction = _extraction(records)
    p06 = _status(extraction, "gq-p06", "price")
    assert (p06.fp1_filterable, p06.status, p06.fp1_applied) == (
        True,
        ExtractionStatus.NOT_EXTRACTED,
        False,
    )
    for field in ("brand", "storage_gb"):
        assert _status(extraction, "gq-p16", field).status is ExtractionStatus.NOT_EXTRACTED
    assert _status(extraction, "gq-p17", "category").status is ExtractionStatus.NOT_EXTRACTED
    assert _status(extraction, "gq-p04", "processor").status is ExtractionStatus.NO_PARSER_FIELD
    series = _status(extraction, "gq-p04", "series")
    assert series.target is ExtractionTarget.PRODUCT_INTENT
    assert series.status is ExtractionStatus.NO_PARSER_FIELD and series.parsed_value is None
    assert _status(extraction, "gq-p14", "price_preference").status is ExtractionStatus.EXTRACTED
    assert _status(extraction, "gq-p10", "price").fp1_applied is True
    assert len(extraction) == len(records.constraints) + len(records.intents)


def test_extraction_is_deterministic_and_order_independent(records):
    first = encode_jsonl(_extraction(records))
    shuffled = derive_query_extraction(
        list(reversed(records.queries)),
        list(reversed(records.constraints)),
        records.intents,
    )
    assert encode_jsonl(shuffled) == first == encode_jsonl(_extraction(records))
    validate_query_extraction(
        records.queries, records.constraints, records.intents, decode_jsonl(first, QueryExtraction)
    )
    tampered = [
        e.model_copy(update={"fp1_applied": False}) if e.fp1_applied else e
        for e in _extraction(records)
    ]
    with pytest.raises(ContractError, match="regeneration"):
        validate_query_extraction(records.queries, records.constraints, records.intents, tampered)


def _record(**changes) -> dict:
    base = dict(
        query_id="gq-p06",
        target="gold_constraint",
        field="price",
        operator="lte",
        gold_value="15000.00",
        status="not_extracted",
        parsed_value=None,
        fp1_filterable=True,
        fp1_applied=False,
    )
    return {**base, **changes}


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"fp1_applied": True}, "fp1_applied"),
        ({"status": "extracted", "parsed_value": "16000.00"}, "equal the gold value"),
        ({"status": "extracted_other_value", "parsed_value": "15000.00"}, "different"),
        ({"parsed_value": "15000.00"}, "parsed_value"),
        ({"fp1_filterable": False}, "fp1_filterable"),
        ({"target": "product_intent", "field": "series", "operator": None}, "product-intent"),
    ],
)
def test_extraction_records_are_internally_consistent(changes, message):
    QueryExtraction(**_record())
    with pytest.raises(ValidationError, match=message):
        QueryExtraction(**_record(**changes))


# ---- authoring attestation (recorded) ------------------------------------------------------


def test_attestation_records_the_ai_draft_and_human_confirmation(records):
    a = records.attestation
    assert (a.drafted_by, a.confirmed_by, a.blind_to_parser_output, a.final_test_evidence) == (
        DraftedBy.AI_ASSISTANT,
        "ann-1",
        False,
        False,
    )
    assert a.purpose is AuthoringPurpose.DEVELOPMENT_RUBRIC_CALIBRATION
    assert a.query_count == 20 and a.confirmed_at == T0
    with pytest.raises(ValidationError, match="never final test evidence"):
        AuthoringAttestation(**{**a.model_dump(), "final_test_evidence": True})
    with pytest.raises(ValidationError, match="development purpose"):
        AuthoringAttestation(**{**a.model_dump(), "partition": "test"})


def test_attestation_binds_annotator_and_time(records):
    other = records.attestation.model_copy(update={"confirmed_by": "ann-2"})
    with pytest.raises(ContractError, match="ann-1"):
        validate_attestation(records.queries, records.constraints, records.intents, other)
    late = [
        c.model_copy(update={"recorded_at": c.recorded_at.replace(microsecond=0)})
        for c in records.constraints
    ]
    with pytest.raises(ContractError, match="differs from confirmation"):
        validate_attestation(records.queries, late, records.intents, records.attestation)
    wrong_count = records.attestation.model_copy(update={"query_count": 19})
    with pytest.raises(ContractError, match="query_count"):
        validate_attestation(records.queries, records.constraints, records.intents, wrong_count)
