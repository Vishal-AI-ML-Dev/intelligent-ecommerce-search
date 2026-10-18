"""Offline diagnostic evaluator: scoring rules and counts (synthetic fixtures, scripted outcomes).

The expected deterministic decisions come from `qu-1` on the fixture texts: dq-001 and dq-002 parse
`laptop`, dq-003's misspelled `headfones` parses no category, and dq-004 to dq-006 parse none.
"""

import pytest
from evals.query_decisions.contracts import CATEGORY_LABELS, CategoryGold, IntentGold
from evals.query_decisions.evaluate import evaluate, jev_prediction, score
from evals.query_decisions.labeling import parse_gold
from query_decision_support import outcome, run

from ecommerce_search.decision.provider import CategoryProposal, JevOutcome


@pytest.mark.parametrize(
    ("gold", "prediction", "expected"),
    [
        ("unclear", "laptop", "excluded_unclear"),
        ("laptop", None, "abstain"),
        ("laptop", "laptop", "correct"),
        ("laptop", "none", "predicted_none"),
        ("laptop", "phone", "wrong"),
        ("none", "none", "correct_none"),
        ("none", "laptop", "wrong_commit"),
        ("none", None, "abstain"),
        ("ambiguous:laptop,phone", "none", "predicted_none"),
        ("ambiguous:laptop,phone", "phone", "commit_in_candidates"),
        ("ambiguous:laptop,phone", "shoes", "commit_outside_candidates"),
    ],
)
def test_category_scoring_table(gold, prediction, expected):
    assert score(parse_gold(gold, CategoryGold), prediction) == expected


@pytest.mark.parametrize(
    ("prediction", "expected"),
    [
        ("student", "in_set"),
        ("gaming", "out_of_set"),
        ("none", "predicted_none"),
        (None, "abstain"),
    ],
)
def test_multi_intent_scoring(prediction, expected):
    assert score(parse_gold("multi:student,lightweight", IntentGold), prediction) == expected


def test_jev_abstentions_are_split_by_reason():
    assert jev_prediction(None, False, "category") == (None, "not_attempted")
    assert jev_prediction(None, True, "category") == (None, "unknown")
    assert jev_prediction(outcome(fallback="timeout"), True, "category") == (None, "fallback")
    assert jev_prediction(outcome("none"), True, "category") == ("none", None)
    tied = {label: 0.0 for label in CATEGORY_LABELS} | {"laptop": 0.5, "phone": 0.5}
    tie = outcome().model_copy(
        update={
            "category": CategoryProposal(
                label=None,
                raw_choice="laptop",
                top_labels=("laptop", "phone"),
                provider_probabilities=tied,
                provider_confidence=0.375,
            )
        }
    )
    assert isinstance(tie, JevOutcome)
    assert jev_prediction(tie, True, "category") == (None, "tie")


def test_diagnostic_counts_over_a_scripted_run(sealed_dir):
    script = [
        outcome("laptop", "none"),  # dq-001
        outcome("phone", "gaming"),  # dq-002: disagrees with deterministic laptop
        outcome("headphones", "travel"),  # dq-003: fills the missing category correctly
        outcome("laptop", "none"),  # dq-004: commits where the gold is none
        outcome(fallback="timeout"),  # dq-005
        outcome("laptop", "none"),  # dq-006: unclear gold, excluded
    ]
    run(sealed_dir, script)
    report = evaluate(sealed_dir, "r1")

    assert report["synthetic"] is True
    assert report["evidence_scope"] == "development diagnostic; not held-out evidence"
    assert report["run"]["valid_results"] == 5
    assert report["run"]["fallbacks"] == {"timeout": 1}
    assert report["run"]["input_tokens"] == 500

    category = report["questions"]["category"]
    assert category["gold_kinds"] == {"ambiguous": 1, "label": 3, "none": 1, "unclear": 1}
    assert category["deterministic_outcomes"] == {
        "abstain": 3,
        "correct": 2,
        "excluded_unclear": 1,
    }
    assert category["jev_outcomes"] == {
        "abstain": 1,
        "correct": 2,
        "excluded_unclear": 1,
        "wrong": 1,
        "wrong_commit": 1,
    }
    assert category["jev_abstain_reasons"] == {"fallback": 1}
    assert (category["both_decided"], category["disagreements"]) == (2, 1)
    assert category["disagreement_outcomes"] == {"correct|wrong": 1}
    assert category["disagreement_query_ids"] == ["dq-002"]
    assert category["deterministic_abstains"] == 4
    assert category["jev_on_deterministic_abstains"] == {
        "abstain": 1,
        "correct": 1,
        "excluded_unclear": 1,
        "wrong_commit": 1,
    }
    assert (category["correct_fills"], category["incorrect_fills"]) == (1, 1)

    intent = report["questions"]["semantic_intent"]
    assert intent["gold_kinds"] == {"label": 2, "multi": 1, "none": 2, "unclear": 1}
    assert intent["deterministic_outcomes"]["correct"] == 2  # gaming, travel
    assert intent["jev_outcomes"]["correct_none"] == 2
    assert "per_query" in report and len(report["per_query"]) == 6


def test_report_records_parser_versions_and_evaluation_time_computation(sealed_dir):
    run(sealed_dir, [outcome() for _ in range(6)])
    deterministic = evaluate(sealed_dir, "r1")["deterministic"]
    assert deterministic["computed"] == "at evaluation time, from the sealed query text"
    assert (deterministic["parser_versions"], deterministic["lexicon_versions"]) == (
        ["qu-1"],
        ["1"],
    )
    assert "not proof" in deterministic["note"]
