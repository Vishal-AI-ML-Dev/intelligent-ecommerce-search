"""M19 Part 3 step A: the scoring harness on TEST FIXTURE labels.

The labels below are hand-made test fixtures that exercise the documented scoring rules. They
are not human judgements, are never written to the real label sheets, and say nothing about
quality."""

import json

import pytest
from evals.returns import scoring
from evals.returns.cli import main
from evals.returns.labels import COLUMNS, HumanLabel

from ecommerce_search.returns.classification.baseline import classify
from ecommerce_search.returns.classification.contracts import ClassificationInput


def result(rid, code, *texts, inspections=()):
    return classify(
        ClassificationInput(
            return_id=rid,
            customer_reason_code=code,
            customer_statements=[
                {"evidence_type": "return_comment", "evidence_id": f"{rid}-c{i}", "text": t}
                for i, t in enumerate(texts)
            ],
            inspection_observations=[
                {"observation_id": f"{rid}-o{i}", "condition": c} for i, c in enumerate(inspections)
            ],
        )
    )


def fixture_label(rid, *tokens):
    return HumanLabel(rid, frozenset(tokens), "fixture", None)


@pytest.fixture
def case():
    results = {
        "A": result("A", "SIZE_TOO_SMALL"),  # sizing_fit
        "B": result("B", "DEFECTIVE_OR_NOT_WORKING"),  # unresolved defect pair
        "C": result("C", "NOT_PROVIDED"),  # abstains
        "D": result("D", "NOT_AS_DESCRIBED"),  # unresolved description pair
        "E": result("E", "ARRIVED_LATE", "arrived damaged"),  # delay + damaged
    }
    fixture_labels = {
        "A": fixture_label("A", "sizing_fit"),
        "B": fixture_label("B", "defect_kind_undetermined"),
        "C": fixture_label("C", "insufficient_evidence"),
        "D": fixture_label("D", "incorrect_specification"),
        "E": fixture_label("E", "logistics_delay"),
    }
    return results, fixture_labels, frozenset({*results, "F"})


def test_wilson_interval():
    assert scoring.wilson(0, 0) is None
    low, high = scoring.wilson(5, 10)
    assert (low, high) == pytest.approx((0.2366, 0.7634), abs=1e-4)
    assert scoring.wilson(0, 5)[0] == 0.0 and scoring.wilson(5, 5)[1] == 1.0


def test_documented_rules_on_fixture_labels(case):
    results, fixture_labels, ids = case
    report = scoring.score("dev", ids, results, fixture_labels)
    cats = report["categories"]
    assert (cats["sizing_fit"]["tp"], cats["sizing_fit"]["fp"], cats["sizing_fit"]["fn"]) == (
        1,
        0,
        0,
    )
    # an unresolved pair is never a prediction of one kind; defect_kind_undetermined is no kind
    for kind in ("product_quality_defect", "manufacturing_defect"):
        assert (cats[kind]["tp"], cats[kind]["fp"], cats[kind]["fn"]) == (0, 0, 0)
    assert report["concerns"]["defect_any_kind"]["tp"] == 1
    assert cats["incorrect_specification"]["fn"] == 1  # unresolved D is not a spec prediction
    assert report["concerns"]["description_either"]["tp"] == 1
    assert cats["damaged_product"]["fp"] == 1 and cats["logistics_delay"]["tp"] == 1
    assert report["abstention"]["table"]["abstained_and_human_insufficient"] == 1
    # exact: A, B (pair -> defect_kind_undetermined) and C match; D never can; E has extra
    assert report["exact_set_match"]["numerator"] == 3
    assert report["exact_set_match"]["denominator"] == 5
    assert report["coverage"] == scoring.proportion(5, 6)
    assert report["exact_set_match"]["too_few_to_interpret"] is True
    assert report["annotators"] == ["fixture"]
    assert "accuracy" not in json.dumps(report).replace("not real-world accuracy", "").replace(
        "no single headline accuracy", ""
    )


def test_the_test_set_needs_the_final_flag(case):
    results, fixture_labels, ids = case
    with pytest.raises(scoring.ScoringError, match="final"):
        scoring.score("test", ids, results, fixture_labels)
    assert scoring.score("test", ids, results, fixture_labels, final=True)["set"] == "test"


def test_labels_outside_the_set_or_without_results_are_refused(case):
    results, fixture_labels, ids = case
    with pytest.raises(scoring.ScoringError, match="outside"):
        scoring.score("dev", frozenset({"A"}), results, fixture_labels)
    del results["A"]
    with pytest.raises(scoring.ScoringError, match="no baseline result"):
        scoring.score("dev", ids, results, fixture_labels)


def test_cli_scores_a_results_file_and_rejects_bad_fixture_sheets(tmp_path, case, capsys):
    results, _, _ = case
    split_file = tmp_path / "split.json"
    split_file.write_text(
        json.dumps(
            {
                "split_version": "return-reason-split-1",
                "seed": 1,
                "dataset_id": "fixture",
                "dataset_version": 1,
                "sets": {"dev": ["A", "B"], "test": ["C"], "unlabelled": []},
            }
        )
    )
    results_file = tmp_path / "results.json"
    results_file.write_text(json.dumps([r.model_dump(mode="json") for r in results.values()]))
    labels_file = tmp_path / "fixture-labels.csv"
    header = ",".join(COLUMNS)
    blank = [""] * (len(COLUMNS) - 2)

    def row(item, rid, **marks):
        cells = dict(zip(COLUMNS[2:], blank, strict=True)) | marks
        return ",".join([str(item), rid, *cells.values()])

    labels_file.write_text(
        "\n".join([header, row(1, "A", sizing_fit="1", annotator="fixture"), row(2, "B")]) + "\n"
    )
    base = ["--split", str(split_file), "--set", "dev", "--labels", str(labels_file)]
    assert main(["validate", *base]) == 0
    assert json.loads(capsys.readouterr().out) == {"set": "dev", "labelled": 1, "set_size": 2}
    assert main(["score", *base, "--results", str(results_file)]) == 0
    assert json.loads(capsys.readouterr().out)["categories"]["sizing_fit"]["tp"] == 1
    test_args = ["score", "--split", str(split_file), "--set", "test", "--labels"]
    test_labels = tmp_path / "fixture-test.csv"
    test_labels.write_text(header + "\n")
    assert main([*test_args, str(test_labels), "--results", str(results_file)]) == 2
    assert "final" in capsys.readouterr().err
    labels_file.write_text(
        "\n".join([header, row(1, "A", sizing_fit="SECRET-CELL", annotator="fixture")]) + "\n"
    )
    assert main(["validate", *base]) == 1
    assert "SECRET-CELL" not in capsys.readouterr().err
