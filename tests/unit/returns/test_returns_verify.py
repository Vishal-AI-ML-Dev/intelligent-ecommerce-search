"""M18-03C `returns verify` without a database: the declared surface, an invalid release that
never reaches the database, the fixed storage error, and the CLI adapter (exit codes, no
label, messages without paths or credentials). The stored-side comparison is covered by
`tests/integration/test_returns_dataset_integration.py`."""

import json

import pytest

from ecommerce_search.returns import cli, verify
from ecommerce_search.returns.verify import (
    VerifyCounts,
    VerifyReport,
    VerifyStorageError,
    verify_dataset,
)
from returns_uploads import CHECKED_AT, json_parts

FAKE_PW = "cli-test-password-not-real"
VERIFY_SURFACE = {"VerifyCounts", "VerifyReport", "VerifyStorageError", "verify_dataset"}


class NoDatabase:
    def connect(self):
        raise AssertionError("verify of an invalid release must not reach the database")

    def dispose(self):
        pass


def test_the_verify_surface_equals_the_declared_set_exactly():
    public = {
        name
        for name, value in vars(verify).items()
        if not name.startswith("_")
        and callable(value)
        and getattr(value, "__module__", None) == verify.__name__
    }
    assert set(verify.__all__) == VERIFY_SURFACE == public


def test_an_invalid_release_is_reported_without_reading_the_database():
    parts = json_parts(top_level_extra=1)  # an unknown top-level key
    report = verify_dataset(NoDatabase(), parts, checked_at=CHECKED_AT)
    assert report.status == "invalid"
    assert report.error_count >= 1 and report.counts == {}
    assert report.expected_records_sha256 is None and report.stored_records_sha256 is None
    assert report.dataset_stored is None


def test_the_storage_error_has_a_fixed_message_and_no_cause():
    error = VerifyStorageError()
    assert str(error) == "the stored records could not be read"
    assert error.__cause__ is None


def report(status: str) -> VerifyReport:
    return VerifyReport(
        status=status,
        checked_at=CHECKED_AT,
        counts={"orders": VerifyCounts(expected=1, stored=0, missing=1, extra=0, changed=0)},
    )


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("POSTGRES_PASSWORD", FAKE_PW)
    monkeypatch.setenv("POSTGRES_DB", "ecommerce_search_test_cli")


class Calls(list):
    """Verify calls recorded instead of reaching a database; `status` is the next result."""

    status = "match"


@pytest.fixture
def calls(monkeypatch):
    seen = Calls()

    class Engine:
        def dispose(self):
            seen.append(("dispose",))

    def fake_verify(engine, parts):
        seen.append(("verify", [p.name for p in parts]))
        return report(seen.status)

    monkeypatch.setattr(cli, "create_db_engine", lambda settings: Engine())
    monkeypatch.setattr(cli, "verify_dataset", fake_verify)
    return seen


def write_json(tmp_path):
    path = tmp_path / "local-release-name.json"
    path.write_bytes(json_parts()[0].data)
    return path


@pytest.mark.parametrize(("status", "code"), [("match", 0), ("mismatch", 1), ("invalid", 1)])
def test_verify_exit_status_follows_the_report(env, calls, tmp_path, capsys, status, code):
    calls.status = status
    assert cli.main(["verify", str(write_json(tmp_path))]) == code
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == status and out["counts"]["orders"]["missing"] == 1
    assert calls[0] == ("verify", ["bundle.json"]) and calls[-1] == ("dispose",)


def test_verify_needs_no_importer_label(env, calls, tmp_path, monkeypatch):
    monkeypatch.delenv("RETURNS_IMPORTER_LABEL", raising=False)
    assert cli.main(["verify", str(write_json(tmp_path))]) == cli.EXIT_OK


def test_a_verify_storage_error_exits_2_with_a_fixed_message(env, tmp_path, monkeypatch, capsys):
    class Engine:
        def dispose(self):
            pass

    def fail(*args, **kwargs):
        raise VerifyStorageError()

    monkeypatch.setattr(cli, "create_db_engine", lambda settings: Engine())
    monkeypatch.setattr(cli, "verify_dataset", fail)
    path = write_json(tmp_path)
    assert cli.main(["verify", str(path)]) == cli.EXIT_ERROR
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "returns verify: the stored records could not be read\n"
    assert FAKE_PW not in captured.err and str(path) not in captured.err
