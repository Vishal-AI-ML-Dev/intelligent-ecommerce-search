"""Category-only annotation (`qd-cat-1`): labelling, resume, seal, load and intent exclusion.

Every query and label is a SYNTHETIC TEST FIXTURE answered by a scripted session (no real
annotator), and every Jev outcome is fabricated for software tests.
"""

import builtins
import json
from datetime import timedelta
from pathlib import Path

import pytest
from evals.contracts.canonical import encode_json_object, encode_jsonl
from evals.query_decisions.cli import main
from evals.query_decisions.contracts import (
    DATASET_ID,
    AnnotationMode,
    CategoryGold,
    CategoryOnlyLabel,
    CategoryOnlySeal,
    DecisionLabel,
    LabelSeal,
)
from evals.query_decisions.evaluate import evaluate
from evals.query_decisions.labeling import (
    CATEGORY_HELP,
    INTENT_HELP,
    LABELS_FILE,
    SEAL_FILE,
    label_session,
    load_sealed,
    read_labels,
    read_queries,
    seal_labels,
)
from evals.query_decisions.partitions import (
    BatchRun,
    PartitionRole,
    SourceSelection,
    build_partition,
    resolve_members,
)
from evals.query_decisions.reporting import build_report
from pydantic import ValidationError
from query_decision_support import (
    FIXTURE_HANDLE,
    FIXTURE_ROWS,
    T0,
    make_sealed_dir,
    outcome,
    run,
    write_fixture_queries,
)

from ecommerce_search import query_understanding
from ecommerce_search.query_understanding import parser

SCRIPT = [
    outcome("laptop", "none"),
    outcome("phone", "office"),
    outcome("headphones", "none"),
    outcome("none", "gaming"),
    outcome("phone", "student"),
    outcome("laptop", "none"),
]
QD2_LABEL_KEYS = {
    "annotator_handle",
    "category",
    "dataset_id",
    "labelled_at",
    "labelling_view",
    "note",
    "queries_sha256",
    "query_id",
    "schema_version",
    "semantic_intent",
}


class Script:
    """Scripted answers; records every prompt it was asked."""

    def __init__(self, *answers: str):
        self.remaining = list(answers)
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if not self.remaining:
            raise EOFError
        return self.remaining.pop(0)


def category_answers(rows=FIXTURE_ROWS) -> list[str]:
    return [answer for _, _, category, _ in rows for answer in (category, "", "y")]


def session(work_dir: Path, *answers: str, category_only: bool = True):
    ask = Script(*answers)
    shown: list[str] = []
    saved = label_session(
        work_dir,
        FIXTURE_HANDLE,
        ask=ask,
        say=shown.append,
        now=lambda: T0,
        category_only=category_only,
    )
    return saved, shown, ask.prompts


def category_only_dir(path: Path) -> Path:
    path.mkdir()
    write_fixture_queries(path)
    session(path, *category_answers())
    seal_labels(path, now=lambda: T0 + timedelta(hours=1))
    return path


def test_the_category_only_view_asks_only_category_note_and_save(tmp_path, monkeypatch):
    write_fixture_queries(tmp_path, FIXTURE_ROWS[:2])

    def refuse(*args, **kwargs):
        raise AssertionError("the labelling path must never parse the query")

    monkeypatch.setattr(query_understanding, "parse_normalized_query", refuse)
    monkeypatch.setattr(parser, "parse_normalized_query", refuse)

    saved, shown, prompts = session(tmp_path, "laptop", "", "y", "ambiguous:phone,laptop", "", "y")

    assert saved == 2
    assert shown == [
        "0 of 2 labelled. Type q at any prompt to stop.",
        "",
        "[1/2] dq-001",
        "  hp laptop 16gb ram",
        "",
        "[2/2] dq-002",
        "  gaming laptop under 70k",
    ]
    category_prompt = f"{CATEGORY_HELP}\n> "
    assert prompts == [category_prompt, "note (optional, Enter for none): ", "save? [y/n]: "] * 2
    assert not any(INTENT_HELP in prompt for prompt in prompts)
    labels = read_labels(tmp_path, read_queries(tmp_path))
    assert all(isinstance(label, CategoryOnlyLabel) for label in labels)
    assert [label.intent_gold for label in labels] == [None, None]
    assert labels[1].category.candidates == ("laptop", "phone")


def test_intent_is_stored_as_the_explicit_not_annotated_marker(tmp_path):
    write_fixture_queries(tmp_path, FIXTURE_ROWS[:1])
    session(tmp_path, "none", "", "y")
    record = json.loads((tmp_path / LABELS_FILE).read_text(encoding="utf-8"))
    assert record["schema_version"] == "qd-cat-1"
    assert record["annotation_mode"] == "category_only"
    assert record["semantic_intent"] == "not_annotated"
    assert record["category"] == {"candidates": [], "kind": "label", "label": "none"}


