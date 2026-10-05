"""End of input (EOF) at an annotation prompt ends the session like `q`. Every judgement here
is a SYNTHETIC TEST FIXTURE on a temporary frozen pool; the real work directory is untouched."""

import io
from datetime import datetime, timedelta

import pytest
from evals.annotation import cli
from evals.annotation.session import BINDING_FILE, WorkStore, load_pool
from pool_support import LABEL_T0, frozen_pool

T = LABEL_T0 + timedelta(hours=5)
LATER = T + timedelta(days=1)
ONE_PAIR = ["n", "2", "h", "", "", "", "y"]  # label, relation, confidence, ..., save


@pytest.fixture(scope="module")
def pool(tmp_path_factory, catalog):
    return frozen_pool(tmp_path_factory.mktemp("eof"), catalog)


@pytest.fixture(scope="module")
def context(pool, catalog):
    root, target, _, _ = pool
    return load_pool(target, allowed_root=root, catalog=catalog)


@pytest.fixture
def store(context, tmp_path):
    return WorkStore(context, tmp_path / "work")


def _clock(start):
    ticks = iter(range(1_000_000))
    return lambda: start + timedelta(seconds=next(ticks))


def _then_eof(answers, on_prompt=None):
    """Answer from `answers`, then raise EOFError as `input()` does when stdin ends."""
    queue = list(answers)

    def ask(_prompt):
        if on_prompt is not None:
            on_prompt()
        if not queue:
            raise EOFError
        return queue.pop(0)

    return ask


def _run(context, store, ask, start=T):
    said: list[str] = []
    saved = cli.annotate(
        context, store, annotator="ann-1", ask=ask, say=said.append, now=_clock(start)
    )
    return saved, said


def test_eof_before_the_first_answer_exits_cleanly(context, store):
    saved, said = _run(context, store, _then_eof([]))
    assert saved == 0
    assert said[-1] == cli.INPUT_ENDED_MESSAGE
    assert not store.path.exists()  # no working file, no judgement
    binding = store.read_binding(now=LATER)
    assert binding is not None and T <= binding.created_at < T + timedelta(minutes=1)
    assert store.load(now=LATER) == []
    assert not store.lock_path.exists()


def test_eof_in_the_middle_of_a_pair_saves_only_confirmed_pairs(context, store):
    lock_seen: list[bool] = []
    # One complete pair, then the second pair's label, relation and confidence, then EOF.
    ask = _then_eof(
        [*ONE_PAIR, "b", "1", "m"], on_prompt=lambda: lock_seen.append(store.lock_path.exists())
    )

    saved, said = _run(context, store, ask)
    assert saved == 1 and said[-1] == cli.INPUT_ENDED_MESSAGE
    assert all(lock_seen)  # the lock was held for every prompt
    stored = store.load(now=LATER)
    first = context.items[0]
    assert [(j.query_id, j.product_id) for j in stored] == [(first.query_id, first.product_id)]
    assert not store.lock_path.exists()


def test_eof_after_completed_pairs_resumes_at_the_first_unjudged_pair(context, store):
    saved, _ = _run(context, store, _then_eof(ONE_PAIR * 2))
    assert saved == 2
    stored_bytes = store.path.read_bytes()
    binding_bytes = (store.directory / BINDING_FILE).read_bytes()
    saved, said = _run(context, store, _then_eof([]), start=T + timedelta(hours=1))
    assert saved == 0
    assert said[0].startswith(f"2 of {len(context.items)} pairs judged")
    third = context.items[2]
    assert any(third.product_id in line for line in said)
    assert store.path.read_bytes() == stored_bytes
    assert (store.directory / BINDING_FILE).read_bytes() == binding_bytes
    assert len(store.load(now=LATER)) == 2


def test_q_still_stops_with_its_own_message(context, store):
    saved, said = _run(context, store, _then_eof([*ONE_PAIR, "q"]))
    assert saved == 1
    assert said[-1] == "stopped; 1 judgement(s) saved this session"
    assert cli.INPUT_ENDED_MESSAGE not in said
    assert not store.lock_path.exists()


def test_the_command_line_exits_zero_when_stdin_is_empty(context, store, monkeypatch, capsys):
    class FixedClock(datetime):  # the fixture pool's times lie after the real clock
        @classmethod
        def now(cls, tz=None):
            return T

    monkeypatch.setattr(cli, "load_pool", lambda _golden_dir: context)
    monkeypatch.setattr(cli, "datetime", FixedClock)
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    work_root = str(store.directory.parent)
    assert cli.main(["--work-root", work_root, "annotate", "--annotator", "ann-1"]) == 0
    out = capsys.readouterr().out
    assert cli.INPUT_ENDED_MESSAGE in out
    assert not store.path.exists() and not store.lock_path.exists()
    assert store.read_binding(now=LATER) is not None
