"""Category-only partitions, metrics, frozen threshold, shadow gate and acceptance (all mocked).

Every query and label is a SYNTHETIC TEST FIXTURE and every Jev outcome is fabricated for
software tests; nothing here is Jev output, human data or an approved experimental parameter.
Thresholds and criteria below are arbitrary test values.

Batch A is the shared six-row fixture: the parser decides `laptop` on dq-001 and dq-002 and no
category on dq-003 (headphones gold), dq-004 (none), dq-005 (ambiguous laptop/phone) and dq-006
(unclear). Batch B holds other parser-abstaining texts for a calibration partition.
"""

import ast
import hashlib
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from evals.contracts.canonical import encode_json_object
from evals.query_decisions.acceptance import (
    clopper_pearson_lower,
    clopper_pearson_upper,
    confirm_acceptance,
    evaluation_content_sha256,
    score_acceptance,
)
from evals.query_decisions.category_metrics import (
    classification_metrics,
    deterministic_predictor,
    fill_counts,
    gate_predictor,
    jev_predictor,
    threshold_sweep,
)
from evals.query_decisions.contracts import CATEGORY_LABELS, QueryDecisionError
from evals.query_decisions.gate import (
    AcceptanceCriteria,
    freeze_threshold,
    load_threshold,
    shadow_category_gate,
)
from evals.query_decisions.labeling import seal_labels
from evals.query_decisions.partitions import (
    PARTITIONS_DIR,
    BatchRun,
    PartitionRole,
    SourceSelection,
    build_partition,
    check_disjoint,
    load_partition,
    resolve_members,
    write_partition,
)
from pydantic import ValidationError
from query_decision_support import (
    FIXTURE_ROWS,
    T0,
    outcome,
    run,
    write_fixture_labels,
    write_fixture_queries,
)

from ecommerce_search.decision.provider import CategoryProposal, DecisionResult
from ecommerce_search.filtering import FilterPolicyError, derive_filters
from ecommerce_search.query_understanding import parse_normalized_query

ROOT_DIR = Path(__file__).resolve().parents[3]
B_ROWS = [
    ("dq-001", "garden hose 20m", "none", "none"),
    ("dq-002", "camera tripod stand", "none", "none"),
]
EVAL_IDS = ("dq-003", "dq-004", "dq-005", "dq-006")
# dq-001..dq-006 of batch A: two parser-decided, then headphones (correct fill), none (Jev none),
# ambiguous laptop/phone (Jev commits inside the candidates), unclear (excluded).
A_SCRIPT = [
    outcome("laptop", "none"),
    outcome("laptop", "gaming"),
    outcome("headphones", "travel"),
    outcome("none", "none"),
    outcome("phone", "none"),
    outcome("laptop", "none"),
]


def criteria(**overrides) -> AcceptanceCriteria:
    values = {
        "confidence_level": Decimal("0.95"),
        "wrong_commit_definition": "lenient",
        "max_wrong_commit_upper_bound": Decimal("1"),
        "min_coverage": Decimal("0.5"),
        "min_accepted_fills": 1,
        "max_unknown_or_fallback_rate": Decimal("0"),
        "max_unclear_rate": Decimal("0.3"),
    }
    values.update(overrides)
    return AcceptanceCriteria(**values)


def sealed_batch(path: Path, rows) -> Path:
    path.mkdir()
    write_fixture_queries(path, rows)
    write_fixture_labels(path, rows)
    seal_labels(path, now=lambda: T0 - timedelta(days=1))
    return path


