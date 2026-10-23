"""Milestone 17: the class-separated human-review report over `aj-1` judgements (coverage, rates,
provenance groups, partial-entry history, incomplete runs) and its bound CLI inputs.

Judgements here are test data built by the test, not human judgements. No provider is called.
"""

import json
from pathlib import Path

import pytest
from evals.assistant import __main__ as cli
from evals.assistant.judgements import (
    AnswerJudgement,
    human_review_report,
    render_human_review,
)

ROOT = Path(__file__).parents[3]
RUN_002 = ROOT / "evals/results/m17/abe-v1-live-002"
ANNOTATIONS = ROOT / "evals/assistant/drafts/abe-v1/annotations.json"


def judgement(case_id, products=(), outcome="yes", misleading="no"):
    return AnswerJudgement.model_validate(
        {
            "schema_version": "aj-1",
            "report_sha256": "a" * 64,
            "case_id": case_id,
            "reviewer_handle": "ann-1",
            "judged_on": "2026-10-23",
            "outcome_appropriate": outcome,
            "products": [{"product_id": p, "fit": f} for p, f in products],
            "misleading": misleading,
        }
    )


REPORT = {
    "stopped": "provider_failure:timeout",
    "not_run": ["z"],
    "results": [
        {"case_id": "a", "error": None, "recommended": ["P1", "P2"]},
        {"case_id": "b", "error": None, "recommended": ["P3"]},
        {"case_id": "c", "error": None, "recommended": []},
        {"case_id": "d", "error": None, "recommended": []},
        {"case_id": "e", "error": "cap", "recommended": []},
    ],
}
CLASSES = {"a": "scored", "b": "scored", "c": "contract", "d": "diagnostic", "e": "scored",
           "z": "scored"}  # fmt: skip
JUDGED = (
    judgement("a", (("P1", "fits"), ("P2", "partly_fits")), misleading="unsure"),
    judgement("c", outcome="no"),
    judgement("d", outcome="no", misleading="yes"),
)
PROVENANCE = {"a": "not stated by the reviewer", "c": "AI-assisted, explicitly endorsed"}
PENDING = {"entries": [{"case_id": "a", "completed": {}}, {"case_id": "b"}]}


def summary(**overrides):
    args = {"provenance": PROVENANCE, "pending": PENDING, "planned": {"scored": 4, "contract": 1,
            "diagnostic": 1}} | overrides  # fmt: skip
    return human_review_report(JUDGED, REPORT, CLASSES, **args)


def test_every_processed_case_appears_exactly_once_and_views_stay_separate():
    views = summary()["views"]
    scored, both = views["scored_excluding_contract"], views["scored_including_contract"]
    assert scored["processed"] == 3 and scored["judged"] == 1
    assert scored["missing_judgements"] == ["b"] and scored["no_saved_response"] == ["e"]
    assert both["processed"] == 4 and views["contract"]["processed"] == 1
    assert views["diagnostic"]["processed"] == 1
    assert scored["outcome_appropriate"] | {} == {
        "numerator": 1, "denominator": 1, "unsure": 0,
        "definition": "judged yes / judged yes or no",
    }  # fmt: skip
    assert both["outcome_appropriate"]["numerator"] == 1
    assert both["outcome_appropriate"]["denominator"] == 2
    assert scored["not_misleading"]["denominator"] == 0 and scored["not_misleading"]["unsure"] == 1
    assert views["diagnostic"]["not_misleading"]["numerator"] == 0
    products = scored["products"]
    assert (products["recorded"], products["fits"], products["partly_fits"]) == (3, 1, 1)
    assert (products["unsure"], products["in_missing_judgements"]) == (0, 1)
    assert scored["planned_cases"] == 4 and both["planned_cases"] == 5


def test_provenance_groups_are_reported_honestly():
    views = summary()["views"]
    assert views["scored_excluding_contract"]["provenance"] == {"not_stated": 1}
    assert views["contract"]["provenance"] == {"ai_assisted_owner_endorsed": 1}
    assert views["diagnostic"]["provenance"] == {"not_recorded": 1}
    text = render_human_review(summary())
    assert "not independent human validation" in text and "is not 'unaided'" in text


def test_completed_pending_entries_are_history_and_incomplete_runs_get_no_verdict():
    result = summary()
    assert result["partial_entries"] == {"active": ["b"], "history_completed": ["a"]}
    assert result["run_status"] == "incomplete" and result["not_run"] == ["z"]
    assert "run incomplete" in result["verdict"]
    complete = human_review_report(JUDGED, REPORT | {"stopped": None, "not_run": []}, CLASSES)
    assert complete["run_status"] == "complete" and "incomplete" not in complete["verdict"]
    assert complete["verdict"].startswith("none")


def test_duplicate_or_unclassified_cases_are_refused():
    duplicate = REPORT | {"results": [*REPORT["results"], REPORT["results"][0]]}
    with pytest.raises(ValueError, match="more than once"):
        human_review_report(JUDGED, duplicate, CLASSES)
    with pytest.raises(ValueError, match="no class"):
        human_review_report(JUDGED, REPORT, {k: v for k, v in CLASSES.items() if k != "a"})


def test_cli_refuses_sidecars_bound_to_another_run(tmp_path, capsys):
    base = [
        "judgements",
        "--report", str(RUN_002 / "report.json"),
        "--judgements", str(RUN_002 / "judgements-ann-1.aj-1.jsonl"),
        "--annotations", str(ANNOTATIONS),
    ]  # fmt: skip
    other = tmp_path / "provenance.json"
    other.write_text(json.dumps({"report_sha256": "0" * 64, "judgements": {}}), "utf-8")
    assert cli.main([*base, "--provenance", str(other)]) == 2
    assert "different report" in capsys.readouterr().err
    wrong = tmp_path / "annotations.json"
    wrong.write_text("{}", "utf-8")
    assert cli.main([*base[:5], "--annotations", str(wrong)]) == 2


def test_run_002_committed_review_is_reported_by_class(capsys):
    argv = [
        "judgements",
        "--report", str(RUN_002 / "report.json"),
        "--judgements", str(RUN_002 / "judgements-ann-1.aj-1.jsonl"),
        "--annotations", str(ANNOTATIONS),
        "--provenance", str(RUN_002 / "judgements-ann-1.provenance.json"),
        "--pending", str(RUN_002 / "judgements-ann-1.pending.json"),
    ]  # fmt: skip
    assert cli.main(argv) == 0
    result = json.loads(capsys.readouterr().out)
    views = result["views"]
    assert result["processed_cases"] == 23 and len(result["not_run"]) == 17
    assert sum(views[v]["processed"] for v in ("scored_excluding_contract", "contract",
                                                "diagnostic")) == 23  # fmt: skip
    assert views["scored_excluding_contract"]["planned_cases"] == 38
    assert views["scored_excluding_contract"]["outcome_appropriate"]["numerator"] == 17
    assert views["scored_excluding_contract"]["outcome_appropriate"]["denominator"] == 21
    assert result["partial_entries"]["active"] == []
