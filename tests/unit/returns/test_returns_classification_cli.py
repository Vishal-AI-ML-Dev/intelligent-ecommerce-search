"""M19 Part 1 offline CLI: classify a JSON file and print the taxonomy. No database or model."""

import json

from ecommerce_search.returns.classification import cli

INPUTS = [
    {
        "return_id": "demo-1",
        "customer_reason_code": "OTHER",
        "customer_statements": [
            {"evidence_type": "return_comment", "evidence_id": "c1", "text": "size chhota hai"}
        ],
    },
    {"return_id": "demo-2", "customer_reason_code": "NOT_PROVIDED"},
]


def test_classify_prints_one_result_per_input(tmp_path, capsys):
    path = tmp_path / "in.json"
    path.write_text(json.dumps(INPUTS), encoding="utf-8")
    assert cli.main(["classify", str(path)]) == cli.EXIT_OK
    results = json.loads(capsys.readouterr().out)
    assert [r["return_id"] for r in results] == ["demo-1", "demo-2"]
    assert results[0]["predictions"][0]["category"] == "sizing_fit"
    assert results[1]["status"] == "abstained"
    assert results[1]["review_reasons"] == ["insufficient_evidence"]
    assert {r["tier_semantics"] for r in results} == {"rule_tier_not_probability"}


def test_invalid_input_exits_2_without_echoing_values_or_paths(tmp_path, capsys):
    secret_dir = tmp_path / "private-folder"
    secret_dir.mkdir()
    path = secret_dir / "in.json"
    path.write_text(json.dumps([{"return_id": "x", "customer_reason_code": "MYSECRET"}]))
    assert cli.main(["classify", str(path)]) == cli.EXIT_ERROR
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "MYSECRET" not in captured.err and "private-folder" not in captured.err
    assert "not valid" in captured.err


def test_missing_file_exits_2(tmp_path, capsys):
    assert cli.main(["classify", str(tmp_path / "absent.json")]) == cli.EXIT_ERROR
    assert "could not be read" in capsys.readouterr().err


def test_taxonomy_lists_every_category_and_the_unpredicted_ones(capsys):
    assert cli.main(["taxonomy"]) == cli.EXIT_OK
    data = json.loads(capsys.readouterr().out)
    rows = {row["category"]: row for row in data["categories"]}
    assert len(rows) == 12
    assert not rows["suspected_abuse"]["predicted_by_baseline"]
    assert not rows["insufficient_evidence"]["predicted_by_baseline"]
    assert rows["damaged_product"]["suggested_responsible_areas"] == ["packaging", "logistics"]