@pytest.fixture
def setup(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    batch_a = sealed_batch(tmp_path / "a", FIXTURE_ROWS)
    batch_b = sealed_batch(tmp_path / "b", B_ROWS)
    return root, batch_a, batch_b


def write_partitions(root, batch_a, batch_b):
    calibration = build_partition(
        "cal-1",
        PartitionRole.CALIBRATION,
        [SourceSelection(batch_b, ("dq-001", "dq-002"))],
        screening="parser_abstains_on_category",
        now=T0 - timedelta(hours=3),
    )
    evaluation = build_partition(
        "eval-1",
        PartitionRole.EVALUATION,
        [SourceSelection(batch_a, EVAL_IDS)],
        screening="parser_abstains_on_category",
        now=T0 - timedelta(hours=3),
    )
    write_partition(root, calibration)
    write_partition(root, evaluation)
    return calibration, evaluation


def evaluation_records(root, batch_a, batch_b, script=A_SCRIPT):
    _, evaluation = write_partitions(root, batch_a, batch_b)
    run(batch_a, script)
    return resolve_members(evaluation, [BatchRun(batch_a, "r1")])


# --- partitions ---------------------------------------------------------------------------------


def test_a_partition_is_screened_written_once_and_reloaded_unchanged(setup):
    root, batch_a, batch_b = setup
    calibration, evaluation = write_partitions(root, batch_a, batch_b)
    assert evaluation.member_count == 4 and evaluation.synthetic is True
    assert (evaluation.parser_version, evaluation.lexicon_version) == ("qu-1", "1")
    loaded, digest = load_partition(root, "eval-1")
    assert loaded == evaluation and len(digest) == 64
    with pytest.raises(Exception, match="overwrite"):
        write_partition(root, evaluation)
    text = (root / PARTITIONS_DIR / "eval-1.json").read_text(encoding="utf-8")
    assert "headfones" not in text  # members carry hashes, never query text


def test_screening_refuses_a_query_the_parser_already_decides(setup):
    _, batch_a, _ = setup
    with pytest.raises(QueryDecisionError, match="parser decides a category"):
        build_partition(
            "eval-x",
            PartitionRole.EVALUATION,
            [SourceSelection(batch_a, ("dq-001",))],
            screening="parser_abstains_on_category",
        )
    unscreened = build_partition(
        "eval-y",
        PartitionRole.EVALUATION,
        [SourceSelection(batch_a, ("dq-001",))],
        screening="none",
    )
    assert unscreened.member_count == 1


@pytest.mark.parametrize("ids", [("dq-099",), ("dq-003", "dq-003")])
def test_unknown_or_repeated_selections_are_refused(setup, ids):
    _, batch_a, _ = setup
    with pytest.raises(QueryDecisionError):
        build_partition(
            "eval-x", PartitionRole.EVALUATION, [SourceSelection(batch_a, ids)], screening="none"
        )


def test_overlapping_partitions_are_refused_by_member_and_by_text(setup, tmp_path):
    _, batch_a, batch_b = setup
    evaluation = build_partition(
        "eval-1", PartitionRole.EVALUATION, [SourceSelection(batch_a, EVAL_IDS)], screening="none"
    )
    same_member = build_partition(
        "cal-1",
        PartitionRole.CALIBRATION,
        [SourceSelection(batch_a, ("dq-003",))],
        screening="none",
    )
    with pytest.raises(QueryDecisionError, match="1 shared member"):
        check_disjoint(same_member, evaluation)
    batch_c = sealed_batch(
        tmp_path / "c", [("dq-001", "headfones for travel", "headphones", "none")]
    )
    same_text = build_partition(
        "cal-2",
        PartitionRole.CALIBRATION,
        [SourceSelection(batch_c, ("dq-001",))],
        screening="none",
    )
    with pytest.raises(QueryDecisionError, match="0 shared member.*1 shared normalized text"):
        check_disjoint(same_text, evaluation)
    clean = build_partition(
        "cal-3",
        PartitionRole.CALIBRATION,
        [SourceSelection(batch_b, ("dq-001",))],
        screening="none",
    )
    check_disjoint(clean, evaluation)
    with pytest.raises(QueryDecisionError, match="calibration partition"):
        check_disjoint(evaluation, clean)


def test_members_resolve_only_against_exactly_their_source_batches(setup):
    root, batch_a, batch_b = setup
    _, evaluation = write_partitions(root, batch_a, batch_b)
    with pytest.raises(QueryDecisionError, match="exactly the partition's source"):
        resolve_members(evaluation, [BatchRun(batch_a), BatchRun(batch_b)])
    records = resolve_members(evaluation, [BatchRun(batch_a)])
    assert [r.member.query_id for r in records] == list(EVAL_IDS)
    assert all(r.outcome is None and not r.attempted and r.run is None for r in records)


# --- shadow gate --------------------------------------------------------------------------------


def _tie():
    probabilities = {label: 0.0 for label in CATEGORY_LABELS} | {"laptop": 0.5, "phone": 0.5}
    return outcome().model_copy(
        update={
            "category": CategoryProposal(
                label=None,
                raw_choice="laptop",
                top_labels=("laptop", "phone"),
                provider_probabilities=probabilities,
                provider_confidence=0.375,
            )
        }
    )


@pytest.mark.parametrize(
    ("text", "jev", "threshold", "expected"),
    [
        ("hp laptop 16gb ram", outcome("phone"), "0.5", ("laptop", "deterministic_decided")),
        ("headfones for travel", outcome("headphones"), "0.9", ("headphones", "accepted")),
        ("headfones for travel", outcome("headphones"), "0.95", (None, "below_threshold")),
        ("headfones for travel", outcome("none"), "0.5", (None, "jev_none")),
        ("headfones for travel", _tie(), "0.5", (None, "jev_tie")),
        ("headfones for travel", outcome(fallback="timeout"), "0.5", (None, "jev_fallback")),
        ("headfones for travel", None, "0.5", (None, "jev_missing")),
    ],
)
def test_shadow_gate_rule(text, jev, threshold, expected):
    decision = shadow_category_gate(parse_normalized_query(text), jev, Decimal(threshold))
    assert (decision.category, decision.reason) == expected


def test_the_shadow_gate_never_changes_filters_or_search_code():
    parsed = parse_normalized_query("headfones for travel")
    deterministic = DecisionResult(understanding=parsed)
    with_jev = DecisionResult(
        provider="jev", provider_version="jq-1", understanding=parsed, jev=outcome("headphones")
    )
    before = derive_filters(deterministic)
    decision = shadow_category_gate(parsed, with_jev.jev, Decimal("0.5"))
    assert decision.category == "headphones"  # the gate would accept ...
    assert derive_filters(deterministic) == before and before.spec.is_empty()  # ... no filter
    with pytest.raises(FilterPolicyError):  # fp-1 still refuses any Jev-provider decision
        derive_filters(with_jev)
    for path in (ROOT_DIR / "src").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = [
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        ] + [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
        assert not any(name.startswith("evals") for name in imported), path


# --- frozen threshold ---------------------------------------------------------------------------


def test_criteria_have_no_defaults():
    with pytest.raises(ValidationError):
        AcceptanceCriteria()


def test_a_threshold_is_frozen_once_and_bound_to_its_calibration_partition(setup):
    root, batch_a, batch_b = setup
    calibration, _ = write_partitions(root, batch_a, batch_b)
    frozen, digest = freeze_threshold(
        root,
        threshold_id="t-1",
        calibration_partition_id="cal-1",
        threshold=Decimal("0.5"),
        model="jev-1.13.0",
        criteria=criteria(),
        now=T0 - timedelta(hours=1),
    )
    assert load_threshold(root, "t-1") == (frozen, digest)
    assert frozen.calibration_partition_sha256 == load_partition(root, "cal-1")[1]
    with pytest.raises(Exception, match="overwrite"):
        freeze_threshold(
            root,
            threshold_id="t-1",
            calibration_partition_id="cal-1",
            threshold=Decimal("0.6"),
            model="jev-1.13.0",
            criteria=criteria(),
        )
    with pytest.raises(QueryDecisionError, match="calibration partition only"):
        freeze_threshold(
            root,
            threshold_id="t-2",
            calibration_partition_id="eval-1",
            threshold=Decimal("0.5"),
            model="jev-1.13.0",
            criteria=criteria(),
        )
    changed = calibration.model_copy(update={"sealed_at": T0})
    (root / PARTITIONS_DIR / "cal-1.json").write_bytes(encode_json_object(changed))
    with pytest.raises(QueryDecisionError, match="changed after the threshold was frozen"):
        load_threshold(root, "t-1")


# --- metrics ------------------------------------------------------------------------------------


def test_category_metrics_separate_none_abstention_ambiguous_and_unclear(setup):
    records = evaluation_records(*setup)

    deterministic = classification_metrics(records, deterministic_predictor)
    assert deterministic["unclear_excluded"] == 1
    assert deterministic["single_category"]["abstain"] == 1
    assert deterministic["single_category"]["coverage"] == "0.000000"

    jev = classification_metrics(records, jev_predictor)
    assert jev["outcomes"] == {
        "commit_in_candidates": 1,
        "correct": 1,
        "correct_none": 1,
        "excluded_unclear": 1,
    }
    assert jev["confusion_matrix"]["rows"]["none"]["none"] == 1  # an explicit none, not abstain
    assert jev["per_category"]["headphones"] == {
        "support": 1,
        "predicted": 1,
        "true_positive": 1,
        "precision": "1.000000",
        "recall": "1.000000",
        "f1": "1.000000",
    }
    assert jev["ambiguous"]["commit_in_candidates"] == 1

    gated = classification_metrics(records, gate_predictor(Decimal("0.5")))
    # Jev's `none` fills nothing: the gold-none query is an abstention for the gate.
    assert gated["confusion_matrix"]["rows"]["none"] == dict.fromkeys(
        (*CATEGORY_LABELS, "abstain"), 0
    ) | {"abstain": 1}
    assert gated["outcomes"]["abstain"] == 1


def test_fill_counts_and_a_sweep_over_supplied_thresholds_only(setup):
    records = evaluation_records(*setup)
    fills = fill_counts(records, Decimal("0.5"))
    assert {k: fills[k] for k in ("unclear", "parser_abstains_scored", "accepted_fills")} == {
        "unclear": 1,
        "parser_abstains_scored": 3,
        "accepted_fills": 2,
    }
    assert (fills["wrong_commits_strict"], fills["wrong_commits_lenient"]) == (1, 0)
    assert (fills["correct_fills"], fills["coverage_denominator"], fills["coverage"]) == (
        1,
        1,
        "1.000000",
    )
    assert fills["gate_reasons"] == {"accepted": 2, "jev_none": 1}
    sweep = threshold_sweep(records, [Decimal("0.95"), Decimal("0.5")])
    assert [row["threshold"] for row in sweep] == ["0.5", "0.95"]
    assert sweep[1]["accepted_fills"] == 0 and sweep[1]["gate_reasons"]["below_threshold"] == 2
    for bad in ([], [Decimal("0.5"), Decimal("0.5")], [Decimal("0")], [Decimal("1.5")]):
        with pytest.raises(ValueError):
            threshold_sweep(records, bad)


# --- bounds and acceptance ----------------------------------------------------------------------


def test_clopper_pearson_bounds_match_closed_forms():
    confidence = Decimal("0.95")
    upper = clopper_pearson_upper(0, 59, confidence)
    assert abs(float(upper) - (1 - 0.05 ** (1 / 59))) < 2e-6 and float(upper) <= 0.05
    lower = clopper_pearson_lower(20, 20, confidence)
    assert abs(float(lower) - 0.05 ** (1 / 20)) < 2e-6
    assert clopper_pearson_upper(3, 3, confidence) == 1
    assert clopper_pearson_lower(0, 7, confidence) == 0
    assert clopper_pearson_upper(0, 0, confidence) is None


def _freeze(root, now, **kwargs):
    values = {
        "threshold_id": "t-1",
        "calibration_partition_id": "cal-1",
        "threshold": Decimal("0.5"),
        "model": "jev-1.13.0",
        "criteria": criteria(),
        "now": now,
    }
    values.update(kwargs)
    return freeze_threshold(root, **values)


def _accept(root, batch_a):
    return score_acceptance(
        root,
        threshold_id="t-1",
        evaluation_partition_id="eval-1",
        batches=[BatchRun(batch_a, "r1")],
    )


@pytest.mark.parametrize(
    ("overrides", "outcome_name", "reason"),
    [
        ({}, "pass", None),
        (
            {"wrong_commit_definition": "strict", "max_wrong_commit_upper_bound": Decimal("0.5")},
            "fail",
            "wrong-commit upper bound above its maximum",
        ),
        ({"min_coverage": Decimal("1"), "min_accepted_fills": 3}, "insufficient_evidence", None),
        ({"max_unclear_rate": Decimal("0.2")}, "insufficient_evidence", "unclear rate"),
    ],
)
def test_acceptance_outcomes_follow_the_frozen_criteria(setup, overrides, outcome_name, reason):
    root, batch_a, batch_b = setup
    write_partitions(root, batch_a, batch_b)
    _freeze(root, T0 - timedelta(hours=1), criteria=criteria(**overrides))
    run(batch_a, A_SCRIPT)
    report = _accept(root, batch_a)
    assert report["outcome"] == outcome_name
    if reason is not None:
        assert any(reason in item for item in report["reasons"])
    assert report["synthetic"] is True
    assert report["fills"]["accepted_fills"] == 2 and report["coverage"] == "1.000000"
    assert "not a production decision" in report["meaning"]
    confirmation = _confirm(root, batch_a)
    assert confirmation.report == report and not confirmation.repeated
    assert confirmation.report_path.name == "t-1.json"


def test_acceptance_refuses_a_threshold_frozen_after_the_evaluation_run(setup):
    root, batch_a, batch_b = setup
    write_partitions(root, batch_a, batch_b)
    run(batch_a, A_SCRIPT)
    _freeze(root, T0 + timedelta(days=1))
    with pytest.raises(QueryDecisionError, match="dispatched before the threshold was frozen"):
        _accept(root, batch_a)


def test_acceptance_refuses_a_model_mismatch_and_overlapping_partitions(setup):
    root, batch_a, batch_b = setup
    write_partitions(root, batch_a, batch_b)
    _freeze(root, T0 - timedelta(hours=1), model="jev-9.9.9")
    run(batch_a, A_SCRIPT)
    with pytest.raises(QueryDecisionError, match="model or question set"):
        _accept(root, batch_a)

    overlapping = build_partition(
        "cal-9",
        PartitionRole.CALIBRATION,
        [SourceSelection(batch_a, ("dq-003",))],
        screening="none",
        now=T0 - timedelta(hours=3),
    )
    write_partition(root, overlapping)
    _freeze(root, T0 - timedelta(hours=1), threshold_id="t-9", calibration_partition_id="cal-9")
    with pytest.raises(QueryDecisionError, match="partitions overlap"):
        score_acceptance(
            root,
            threshold_id="t-9",
            evaluation_partition_id="eval-1",
            batches=[BatchRun(batch_a, "r1")],
        )


def test_an_incomplete_evaluation_run_is_insufficient_evidence(setup):
    root, batch_a, batch_b = setup
    write_partitions(root, batch_a, batch_b)
    _freeze(root, T0 - timedelta(hours=1), criteria=criteria(max_unknown_or_fallback_rate=1))
    run(batch_a, A_SCRIPT[:4] + [outcome(fallback="auth_error")])  # stops the run at dq-005
    report = _accept(root, batch_a)
    assert report["outcome"] == "insufficient_evidence"
    assert "a run is incomplete or blocked" in report["reasons"]
    assert report["run"]["not_attempted"] == 1 and report["run"]["fallbacks"] == 1


# --- chronology and evaluation consumption ------------------------------------------------------


def test_a_partition_sealed_before_a_source_batch_seal_is_refused(setup):
    root, batch_a, batch_b = setup
    with pytest.raises(QueryDecisionError, match="sealed after every source batch seal"):
        build_partition(
            "eval-x",
            PartitionRole.EVALUATION,
            [SourceSelection(batch_a, EVAL_IDS)],
            screening="none",
            now=T0 - timedelta(days=2),  # batch A was sealed at T0 - 1 day
        )
    _, evaluation = write_partitions(root, batch_a, batch_b)
    backdated = evaluation.model_copy(update={"sealed_at": T0 - timedelta(days=1)})  # not after
    with pytest.raises(QueryDecisionError, match="sealed after every source batch seal"):
        resolve_members(backdated, [BatchRun(batch_a)])


def _confirm(root, batch_a, threshold_id="t-1", evaluation_partition_id="eval-1", batches=None):
    return confirm_acceptance(
        root,
        threshold_id=threshold_id,
        evaluation_partition_id=evaluation_partition_id,
        batches=batches or [BatchRun(batch_a, "r1")],
        now=T0 + timedelta(days=1),
    )


def _confirmed(setup):
    root, batch_a, batch_b = setup
    calibration, evaluation = write_partitions(root, batch_a, batch_b)
    _freeze(root, T0 - timedelta(hours=1))
    run(batch_a, A_SCRIPT)
    first = _confirm(root, batch_a)
    return root, batch_a, batch_b, evaluation, first


def _files(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_the_consumption_record_is_keyed_by_evaluation_content(setup):
    root, _, _, evaluation, first = _confirmed(setup)
    content = evaluation_content_sha256(evaluation.members)
    assert first.record.evaluation_content_sha256 == content
    assert (root / "consumption" / f"{content}.json").is_file()
    assert first.record.report_sha256 == hashlib.sha256(first.report_path.read_bytes()).hexdigest()


def test_an_identical_repeat_writes_nothing_and_returns_the_stored_report(setup):
    root, batch_a, _, _, first = _confirmed(setup)
    before = _files(root)
    again = _confirm(root, batch_a)
    assert again.repeated and again.report == first.report and again.record == first.record
    assert _files(root) == before


def test_an_interruption_before_the_report_is_completed_by_an_identical_repeat(setup):
    root, batch_a, _, _, first = _confirmed(setup)
    data = first.report_path.read_bytes()
    first.report_path.unlink()  # as if the process stopped after the consumption record
    again = _confirm(root, batch_a)
    assert again.repeated and again.report_path.read_bytes() == data


def test_renamed_or_overlapping_evaluation_data_cannot_be_confirmed_again(setup):
    root, batch_a, _, evaluation, _ = _confirmed(setup)
    renamed = evaluation.model_copy(update={"partition_id": "eval-2", "sealed_at": T0})
    write_partition(root, renamed)
    subset = build_partition(
        "eval-3",
        PartitionRole.EVALUATION,
        [SourceSelection(batch_a, ("dq-003",))],
        screening="none",
        now=T0,
    )
    write_partition(root, subset)
    for partition_id, threshold_id in (("eval-2", "t-2"), ("eval-3", "t-3")):
        _freeze(root, T0 - timedelta(minutes=30), threshold_id=threshold_id)
        with pytest.raises(QueryDecisionError, match="already consumed by a different"):
            _confirm(root, batch_a, threshold_id, partition_id)


def test_a_threshold_cannot_confirm_a_second_evaluation(setup, tmp_path):
    root, batch_a, _, _, _ = _confirmed(setup)
    batch_c = sealed_batch(tmp_path / "c", [("dq-001", "office chair mesh", "none", "none")])
    other = build_partition(
        "eval-4",
        PartitionRole.EVALUATION,
        [SourceSelection(batch_c, ("dq-001",))],
        screening="parser_abstains_on_category",
        now=T0,
    )
    write_partition(root, other)
    with pytest.raises(QueryDecisionError, match="threshold was already used"):
        _confirm(root, batch_a, "t-1", "eval-4", [BatchRun(batch_c)])


def test_a_changed_report_or_a_report_without_its_record_is_refused(setup):
    root, batch_a, _, evaluation, first = _confirmed(setup)
    first.report_path.write_bytes(first.report_path.read_bytes() + b" ")
    with pytest.raises(QueryDecisionError, match="stored acceptance report differs"):
        _confirm(root, batch_a)
    content = evaluation_content_sha256(evaluation.members)
    (root / "consumption" / f"{content}.json").unlink()
    with pytest.raises(QueryDecisionError, match="report exists without its consumption record"):
        _confirm(root, batch_a)


def test_an_interrupted_write_blocks_later_writes_until_inspected(setup):
    root, batch_a, batch_b = setup
    write_partitions(root, batch_a, batch_b)
    _freeze(root, T0 - timedelta(hours=1))
    run(batch_a, A_SCRIPT)
    (root / ".freeze-staging-left-behind").mkdir()
    with pytest.raises(Exception, match="staging"):
        _confirm(root, batch_a)
    assert not (root / "consumption").exists() and not (root / "acceptance").exists()
