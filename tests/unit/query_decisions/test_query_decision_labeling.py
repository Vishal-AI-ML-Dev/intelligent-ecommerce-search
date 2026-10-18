"""Blind labelling and sealing (synthetic fixtures only; scripted input, no real annotator)."""

import ast
from datetime import timedelta
from pathlib import Path

import pytest
from evals.query_decisions import labeling
from evals.query_decisions.contracts import GoldKind, QueryDecisionError
from evals.query_decisions.labeling import (
    LABELS_FILE,
    LOCK_FILE,
    QUERIES_FILE,
    label_session,
    load_sealed,
    read_labels,
    read_queries,
    seal_labels,
)
from query_decision_support import (
    FIXTURE_HANDLE,
    FIXTURE_ROWS,
    T0,
    write_fixture_labels,
    write_fixture_queries,
)

from ecommerce_search import query_understanding
from ecommerce_search.query_understanding import parser

ROWS = FIXTURE_ROWS[:2]


def _script(*answers: str):
    remaining = list(answers)

    def ask(prompt: str) -> str:
        if not remaining:
            raise EOFError
        return remaining.pop(0)

    return ask


def _session(work_dir: Path, *answers: str, annotator: str = FIXTURE_HANDLE):
    shown: list[str] = []
    saved = label_session(
        work_dir, annotator, ask=_script(*answers), say=shown.append, now=lambda: T0
    )
    return saved, shown


def test_the_labelling_module_imports_no_parser_provider_runner_or_evaluator():
    tree = ast.parse(Path(labeling.__file__).read_text(encoding="utf-8"))
    imported = {
        node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module
    } | {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    forbidden = (
        "ecommerce_search.query_understanding",
        "ecommerce_search.decision",
        "evals.query_decisions.runner",
        "evals.query_decisions.evaluate",
    )
    assert not [name for name in imported if name.startswith(forbidden)]


def test_the_labelling_view_shows_only_id_progress_and_query_text(tmp_path, monkeypatch):
    write_fixture_queries(tmp_path, ROWS)

    def refuse(*args, **kwargs):
        raise AssertionError("the labelling path must never parse the query")

    monkeypatch.setattr(query_understanding, "parse_normalized_query", refuse)
    monkeypatch.setattr(parser, "parse_normalized_query", refuse)

    saved, shown = _session(tmp_path, "laptop", "none", "", "y", "laptop", "gaming", "", "y")

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
    labels = read_labels(tmp_path, read_queries(tmp_path))
    assert [label.labelling_view for label in labels] == ["query_text_only"] * 2
    assert [label.annotator_handle for label in labels] == [FIXTURE_HANDLE] * 2
    assert labels[1].semantic_intent.label == "gaming"


def test_quit_saves_nothing_partial_and_a_later_session_resumes(tmp_path):
    write_fixture_queries(tmp_path, ROWS)
    saved, _ = _session(tmp_path, "laptop", "none", "", "y", "laptop", "q")
    assert saved == 1
    assert not (tmp_path / LOCK_FILE).exists()

    saved, shown = _session(tmp_path, "ambiguous:phone,laptop", "multi:gaming,student", "", "y")
    assert saved == 1
    assert "[2/2] dq-002" in shown
    second = read_labels(tmp_path, read_queries(tmp_path))[1]
    assert (second.category.kind, second.category.candidates) == (
        GoldKind.AMBIGUOUS,
        ("laptop", "phone"),
    )


def test_unparsable_answers_are_asked_again_and_n_does_not_save(tmp_path):
    write_fixture_queries(tmp_path, ROWS[:1])
    saved, shown = _session(tmp_path, "tablet", "laptop", "none", "", "n")
    assert saved == 0
    assert "  not understood; try again" in shown
    assert not (tmp_path / LABELS_FILE).exists()


def test_a_second_annotator_cannot_write_into_the_first_annotators_labels(tmp_path):
    write_fixture_queries(tmp_path, ROWS)
    _session(tmp_path, "laptop", "none", "", "y", "q")
    with pytest.raises(QueryDecisionError, match="another annotator"):
        _session(tmp_path, "laptop", "none", "", "y", annotator="ann-8")


def test_an_existing_lock_refuses_the_session(tmp_path):
    write_fixture_queries(tmp_path, ROWS)
    (tmp_path / LOCK_FILE).write_text("pid=1\n", encoding="utf-8")
    with pytest.raises(QueryDecisionError, match="label.lock"):
        _session(tmp_path, "laptop")


def test_seal_needs_every_query_labelled(tmp_path):
    write_fixture_queries(tmp_path, ROWS)
    _session(tmp_path, "laptop", "none", "", "y", "q")
    with pytest.raises(QueryDecisionError, match="every query"):
        seal_labels(tmp_path)


def test_seal_is_written_once_and_freezes_labels(tmp_path):
    write_fixture_queries(tmp_path)
    write_fixture_labels(tmp_path)
    seal = seal_labels(tmp_path, now=lambda: T0 + timedelta(hours=1))
    assert seal.synthetic is True
    assert seal.evidence_scope == "development_diagnostic_not_held_out"
    assert seal.query_count == len(FIXTURE_ROWS)

    with pytest.raises(QueryDecisionError):
        seal_labels(tmp_path)
    with pytest.raises(QueryDecisionError, match="sealed"):
        _session(tmp_path, "laptop")


def test_changes_after_sealing_are_refused(sealed_dir):
    load_sealed(sealed_dir)
    queries = sealed_dir / QUERIES_FILE
    queries.write_text(queries.read_text(encoding="utf-8").replace("refrigerator", "fridge"))
    with pytest.raises(QueryDecisionError):
        load_sealed(sealed_dir)


def test_an_invalid_handle_is_refused_before_the_lock_or_any_prompt(tmp_path):
    write_fixture_queries(tmp_path, ROWS)

    def never(prompt):
        raise AssertionError("no prompt may be shown for an invalid handle")

    with pytest.raises(QueryDecisionError, match="invalid annotator handle"):
        label_session(tmp_path, "alice", ask=never, say=lambda _: None, now=lambda: T0)
    assert not (tmp_path / LOCK_FILE).exists()
    assert not (tmp_path / LABELS_FILE).exists()


def test_an_invalid_note_is_reported_as_a_note_error_and_not_saved(tmp_path):
    write_fixture_queries(tmp_path, ROWS[:1])
    saved, shown = _session(tmp_path, "laptop", "none", r"see C:\private\notes", "y")
    assert saved == 0
    assert "  the note is not acceptable free text; not saved, the query stays unlabelled" in shown
    assert not (tmp_path / LABELS_FILE).exists()
