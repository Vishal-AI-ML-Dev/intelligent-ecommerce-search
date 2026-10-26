"""M18-03B CLI adapter, without a database: argument and input handling, exit codes, the
required importer label, and messages free of paths, values and credentials. The domain service
is replaced by recorders here; the database path is covered by the integration tests."""

import pytest

from ecommerce_search.returns import cli
from ecommerce_search.returns.bundle import FILE_MAX_BYTES
from ecommerce_search.returns.service import (
    EntityCounts,
    ImportReport,
    ImportRoleError,
    ImportStorageError,
)
from returns_uploads import CHECKED_AT, csv_parts, json_parts

FAKE_PW = "cli-test-password-not-real"


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("POSTGRES_PASSWORD", FAKE_PW)
    monkeypatch.setenv("POSTGRES_DB", "ecommerce_search_test_cli")


class Calls(list):
    """Service calls recorded instead of reaching a database; `status` is the next result."""

    status = "valid"


@pytest.fixture
def calls(monkeypatch):
    seen = Calls()

    class Engine:
        def dispose(self):
            seen.append(("dispose",))

    def report(mode, parts):
        return ImportReport(
            mode=mode,
            status=seen.status,
            bundle_sha256=None,
            checked_at=CHECKED_AT,
            counts={"orders": EntityCounts(received=len(parts))},
            phases_completed=(),
            error_count=0,
            error_count_is_lower_bound=False,
            errors_truncated=False,
            errors=(),
        )

    def validate_import(engine, parts):
        seen.append(("validate", [p.name for p in parts]))
        return report("validate", parts)

    def commit_import(engine, parts, *, imported_by_label):
        seen.append(("commit", [p.name for p in parts], imported_by_label))
        return report("commit", parts)

    monkeypatch.setattr(cli, "create_db_engine", lambda settings: Engine())
    monkeypatch.setattr(cli, "validate_import", validate_import)
    monkeypatch.setattr(cli, "commit_import", commit_import)
    return seen


def write_json(tmp_path, name="my-local-export.json"):
    path = tmp_path / name
    path.write_bytes(json_parts()[0].data)
    return path


def write_csv_dir(tmp_path):
    folder = tmp_path / "csv"
    folder.mkdir()
    for part in csv_parts():
        (folder / part.name).write_bytes(part.data)
    return folder


def test_a_file_argument_is_read_as_bundle_json_whatever_its_local_name(tmp_path):
    parts = cli.read_parts(write_json(tmp_path))
    assert [p.name for p in parts] == ["bundle.json"]
    assert parts[0].data == json_parts()[0].data


def test_a_directory_gives_one_part_per_file_in_name_order(tmp_path):
    folder = write_csv_dir(tmp_path)
    (folder / "scenarios.json").write_bytes(b"{}")  # refused later by Phase A as a part
    names = [p.name for p in cli.read_parts(folder)]
    assert names == sorted(p.name for p in folder.iterdir())
    assert "scenarios.json" in names


def test_files_are_read_with_a_cap_just_above_the_file_limit(tmp_path):
    big = tmp_path / "big.json"
    big.write_bytes(b" " * (FILE_MAX_BYTES + 100))
    assert len(cli.read_parts(big)[0].data) == FILE_MAX_BYTES + 1


@pytest.mark.parametrize("case", ["missing", "subdirectory"])
def test_unusable_input_is_a_cli_error_without_the_path(tmp_path, case):
    path = tmp_path / "secret-folder-name"
    if case == "subdirectory":
        (path / "nested").mkdir(parents=True)
    with pytest.raises(cli.CliError) as caught:
        cli.read_parts(path)
    assert "secret-folder-name" not in str(caught.value)


def test_validate_prints_the_report_and_exits_0(env, calls, tmp_path, capsys):
    assert cli.main(["validate", str(write_csv_dir(tmp_path))]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert '"mode": "validate"' in out and '"status": "valid"' in out
    assert calls[0][0] == "validate" and calls[-1] == ("dispose",)


@pytest.mark.parametrize(
    ("status", "code"),
    [
        ("valid", 0),
        ("imported", 0),
        ("already_imported", 0),
        ("no_new_records", 0),
        ("invalid", 1),
    ],
)
def test_exit_status_follows_the_report_status(env, calls, tmp_path, monkeypatch, status, code):
    monkeypatch.setenv("RETURNS_IMPORTER_LABEL", "deploy-1")
    calls.status = status
    command = "validate" if status == "valid" else "import"
    assert cli.main([command, str(write_json(tmp_path))]) == code


def test_import_passes_the_configured_label(env, calls, tmp_path, monkeypatch):
    monkeypatch.setenv("RETURNS_IMPORTER_LABEL", "deploy-1")
    calls.status = "imported"
    assert cli.main(["import", str(write_json(tmp_path))]) == 0
    assert calls[0] == ("commit", ["bundle.json"], "deploy-1")


def test_import_without_a_label_fails_before_reading_input_or_connecting(
    env, calls, tmp_path, capsys
):
    assert cli.main(["import", str(tmp_path / "does-not-matter")]) == cli.EXIT_ERROR
    err = capsys.readouterr().err
    assert "RETURNS_IMPORTER_LABEL is required" in err
    assert calls == []


def test_an_invalid_label_or_missing_password_names_the_setting_only(
    calls, tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("RETURNS_IMPORTER_LABEL", "ops@example.com")
    assert cli.main(["import", str(write_json(tmp_path))]) == cli.EXIT_ERROR
    err = capsys.readouterr().err
    assert "postgres_password" in err and "returns_importer_label" in err
    assert "ops@example.com" not in err
    assert calls == []


@pytest.mark.parametrize(
    "error", [ImportRoleError("superuser"), ImportStorageError()], ids=["role", "storage"]
)
def test_role_and_storage_errors_exit_2_with_a_fixed_message(
    env, tmp_path, monkeypatch, capsys, error
):
    monkeypatch.setenv("RETURNS_IMPORTER_LABEL", "deploy-1")

    class Engine:
        def dispose(self):
            pass

    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(cli, "create_db_engine", lambda settings: Engine())
    monkeypatch.setattr(cli, "commit_import", fail)
    assert cli.main(["import", str(write_json(tmp_path))]) == cli.EXIT_ERROR
    captured = capsys.readouterr()
    assert captured.out == ""
    assert str(error) in captured.err
    assert FAKE_PW not in captured.err and "postgresql" not in captured.err


def test_only_validate_and_import_are_commands(capsys):
    with pytest.raises(SystemExit) as caught:
        cli.main(["verify", "x"])
    assert caught.value.code == 2
    with pytest.raises(SystemExit):
        cli.main(["import", "x", "--database", "other"])
