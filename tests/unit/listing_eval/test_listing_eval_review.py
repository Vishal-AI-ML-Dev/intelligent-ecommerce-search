"""Blinded human-review path (`lh-1`): frozen selection, blind packet, answer import and seal,
scoring. Every answer here is a SYNTHETIC test fixture typed by the test, never a human review."""

import json

import pytest
from evals.listing_decisions import review as rv
from evals.listing_decisions.contracts import EvalSetError, load_set
from listing_eval_support import (
    CORRUPT_COLOR,
    ITEMS,
    default_outcomes,
    outcome,
    write_fixture,
    write_outcomes,
)


@pytest.fixture
def review(tmp_path):
    set_path, raw_dir = write_fixture(tmp_path)
    eval_set = load_set(set_path)
    review_dir = tmp_path / "review" / "hr-1"
    selection, _ = rv.freeze(eval_set, review_dir, "hr-1", seed=11, per_source=5)
    rv.write_packet(eval_set, raw_dir, review_dir)
    return eval_set, review_dir, selection


def _answers(review_dir, handle="ann-1", **overrides) -> list[dict]:
    items, _ = rv.load_packet(review_dir)
    rows = []
    for p in items:
        row = {
            "packet_id": p.packet_id,
            "annotator_handle": handle,
            "rubric_version": rv.RUBRIC_VERSION,
            "category": "shoes",
            "subcategory": "casual",
            "claims": {str(c.ordinal): "supported" for c in p.claims},
            "needs_review": "no",
        }
        rows.append(row | overrides.get(p.packet_id, {}))
    return rows


def _write(path, rows) -> object:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return path


# ---- selection and packet -----------------------------------------------------------------


def test_the_selection_is_seeded_from_the_set_alone_and_frozen(tmp_path, review) -> None:
    eval_set, review_dir, selection = review
    clean_dev = {
        i for i, (_, _, _, split, _, twin, _) in ITEMS.items() if split == "dev" and twin is None
    }
    assert {item_id for _, item_id in selection.items} == clean_dev  # per_source exceeds pools
    assert rv.select(eval_set, "hr-1", 11, 5) == selection  # reproducible
    assert rv.select(eval_set, "hr-1", 12, 5) != selection  # the order depends on the seed
    capped = rv.select(eval_set, "hr-2", 11, 1)
    assert len(capped.items) == len(eval_set.sources)
    with pytest.raises(FileExistsError):
        rv.freeze(eval_set, review_dir, "hr-1", seed=11, per_source=5)


def test_the_packet_is_blind_to_source_labels_ids_and_model_output(review) -> None:
    eval_set, review_dir, selection = review
    text = (review_dir / rv.PACKET_FILE).read_text(encoding="utf-8")
    for item in eval_set.items:
        assert item.item_id not in text and item.source_row_id not in text
    for forbidden in ("flipkart", "laptop_price", "smartphones", "mapped", "label_kind", "route"):
        assert forbidden not in text
    items, _ = rv.load_packet(review_dir)
    assert [p.packet_id for p in items] == [p for p, _ in selection.items]
    assert set(json.loads(text.splitlines()[0])) == {"packet_id", "title", "description", "claims"}
    with pytest.raises(FileExistsError):
        rv.write_packet(eval_set, review_dir.parent.parent / "raw", review_dir)


# ---- import -------------------------------------------------------------------------------


def test_import_needs_the_human_attestation(tmp_path, review) -> None:
    eval_set, review_dir, _ = review
    path = _write(tmp_path / "a.jsonl", _answers(review_dir))
    with pytest.raises(EvalSetError, match="reviewer-is-human"):
        rv.import_answers(eval_set, review_dir, path, reviewer_is_human=False)
    assert not list(review_dir.glob("seal-*"))


@pytest.mark.parametrize(
    ("mutate", "fragment"),
    [
        (lambda rows: rows[1:], "exactly once"),
        (lambda rows: [*rows, rows[0]], "exactly once"),
        (lambda rows: [rows[0] | {"annotator_handle": "ann-2"}, *rows[1:]], "one reviewer"),
        (lambda rows: [rows[0] | {"category": "toaster"}, *rows[1:]], "invalid answers"),
        (lambda rows: [rows[0] | {"needs_review": "maybe"}, *rows[1:]], "invalid answers"),
        (lambda rows: [rows[0] | {"annotator_handle": "alice"}, *rows[1:]], "invalid answers"),
        (lambda rows: [rows[0] | {"claims": {"999": "supported"}}, *rows[1:]], "claims asked"),
        (lambda rows: [rows[0] | {"extra": 1}, *rows[1:]], "invalid answers"),
    ],
)
def test_invalid_answer_files_are_refused(tmp_path, review, mutate, fragment) -> None:
    eval_set, review_dir, _ = review
    path = _write(tmp_path / "a.jsonl", mutate(_answers(review_dir)))
    with pytest.raises(EvalSetError, match=fragment):
        rv.import_answers(eval_set, review_dir, path, reviewer_is_human=True)
    assert not list(review_dir.glob("seal-*")) and not list(review_dir.glob("labels-*"))


