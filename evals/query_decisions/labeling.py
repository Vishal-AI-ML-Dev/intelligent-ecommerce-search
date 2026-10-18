"""Blind labelling and sealing of the M12 diagnostic batch (`qd-2`).

Work directory layout (default under the git-ignored `data/processed/m12_diagnostic/`):

* `queries.jsonl`: written by the query author (format in `contracts.DecisionQuery`);
* `labels.jsonl`: canonical JSONL of `DecisionLabel`, written only by the labelling session;
* `label.lock`: held while a session runs;
* `seal.json`: `LabelSeal`, written once with exclusive creation;
* `runs/<run-id>/`: Jev runs (`runner`), which refuse to start before the seal.

The labelling view shows the query id, progress and the query text only. This module never
imports the parser or the Jev provider and never reads a run directory, so no prediction can be
shown. Every confirmed label is saved atomically (temporary file, fsync, replace) before the next
query, and a later session resumes at the first unlabelled query. Once `seal.json` exists, labels
can no longer change. A lock left behind by a hard kill is never removed automatically.
"""

import contextlib
import os
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from evals.contracts.canonical import (
    decode_json_object,
    decode_jsonl,
    encode_json_object,
    encode_jsonl,
    sha256_hex,
)
from evals.contracts.catalog import ROOT
from evals.contracts.models import AnnotatorHandle, FreeText
from evals.query_decisions.contracts import (
    CATEGORY_LABELS,
    DATASET_ID,
    INTENT_LABELS,
    QUESTION_SET_VERSION,
    SCHEMA_VERSION,
    CategoryGold,
    DecisionLabel,
    DecisionQuery,
    GoldKind,
    IntentGold,
    LabelSeal,
    QueryDecisionError,
    QuerySource,
    load_queries,
    queries_sha256,
    source_counts,
)
from evals.runners.golden_io import safe_child, write_exclusive

WORK_ROOT = ROOT / "data" / "processed" / "m12_diagnostic"
QUERIES_FILE = "queries.jsonl"
LABELS_FILE = "labels.jsonl"
LOCK_FILE = "label.lock"
SEAL_FILE = "seal.json"

CATEGORY_HELP = (
    f"category: one of {', '.join(CATEGORY_LABELS)}; or ambiguous:<a>,<b>[,...]; or unclear"
)
INTENT_HELP = (
    f"intent: one of {', '.join(INTENT_LABELS)}; "
    "or multi:<a>,<b>[,...] (several stated); or ambiguous:<a>,<b>[,...]; or unclear"
)


class QuitSession(Exception):
    """The annotator typed `q` or input ended; nothing partial is saved."""


def read_queries(work_dir: Path) -> tuple[DecisionQuery, ...]:
    path = safe_child(work_dir, QUERIES_FILE)
    if not path.is_file():
        raise QueryDecisionError(f"missing {QUERIES_FILE} in the work directory")
    return load_queries(path.read_bytes())


def read_labels(work_dir: Path, queries: tuple[DecisionQuery, ...]) -> tuple[DecisionLabel, ...]:
    path = safe_child(work_dir, LABELS_FILE)
    if not path.is_file():
        return ()
    labels = tuple(decode_jsonl(path.read_bytes(), DecisionLabel))
    _check_labels(labels, queries)
    return labels


def _check_labels(labels: tuple[DecisionLabel, ...], queries: tuple[DecisionQuery, ...]) -> None:
    known = {query.query_id for query in queries}
    digest = queries_sha256(queries)
    handles = {label.annotator_handle for label in labels}
    if len(handles) > 1:
        raise QueryDecisionError("labels.jsonl must hold one annotator's labels")
    for label in labels:
        if label.query_id not in known:
            raise QueryDecisionError(f"label for an unknown query {label.query_id}")
        if label.queries_sha256 != digest:
            raise QueryDecisionError("labels were made for a different queries.jsonl")


