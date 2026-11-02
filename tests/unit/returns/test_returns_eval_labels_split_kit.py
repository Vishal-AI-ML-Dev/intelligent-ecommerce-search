"""M19 Part 3 step A: label contract, seeded split and blind kit.

Every label here is a TEST FIXTURE written to `tmp_path`, never to the real label sheets, and
none of them is a human judgement. The real split, kits and blank sheets are only read."""

import csv
import io
import json
import re
from pathlib import Path

import pytest
from evals.returns import kit, labels, source, split
from evals.returns.cli import main

ROOT = Path(__file__).resolve().parents[3]
BUNDLE = ROOT / "data" / "seed" / "returns_v1" / "bundle" / "returns_v1.json"
LABELLING = ROOT / "evals" / "returns" / "labelling"
REAL_SPLIT = LABELLING / "return-reason-split-1.json"


@pytest.fixture(scope="module")
def bundle():
    return source.load_bundle(BUNDLE)


def sheet(rows, header=labels.COLUMNS, bom=False) -> bytes:
    """A TEST FIXTURE label sheet; rows are dicts of the cells to fill."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(header)
    for n, row in enumerate(rows, start=1):
        writer.writerow([row.get(c, str(n) if c == "item" else "") for c in header])
    return (("﻿" if bom else "") + out.getvalue()).encode("utf-8")


IDS = frozenset({"R1", "R2", "R3"})


# ---- label contract ------------------------------------------------------------------------


def test_fixture_labels_parse_and_unlabelled_rows_are_skipped():
    data = sheet(
        [
            {"return_id": "R1", "sizing_fit": "1", "logistics_delay": "1", "annotator": "fx-1"},
            {"return_id": "R2"},
            {"return_id": "R3", "defect_kind_undetermined": "1", "annotator": "fx-1", "note": "n"},
        ],
        bom=True,
    )
    parsed = labels.parse_labels(data, IDS)
    assert set(parsed) == {"R1", "R3"}
    assert parsed["R1"].tokens == {"sizing_fit", "logistics_delay"}
    assert parsed["R3"].note == "n"


@pytest.mark.parametrize(
    ("row", "fragment"),
    [
        ({"return_id": "R1", "sizing_fit": "yes", "annotator": "fx-1"}, "must be 1 or empty"),
        (
            {"return_id": "R1", "insufficient_evidence": "1", "sizing_fit": "1", "annotator": "a"},
            "must stand alone",
        ),
        (
            {
                "return_id": "R1",
                "defect_kind_undetermined": "1",
                "manufacturing_defect": "1",
                "annotator": "a",
            },
            "cannot be combined",
        ),
        ({"return_id": "R1", "sizing_fit": "1"}, "annotator"),
        ({"return_id": "R1", "sizing_fit": "1", "annotator": "Not Pseudonymous"}, "annotator"),
        ({"return_id": "R9", "sizing_fit": "1", "annotator": "a"}, "not a return of this set"),
        ({"return_id": "R1", "note": "orphan note"}, "unlabelled row"),
        ({"return_id": "R1", "sizing_fit": "1", "annotator": "a", "note": "x" * 501}, "note"),
        ({"return_id": "R1", "item": "first", "sizing_fit": "1", "annotator": "a"}, "item"),
    ],
)
def test_invalid_fixture_rows_are_rejected_with_safe_messages(row, fragment):
    with pytest.raises(labels.LabelError) as caught:
        labels.parse_labels(sheet([row]), IDS)
    message = str(caught.value)
    assert fragment in message
    for value in row.values():  # no cell content is echoed
        if value not in ("1", "", "a", "R1") and len(value) > 1:
            assert value not in message


def test_header_duplicates_and_non_utf8_are_rejected():
    with pytest.raises(labels.LabelError, match="header"):
        labels.parse_labels(sheet([], header=labels.COLUMNS[:-1]), IDS)
    dup = sheet([{"return_id": "R1"}, {"return_id": "R1"}])
    with pytest.raises(labels.LabelError, match="duplicate"):
        labels.parse_labels(dup, IDS)
    with pytest.raises(labels.LabelError, match="UTF-8"):
        labels.parse_labels(b"\xff\xfe\x00", IDS)


def test_the_template_is_blank_and_never_overwrites(tmp_path):
    path = tmp_path / "fixture-labels.csv"
    labels.write_template(path, ["R2", "R1"])
    assert labels.parse_labels(path.read_bytes(), IDS) == {}
    rows = list(csv.reader(path.open(encoding="utf-8")))
    assert rows[1][:2] == ["1", "R2"] and set(rows[1][2:]) == {""}
    with pytest.raises(FileExistsError):
        labels.write_template(path, ["R1"])


# ---- split ---------------------------------------------------------------------------------


def test_the_split_is_seeded_stratified_disjoint_and_complete(bundle):
    result = split.make_split(bundle)
    assert result == split.make_split(bundle)  # deterministic
    assert result["sizes"] == {"dev": 60, "test": 60, "unlabelled": 60}
    assert (result["split_version"], result["seed"]) == ("return-reason-split-1", 20261102)
    sets = {k: set(v) for k, v in result["sets"].items()}
    assert not (sets["dev"] & sets["test"]) and not (sets["dev"] & sets["unlabelled"])
    assert set().union(*sets.values()) == {i.return_id for i in bundle.items}
    for counts in result["per_stratum"].values():
        assert abs(counts["dev"] - counts["test"]) <= 1
    assert split.make_split(bundle, seed=1)["sets"] != result["sets"]


def test_the_recorded_split_matches_the_bundle(bundle):
    split.check_split(json.loads(REAL_SPLIT.read_text(encoding="utf-8")), bundle)


def test_the_bundle_loader_refuses_scenario_files_and_non_bundles(tmp_path):
    with pytest.raises(source.SourceError, match="never read"):
        source.load_bundle(BUNDLE.parent.parent / "scenarios.json")
    other = tmp_path / "other.json"
    other.write_text('{"contract_version": "something-else"}')
    with pytest.raises(source.SourceError, match="not a return-data-v1 bundle"):
        source.load_bundle(other)


# ---- blind kit -----------------------------------------------------------------------------


def test_the_kit_is_offline_escaped_and_blind(bundle):
    recorded = split.make_split(bundle)
    by_id = {i.return_id: i for i in bundle.items}
    order = recorded["presentation_order"]["dev"]
    html = kit.render_kit("dev", [by_id[r] for r in order], "dev-labels.csv")
    assert "default-src 'none'" in html
    assert not re.search(r"<script|<link|<img|<iframe|\bsrc=\"|\bhref=", html, re.I)
    assert all(rid in html for rid in order)
    for leaked in ("tier", "prediction", "scenario", "baseline", "pattern", "intent", "SYN-OI-"):
        assert leaked not in html.lower() if leaked.islower() else leaked not in html
    raw = json.loads(BUNDLE.read_text(encoding="utf-8"))
    product_ids = {r["product_id"] for r in raw["order_items"]}
    assert not any(pid in html for pid in product_ids)
    assert "&lt;" in html or all("<" not in s.text for i in bundle.items for s in i.comments)


def test_the_real_kits_and_sheets_hold_no_labels(bundle):
    recorded = json.loads(REAL_SPLIT.read_text(encoding="utf-8"))
    for name in ("dev", "test"):
        ids = frozenset(recorded["sets"][name])
        data = (LABELLING / f"{name}-labels.csv").read_bytes()
        assert labels.parse_labels(data, ids) == {}  # blank until the owner labels
        html = (LABELLING / f"{name}-kit.html").read_text(encoding="utf-8")
        assert "default-src 'none'" in html and "<script" not in html.lower()


def test_cli_split_and_kit_refuse_to_overwrite(tmp_path, capsys):
    out = tmp_path / "split.json"
    assert main(["split", "--bundle", str(BUNDLE), "--out", str(out)]) == 0
    assert main(["split", "--bundle", str(BUNDLE), "--out", str(out)]) == 2
    args = ["kit", "--bundle", str(BUNDLE), "--split", str(out), "--out-dir", str(tmp_path / "k")]
    assert main(args) == 0
    assert main(args) == 2
    assert "refusing to overwrite" in capsys.readouterr().err