def test_the_category_only_contract_refuses_any_substituted_intent():
    label = dict(
        schema_version="qd-cat-1",
        dataset_id=DATASET_ID,
        query_id="dq-001",
        queries_sha256="a" * 64,
        annotator_handle=FIXTURE_HANDLE,
        labelled_at=T0,
        labelling_view="query_text_only",
        annotation_mode="category_only",
        category=CategoryGold(kind="label", label="laptop"),
    )
    for intent in ("none", "unclear", {"kind": "label", "label": "none"}, None):
        with pytest.raises(ValidationError):
            CategoryOnlyLabel(**label, semantic_intent=intent)
    with pytest.raises(ValidationError):  # a qd-2 label must carry a real intent gold
        DecisionLabel(**{**label, "schema_version": "qd-2"}, semantic_intent="not_annotated")


def test_quit_and_resume_in_the_same_mode(tmp_path):
    write_fixture_queries(tmp_path, FIXTURE_ROWS[:2])
    saved, _, _ = session(tmp_path, "laptop", "", "y", "q")
    assert saved == 1
    saved, shown, _ = session(tmp_path, "laptop", "", "y")
    assert saved == 1
    assert shown[0] == "1 of 2 labelled. Type q at any prompt to stop."
    assert "[2/2] dq-002" in shown


@pytest.mark.parametrize("first_category_only", [True, False])
def test_resuming_in_the_other_mode_is_refused_before_any_prompt(tmp_path, first_category_only):
    write_fixture_queries(tmp_path, FIXTURE_ROWS[:2])
    first = ["laptop", "", "y"] if first_category_only else ["laptop", "none", "", "y"]
    session(tmp_path, *first, "q", category_only=first_category_only)
    before = (tmp_path / LABELS_FILE).read_bytes()

    with pytest.raises(Exception, match="resume it in that mode") as refused:
        session(tmp_path, "laptop", "gaming", "", "y", category_only=not first_category_only)

    assert type(refused.value).__name__ == "QueryDecisionError"
    assert (tmp_path / LABELS_FILE).read_bytes() == before
    assert not (tmp_path / "label.lock").exists()


def test_a_label_file_mixing_modes_is_refused(tmp_path):
    write_fixture_queries(tmp_path, FIXTURE_ROWS[:2])
    session(tmp_path, "laptop", "none", "", "y", "q", category_only=False)
    full = read_labels(tmp_path, read_queries(tmp_path))[0]
    category_only = CategoryOnlyLabel(
        schema_version="qd-cat-1",
        annotation_mode=AnnotationMode.CATEGORY_ONLY,
        semantic_intent="not_annotated",
        **{**full.model_dump(exclude={"schema_version", "semantic_intent"}), "query_id": "dq-002"},
    )
    (tmp_path / LABELS_FILE).write_bytes(encode_jsonl([full, category_only]))
    with pytest.raises(Exception, match="mixes annotation modes"):
        read_labels(tmp_path, read_queries(tmp_path))


def test_seal_records_the_mode_and_load_binds_it(tmp_path):
    work_dir = category_only_dir(tmp_path / "cat")
    seal_bytes = (work_dir / SEAL_FILE).read_bytes()
    seal, queries, labels = load_sealed(work_dir)

    assert isinstance(seal, CategoryOnlySeal)
    assert seal.schema_version == "qd-cat-1"
    assert seal.annotation_mode is AnnotationMode.CATEGORY_ONLY
    assert json.loads(seal_bytes)["annotation_mode"] == "category_only"
    assert len(queries) == len(labels) == len(FIXTURE_ROWS)

    # A qd-2 seal with the same hashes would claim intent was annotated: refused.
    relabelled = LabelSeal(
        schema_version="qd-2", **seal.model_dump(exclude={"schema_version", "annotation_mode"})
    )
    (work_dir / SEAL_FILE).write_bytes(encode_json_object(relabelled))
    with pytest.raises(Exception, match="annotation mode does not match"):
        load_sealed(work_dir)
    (work_dir / SEAL_FILE).write_bytes(seal_bytes)
    with pytest.raises(Exception, match="already sealed"):
        seal_labels(work_dir)
    with pytest.raises(Exception, match="sealed"):
        session(work_dir, "laptop", "", "y")