def _write_atomic(path: Path, data: bytes) -> None:
    temporary = path.with_name(path.name + ".tmp")
    with open(temporary, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


@contextlib.contextmanager
def label_lock(work_dir: Path) -> Iterator[None]:
    path = safe_child(work_dir, LOCK_FILE)
    try:
        with open(path, "x", encoding="utf-8") as handle:
            handle.write(f"pid={os.getpid()}\n")
    except FileExistsError:
        raise QueryDecisionError(
            f"{LOCK_FILE} exists: another session is running, or one was killed. Check that no "
            "session is running, then delete the lock by hand."
        ) from None
    try:
        yield
    finally:
        path.unlink(missing_ok=True)


def parse_gold[G: (CategoryGold, IntentGold)](text: str, gold_type: type[G]) -> G:
    """`laptop` / `none` → one label; `ambiguous:a,b` and (intent only) `multi:a,b` → candidates
    in label-set order; `unclear`. Raises `ValueError` for anything else."""
    text = text.strip()
    if text == GoldKind.UNCLEAR.value:
        return gold_type(kind=GoldKind.UNCLEAR)
    kind_text, separator, rest = text.partition(":")
    if not separator:
        return gold_type(kind=GoldKind.LABEL, label=text)
    kind = GoldKind(kind_text)
    if kind not in (GoldKind.AMBIGUOUS, GoldKind.MULTI):
        raise ValueError(f"unknown kind {kind_text!r}")
    typed = [part.strip() for part in rest.split(",")]
    if len(set(typed)) != len(typed) or not set(typed) <= set(gold_type.LABELS):
        raise ValueError("candidates must be distinct known labels")
    ordered = tuple(label for label in gold_type.LABELS if label in typed)
    return gold_type(kind=kind, candidates=ordered)


def label_session(
    work_dir: Path,
    annotator: str,
    *,
    ask: Callable[[str], str],
    say: Callable[[str], None],
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> int:
    """Label every unlabelled query, one at a time. Returns how many labels this session saved."""
    try:  # before the lock and before any prompt
        TypeAdapter(AnnotatorHandle).validate_python(annotator)
    except ValidationError:
        raise QueryDecisionError(
            "invalid annotator handle: expected ann-<n> (for example ann-1)"
        ) from None
    if safe_child(work_dir, SEAL_FILE).exists():
        raise QueryDecisionError("the labels are sealed; they can no longer change")
    queries = read_queries(work_dir)
    digest = queries_sha256(queries)
    saved = 0
    with label_lock(work_dir):
        labels = list(read_labels(work_dir, queries))
        if labels and labels[0].annotator_handle != annotator:
            raise QueryDecisionError("labels.jsonl belongs to another annotator")
        done = {label.query_id for label in labels}
        pending = [query for query in queries if query.query_id not in done]
        say(f"{len(done)} of {len(queries)} labelled. Type q at any prompt to stop.")
        for position, query in enumerate(pending, start=len(done) + 1):
            say("")
            say(f"[{position}/{len(queries)}] {query.query_id}")
            say(f"  {query.text}")
            try:
                category = _ask_gold(ask, say, CATEGORY_HELP, CategoryGold)
                intent = _ask_gold(ask, say, INTENT_HELP, IntentGold)
                note = _ask(ask, "note (optional, Enter for none): ") or None
                if note is not None and not _valid_note(note):
                    say(
                        "  the note is not acceptable free text; not saved, the query stays "
                        "unlabelled"
                    )
                    continue
                if _ask(ask, "save? [y/n]: ").lower() != "y":
                    say("  not saved; the query stays unlabelled")
                    continue
            except QuitSession:
                say("stopped; nothing partial was saved")
                break
            label = DecisionLabel(
                schema_version=SCHEMA_VERSION,
                dataset_id=DATASET_ID,
                query_id=query.query_id,
                queries_sha256=digest,
                annotator_handle=annotator,
                labelled_at=now(),
                labelling_view="query_text_only",
                category=category,
                semantic_intent=intent,
                note=note,
            )
            labels.append(label)
            _write_atomic(safe_child(work_dir, LABELS_FILE), encode_jsonl(labels))
            saved += 1
    return saved


def _valid_note(note: str) -> bool:
    try:
        TypeAdapter(FreeText).validate_python(note)
    except ValidationError:
        return False
    return True


def _ask(ask: Callable[[str], str], prompt: str) -> str:
    try:
        answer = ask(prompt).strip()
    except EOFError:
        raise QuitSession() from None
    if answer == "q":
        raise QuitSession()
    return answer


def _ask_gold[G: (CategoryGold, IntentGold)](
    ask: Callable[[str], str], say: Callable[[str], None], help_text: str, gold_type: type[G]
) -> G:
    while True:
        answer = _ask(ask, f"{help_text}\n> ")
        try:
            return parse_gold(answer, gold_type)
        except ValueError:
            say("  not understood; try again")


def seal_labels(
    work_dir: Path, *, now: Callable[[], datetime] = lambda: datetime.now(UTC)
) -> LabelSeal:
    """Seal once every query has exactly one label from the one annotator."""
    if safe_child(work_dir, SEAL_FILE).exists():
        raise QueryDecisionError("the labels are already sealed")
    if safe_child(work_dir, LOCK_FILE).exists():
        raise QueryDecisionError(f"{LOCK_FILE} exists; finish or clear the labelling session")
    queries = read_queries(work_dir)
    labels = read_labels(work_dir, queries)
    if {label.query_id for label in labels} != {query.query_id for query in queries}:
        raise QueryDecisionError("every query needs a label before sealing")
    counts = source_counts(queries)
    seal = LabelSeal(
        schema_version=SCHEMA_VERSION,
        dataset_id=DATASET_ID,
        question_set_version=QUESTION_SET_VERSION,
        queries_sha256=queries_sha256(queries),
        labels_sha256=sha256_hex(encode_jsonl(labels)),
        query_count=len(queries),
        human_authored_queries=counts[QuerySource.HUMAN_AUTHORED],
        ai_authored_queries=counts[QuerySource.AI_AUTHORED],
        synthetic_fixture_queries=counts[QuerySource.SYNTHETIC_TEST_FIXTURE],
        annotator_handle=labels[0].annotator_handle,
        synthetic=counts[QuerySource.SYNTHETIC_TEST_FIXTURE] > 0,
        sealed_at=now(),
        evidence_scope="development_diagnostic_not_held_out",
    )
    write_exclusive(work_dir, {SEAL_FILE: encode_json_object(seal)})
    return seal


def load_sealed(
    work_dir: Path,
) -> tuple[LabelSeal, tuple[DecisionQuery, ...], tuple[DecisionLabel, ...]]:
    """The seal and the exact queries and labels it binds; refuses any change since sealing."""
    path = safe_child(work_dir, SEAL_FILE)
    if not path.is_file():
        raise QueryDecisionError("the labels are not sealed")
    seal = decode_json_object(path.read_bytes(), LabelSeal)
    queries = read_queries(work_dir)
    labels = read_labels(work_dir, queries)
    if queries_sha256(queries) != seal.queries_sha256:
        raise QueryDecisionError("queries.jsonl changed after sealing")
    if sha256_hex(encode_jsonl(labels)) != seal.labels_sha256:
        raise QueryDecisionError("labels.jsonl changed after sealing")
    if seal.counts() != source_counts(queries):
        raise QueryDecisionError("the seal's source counts do not match the sealed queries")
    return seal, queries, labels