def test_a_valid_file_is_sealed_once_and_tampering_is_refused(tmp_path, review) -> None:
    eval_set, review_dir, _ = review
    path = _write(tmp_path / "a.jsonl", _answers(review_dir))
    seal = rv.import_answers(eval_set, review_dir, path, reviewer_is_human=True)
    assert seal.operator_attests_human_reviewer is True and seal.answers == 7
    assert list(rv.load_sealed(review_dir, eval_set)) == ["ann-1"]
    with pytest.raises(FileExistsError):
        rv.import_answers(eval_set, review_dir, path, reviewer_is_human=True)
    labels = review_dir / "labels-ann-1.jsonl"
    labels.write_bytes(labels.read_bytes().replace(b'"no"', b'"yes"', 1))
    with pytest.raises(EvalSetError, match="does not match"):
        rv.load_sealed(review_dir, eval_set)


# ---- score --------------------------------------------------------------------------------


def test_scoring_excludes_unsure_and_counts_ties_and_fallbacks_apart(tmp_path, review) -> None:
    eval_set, review_dir, selection = review
    packet_of = {item_id: packet_id for packet_id, item_id in selection.items}
    overrides = {
        packet_of["hp-1"]: {"category": "headphones", "subcategory": "over-ear"},
        packet_of["acc-1"]: {"category": rv.UNSURE},
        packet_of["shoe-3"]: {"subcategory": "formal"},
    }
    rows = _answers(review_dir, **overrides)
    rv.import_answers(
        eval_set, review_dir, _write(tmp_path / "a.jsonl", rows), reviewer_is_human=True
    )
    outcomes = default_outcomes() | {"shoe-1-c2": outcome(CORRUPT_COLOR)}
    path = write_outcomes(tmp_path / "o.jsonl", tmp_path / "set.json", outcomes)
    report = rv.score(eval_set, review_dir, path)
    mine = report["by_reviewer"]["ann-1"]
    # 7 clean dev items. acc-1: human unsure (excluded). ph-1: fallback (missing). hp-1: model
    # none (disagree). lp-1: model phone (disagree). shoe-3: model tie (disagree). shoe-1 and
    # shoe-2 agree.
    assert mine["category_vs_model"] == {
        "items": 7,
        "human_unsure": 1,
        "other_missing": 1,
        "scored": 5,
        "agree": 2,
        "agreement": 0.4,
    }
    # Source labels: hp-1 and the three shoes agree; lp-1 and ph-1 do not; acc-1 is unsure.
    source = mine["category_vs_source_label"]
    assert (source["scored"], source["agree"]) == (6, 4)
    assert report["label_basis"].startswith("human review under the operator's attestation")


def test_score_needs_sealed_labels(review) -> None:
    eval_set, review_dir, _ = review
    with pytest.raises(EvalSetError, match="no sealed human labels"):
        rv.score(eval_set, review_dir, None)


def test_cli_round_trip(tmp_path, capsys) -> None:
    write_fixture(tmp_path)
    base = ["--set-dir", str(tmp_path), "--review-id", "hr-cli"]
    assert rv.main(["freeze", *base, "--seed", "3"]) == 0
    assert rv.main(["packet", *base]) == 0
    review_dir = tmp_path / "review" / "hr-cli"
    answers = _write(tmp_path / "a.jsonl", _answers(review_dir))
    assert rv.main(["import", *base, "--answers", str(answers)]) == 2
    assert "reviewer-is-human" in capsys.readouterr().err
    assert rv.main(["import", *base, "--answers", str(answers), "--reviewer-is-human"]) == 0
    assert rv.main(["score", *base, "--out", str(tmp_path / "r.json")]) == 0
    assert json.loads((tmp_path / "r.json").read_text())["reviewers"] == ["ann-1"]
    assert rv.main(["freeze", *base, "--seed", "3"]) == 2  # frozen once
