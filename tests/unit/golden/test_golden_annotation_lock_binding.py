"""Regression tests for the annotation-session lock and the work binding's `created_at` (C2
review findings 1 and 2). Every judgement here is a SYNTHETIC TEST FIXTURE on a temporary
frozen pool; the real working directory is never touched."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from evals.annotation import cli
from evals.annotation.session import (
    BINDING_FILE,
    LOCK_FILE,
    AnnotationError,
    WorkBinding,
    WorkStore,
    annotation_lock,
    load_pool,
)
from evals.contracts.canonical import ContractError, encode_json_object, encode_jsonl
from evals.runners import seal_primary
from pool_support import LABEL_T0, frozen_pool, synthetic_primaries
from pydantic import ValidationError

T = LABEL_T0 + timedelta(hours=5)  # binding creation time used below
LATER = T + timedelta(days=1)
ANSWER_ONE = ["n", "2", "h", "", "", "", "y"]  # one not_relevant / off_target judgement


@pytest.fixture(scope="module")
def pool(tmp_path_factory, catalog):
    return frozen_pool(tmp_path_factory.mktemp("lock-binding"), catalog)


@pytest.fixture(scope="module")
def context(pool, catalog):
    root, target, _, _ = pool
    return load_pool(target, allowed_root=root, catalog=catalog)


@pytest.fixture
def store(context, tmp_path):
    return WorkStore(context, tmp_path / "work")


def _clock(start=T):
    ticks = iter(range(1_000_000))
    return lambda: start + timedelta(seconds=next(ticks))


def _annotate(context, store, ask, *, now=None, say=None):
    return cli.annotate(
        context,
        store,
        annotator="ann-1",
        ask=ask,
        say=say or (lambda _text: None),
        now=now or _clock(),
    )


def _scripted(answers):
    queue = list(answers)
    return lambda _prompt: queue.pop(0)


# ---- finding 1: one annotation session per work directory ---------------------------------


def test_a_second_session_is_refused_while_the_first_keeps_its_judgements(context, store):
    _annotate(context, store, _scripted([*ANSWER_ONE, "q"]))
    first_bytes = store.path.read_bytes()
    outcome = {}
    answers = list(ANSWER_ONE)

    def ask(prompt):
        if "nested" not in outcome:  # while the first session is running, start another one
            other = WorkStore(context, store.directory.parent)
            try:
                _annotate(context, other, _scripted(["q"]), now=_clock(T + timedelta(hours=1)))
            except AnnotationError as exc:
                outcome["nested"] = str(exc)
            assert store.path.read_bytes() == first_bytes  # nothing read-modify-written
        return answers.pop(0) if answers else "q"

    saved = _annotate(context, store, ask, now=_clock(T + timedelta(hours=2)))
    assert "another annotation session holds" in outcome["nested"]
    assert saved == 1
    stored = store.load(now=LATER)
    assert len(stored) == 2  # the first session's earlier judgement and its new one
    assert not store.lock_path.exists()


def test_the_lock_is_taken_before_any_judgement_is_read(context, store, monkeypatch):
    store.directory.mkdir(parents=True)
    store.lock_path.write_text("{}\n", encoding="utf-8")

    def must_not_read(*_args, **_kwargs):
        raise AssertionError("judgements were read while another session held the lock")

    monkeypatch.setattr(WorkStore, "load", must_not_read)
    monkeypatch.setattr(WorkStore, "initialize", must_not_read)
    with pytest.raises(AnnotationError, match="another annotation session"):
        _annotate(context, store, _scripted(["q"]))


def test_normal_exit_removes_the_lock_and_records_safe_metadata(context, store):
    seen = {}

    def ask(prompt):
        seen.setdefault("lock", json.loads(store.lock_path.read_text(encoding="utf-8")))
        return "q"

    _annotate(context, store, ask)
    assert set(seen["lock"]) == {"annotator_handle", "pid", "pool_manifest_sha256", "started_at"}
    assert seen["lock"]["pool_manifest_sha256"] == context.pool_manifest.manifest_sha256
    assert seen["lock"]["annotator_handle"] == "ann-1"
    assert seen["lock"]["started_at"].endswith("Z")
    assert not store.lock_path.exists()


@pytest.mark.parametrize("failure", [ValueError("handled failure"), KeyboardInterrupt()])
def test_a_failure_or_interrupt_removes_the_lock(context, store, failure):
    def ask(_prompt):
        assert store.lock_path.exists()
        raise failure

    with pytest.raises(type(failure)):
        _annotate(context, store, ask)
    assert not store.lock_path.exists()


def test_a_stale_lock_fails_closed_and_is_never_removed(context, store):
    _annotate(context, store, _scripted([*ANSWER_ONE, "q"]))
    before = store.path.read_bytes()
    stale = (
        '{"annotator_handle": "ann-1", "pid": 4242, "started_at": "2026-10-06T10:00:00.000000Z"}\n'
    )
    store.lock_path.write_text(stale, encoding="utf-8")
    with pytest.raises(AnnotationError) as raised:
        _annotate(context, store, _scripted(["q"]))
    message = str(raised.value)
    assert "pid=4242" in message and "delete the lock file by hand" in message
    assert "never removed automatically" in message
    assert store.lock_path.read_text(encoding="utf-8") == stale
    assert store.path.read_bytes() == before
    with pytest.raises(seal_primary.SealError, match="another annotation session"):
        seal_primary.read_primary(store, now=LATER)
    # There is no override flag.
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["annotate", "--annotator", "ann-1", "--force"])


def test_read_only_status_works_while_a_lock_is_held(context, store, monkeypatch, capsys):
    _annotate(context, store, _scripted([*ANSWER_ONE, "q"]))
    monkeypatch.setattr(cli, "load_pool", lambda _golden_dir: context)

    class FixedClock(datetime):  # the fixture times lie after the real clock
        @classmethod
        def now(cls, tz=None):
            return LATER

    monkeypatch.setattr(cli, "datetime", FixedClock)
    with annotation_lock(store, annotator="ann-1", now=LATER):
        assert cli.main(["--work-root", str(store.directory.parent), "status"]) == 0
        assert cli.main(["--work-root", str(store.directory.parent), "validate"]) == 0
        assert store.lock_path.exists()
    out = capsys.readouterr().out
    assert f"1 of {len(context.items)} pairs judged" in out
    assert "an annotation lock is present" in out
    assert "working file valid: 1 primary judgement(s)" in out
    assert not store.lock_path.exists()


def test_isolated_work_directories_do_not_block_each_other(context, tmp_path):
    held = WorkStore(context, tmp_path / "a")
    other = WorkStore(context, tmp_path / "b")
    with annotation_lock(held, annotator="ann-1", now=T):
        assert _annotate(context, other, _scripted([*ANSWER_ONE, "q"])) == 1
    assert len(other.load(now=LATER)) == 1


# ---- finding 2: the binding's created_at --------------------------------------------------


def _one(context, at):
    (judgement,) = synthetic_primaries([context.items[0]], start=at)
    return judgement


def test_a_judgement_before_the_binding_is_refused_and_equality_is_allowed(context, store):
    store.initialize(now=T)
    with pytest.raises(AnnotationError, match="window"):
        store.save([_one(context, T - timedelta(microseconds=1))], now=LATER)
    store.save([_one(context, T)], now=LATER)  # the boundary itself is allowed
    assert store.load(now=LATER)[0].annotated_at == T


def test_save_requires_an_initialized_binding(context, store):
    with pytest.raises(AnnotationError, match="not initialized"):
        store.save([_one(context, T)], now=LATER)


def test_resume_preserves_the_original_created_at(context, store):
    first = store.initialize(now=T)
    data = (store.directory / BINDING_FILE).read_bytes()
    again = store.initialize(now=T + timedelta(hours=3))
    assert again.created_at == first.created_at == T
    _annotate(context, store, _scripted([*ANSWER_ONE, "q"]), now=_clock(T + timedelta(hours=4)))
    assert (store.directory / BINDING_FILE).read_bytes() == data
    assert store.read_binding(now=LATER).created_at == T


def _rewrite_binding(store, **changes):
    binding = store.read_binding(now=LATER)
    (store.directory / BINDING_FILE).write_bytes(
        encode_json_object(binding.model_copy(update=changes))
    )


@pytest.mark.parametrize(
    ("created_at", "message"),
    [
        (T + timedelta(hours=1), "annotated_at is outside"),  # moved after a stored judgement
        (LABEL_T0 - timedelta(days=30), "created_at is outside"),  # before query confirmation
        (LATER + timedelta(days=30), "created_at is outside"),  # in the future
    ],
)
def test_an_inconsistently_edited_binding_is_refused(context, store, created_at, message):
    store.initialize(now=T)
    store.save([_one(context, T + timedelta(minutes=1))], now=LATER)
    _rewrite_binding(store, created_at=created_at)
    with pytest.raises(AnnotationError, match=message):
        store.load(now=LATER)


def test_a_binding_cannot_be_recreated_under_an_existing_working_file(context, store):
    store.initialize(now=T)
    store.save([_one(context, T)], now=LATER)
    (store.directory / BINDING_FILE).unlink()
    with pytest.raises(AnnotationError, match="without its pool binding"):
        store.initialize(now=LATER)
    with pytest.raises(AnnotationError, match="without its pool binding"):
        store.load(now=LATER)


def test_the_primary_seal_check_enforces_the_binding_bound(context, store):
    store.initialize(now=T)
    # A full round, every record backdated one second before the binding (written directly,
    # as a hand edit would be).
    store.directory.mkdir(parents=True, exist_ok=True)
    backdated = synthetic_primaries(context.items, start=T - timedelta(seconds=1))
    store.path.write_bytes(encode_jsonl(backdated))
    data, created_at = seal_primary.read_primary(store, now=LATER)
    assert created_at == T
    with pytest.raises(AnnotationError, match="window"):
        seal_primary.check_primary(context, data, now=LATER, binding_created_at=created_at)
    on_time = synthetic_primaries(context.items, start=T)
    store.path.write_bytes(encode_jsonl(on_time))
    data, created_at = seal_primary.read_primary(store, now=LATER)
    assert len(seal_primary.check_primary(context, data, now=LATER, binding_created_at=T)) == len(
        context.items
    )


@pytest.mark.parametrize(
    "created_at",
    [
        "2026-10-06T14:00:00+00:00",  # not the canonical Z form
        "2026-10-06T14:00:00.000000",  # naive
        "2026-10-06T14:00:00.000000+05:30",  # not UTC
        "2026-13-06T14:00:00.000000Z",  # malformed
    ],
)
def test_malformed_binding_timestamps_are_refused(context, store, created_at):
    store.initialize(now=T)
    path = store.directory / BINDING_FILE
    body = json.loads(path.read_text(encoding="utf-8"))
    body["created_at"] = created_at
    path.write_text(
        json.dumps(body, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8"
    )
    with pytest.raises(ContractError):
        store.read_binding(now=LATER)


def test_naive_or_non_utc_binding_times_are_refused_by_the_model(context, store):
    expected = store.expected_binding(T).model_dump()
    for value in (datetime(2026, 10, 6, 14, 0), datetime(2026, 10, 6, 14, 0).astimezone()):
        if value.utcoffset() == timedelta(0) and value.tzinfo is not None:
            continue  # a host in UTC: the aware local time is UTC and therefore valid
        with pytest.raises(ValidationError):
            WorkBinding.model_validate({**expected, "created_at": value})
    assert WorkBinding.model_validate({**expected, "created_at": T.astimezone(UTC)}).created_at == T


def test_the_lock_file_is_not_mistaken_for_work_or_staging(context, store):
    store.initialize(now=T)
    with annotation_lock(store, annotator="ann-1", now=T):
        assert (store.directory / LOCK_FILE).exists()
        assert store.load(now=LATER) == []