def test_existing_full_labels_and_seals_keep_their_qd2_bytes(sealed_dir):
    seal, _, labels = load_sealed(sealed_dir)
    assert (
        isinstance(seal, LabelSeal) and seal.annotation_mode is AnnotationMode.CATEGORY_AND_INTENT
    )
    assert all(isinstance(label, DecisionLabel) for label in labels)
    lines = (sealed_dir / LABELS_FILE).read_text(encoding="utf-8").splitlines()
    assert all(set(json.loads(line)) == QD2_LABEL_KEYS for line in lines)
    sealed = json.loads((sealed_dir / SEAL_FILE).read_text(encoding="utf-8"))
    assert sealed["schema_version"] == "qd-2" and "annotation_mode" not in sealed
    assert [label.intent_gold for label in labels] == [label.semantic_intent for label in labels]


def test_evaluate_excludes_unannotated_intent_and_keeps_category_scoring(tmp_path):
    full = make_sealed_dir(_mkdir(tmp_path / "full"))
    category_only = category_only_dir(tmp_path / "cat")
    run(full, SCRIPT)
    run(category_only, SCRIPT)

    full_report = evaluate(full, "r1")
    report = evaluate(category_only, "r1")

    assert "annotation_mode" not in full_report
    assert report["annotation_mode"] == "category_only"
    assert report["questions"]["category"] == full_report["questions"]["category"]
    for row, full_row in zip(report["per_query"], full_report["per_query"], strict=True):
        assert row["category"] == full_row["category"]
    intent = report["questions"]["semantic_intent"]
    assert intent["gold_kinds"] == {"not_annotated": len(FIXTURE_ROWS)}
    assert intent["jev_outcomes"] == {"excluded_not_annotated": len(FIXTURE_ROWS)}
    assert intent["deterministic_outcomes"] == {"excluded_not_annotated": len(FIXTURE_ROWS)}
    assert intent["deterministic_abstains_scored"] == 0
    assert intent["correct_fills"] == intent["incorrect_fills"] == 0


def test_report_counts_intent_as_not_annotated(tmp_path):
    full = make_sealed_dir(_mkdir(tmp_path / "full"))
    category_only = category_only_dir(tmp_path / "cat")
    run(full, SCRIPT)
    run(category_only, SCRIPT)

    full_report = build_report(full, "r1")
    report = build_report(category_only, "r1")

    assert "annotation_mode" not in full_report
    assert "not_annotated_excluded" not in full_report["intent"]["jev"]
    assert report["annotation_mode"] == "category_only"
    assert report["category"] == full_report["category"]
    assert report["category_probability"] == full_report["category_probability"]
    for predictor in ("deterministic", "jev"):
        intent = report["intent"][predictor]
        assert intent["not_annotated_excluded"] == len(FIXTURE_ROWS)
        assert intent["members"] == len(FIXTURE_ROWS)
        assert intent["outcomes"] == {}
        assert intent["unclear_excluded"] == 0
        assert intent["single_intent"]["n"] == 0
        assert intent["single_intent"]["accuracy"] is None
        assert intent["macro_f1"] is None


def test_partitions_resolve_category_only_members(tmp_path):
    work_dir = category_only_dir(tmp_path / "cat")
    run(work_dir, SCRIPT)
    ids = tuple(row[0] for row in FIXTURE_ROWS)
    seal = build_partition(
        "cal-1",
        PartitionRole.CALIBRATION,
        [SourceSelection(work_dir, ids)],
        screening="none",
        now=T0 + timedelta(hours=2),
    )
    records = resolve_members(seal, [BatchRun(work_dir, "r1")])
    assert [record.intent_gold for record in records] == [None] * len(ids)
    assert [record.category_gold.kind.value for record in records] == [
        "label",
        "label",
        "label",
        "label",
        "ambiguous",
        "unclear",
    ]


def test_the_cli_labels_and_seals_in_category_only_mode(tmp_path, monkeypatch, capsys):
    write_fixture_queries(tmp_path, FIXTURE_ROWS[:2])
    ask = Script("laptop", "", "y", "headphones", "", "y")
    monkeypatch.setattr(builtins, "input", ask)
    argv = ["--work-dir", str(tmp_path), "label", "--annotator", FIXTURE_HANDLE, "--category-only"]
    assert main(argv) == 0
    assert not any(INTENT_HELP in prompt for prompt in ask.prompts)
    assert main(["--work-dir", str(tmp_path), "seal"]) == 0
    assert "sealed 2 queries (category_only)" in capsys.readouterr().out
    assert isinstance(load_sealed(tmp_path)[0], CategoryOnlySeal)


def _mkdir(path: Path) -> Path:
    path.mkdir()
    return path
