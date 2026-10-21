"""M13-3 command line without a database: the one-listing file rule, offline `normalize`, and
`submit`/`show` argument and failure handling before any database access. All listings are
SYNTHETIC test fixtures."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from ecommerce_search.normalization import cli, store
from ecommerce_search.normalization.intake import MAX_RAW_BYTES, parse_raw_listing
from ecommerce_search.normalization.normalizer import normalize_listing

ROOT = Path(__file__).resolve().parents[3]
LISTING = (
    '{"seller_id":"SYN-TEST-SELLER","seller_listing_ref":"SYN-CLI-1","title":"Zénith 🔥 16GB RAM",'
    '"brand":"hp","ram_gb":"8 gb","rating":4.5}'
).encode()


def _file(tmp_path, data: bytes, name="listing.json") -> Path:
    path = tmp_path / name
    path.write_bytes(data)
    return path


def _no_database(monkeypatch):
    def refuse(_database):
        raise AssertionError("no settings or database access is allowed here")

    monkeypatch.setattr(cli, "_engine", refuse)


def _normalize(capsysbinary, path) -> tuple[int, bytes, bytes]:
    code = cli.main(["normalize", "--file", str(path)])
    captured = capsysbinary.readouterr()
    return code, captured.out, captured.err


# ---- normalize ----------------------------------------------------------------------------


def test_normalize_prints_the_deterministic_normalization(tmp_path, capsysbinary, monkeypatch):
    _no_database(monkeypatch)
    path = _file(tmp_path, LISTING + b"\n")
    code, out, err = _normalize(capsysbinary, path)
    assert (code, err) == (0, b"")
    printed = json.loads(out.decode("utf-8"))
    raw = parse_raw_listing(LISTING)
    result = normalize_listing(raw)
    assert printed == {
        "seller_id": "SYN-TEST-SELLER",
        "seller_listing_ref": "SYN-CLI-1",
        "raw_sha256": raw.raw_sha256,
        "output_sha256": result.output_sha256(),
        "normalization": result.model_dump(mode="json"),
    }
    again = _normalize(capsysbinary, path)
    assert again == (0, out, b"")


@pytest.mark.parametrize("ending", [b"", b"\n", b"\r\n"])
def test_one_final_line_terminator_is_not_part_of_the_listing(tmp_path, capsysbinary, ending):
    code, out, _ = _normalize(capsysbinary, _file(tmp_path, LISTING + ending))
    assert code == 0
    assert json.loads(out)["raw_sha256"] == parse_raw_listing(LISTING).raw_sha256


@pytest.mark.parametrize(
    "data",
    [
        LISTING + b"\n" + LISTING + b"\n",  # two listings: no bulk import
        LISTING + b"\n\n",
        b'{"seller_id":"SYN-S",\n"seller_listing_ref":"SYN-R"}',  # pretty-printed JSON
        LISTING + b"\r",
        b"\r" + LISTING,
    ],
    ids=["two-listings", "blank-second-line", "pretty-printed", "trailing-cr", "leading-cr"],
)
def test_anything_but_one_line_is_rejected(tmp_path, capsysbinary, data):
    code, out, err = _normalize(capsysbinary, _file(tmp_path, data))
    assert (code, out) == (1, b"")
    assert b"exactly one listing on one line" in err and b"bulk import" in err


@pytest.mark.parametrize(
    ("data", "reason"),
    [
        (b"", "empty"),
        (b"\n", "empty"),
        (b"\xef\xbb\xbf" + LISTING, "byte_order_mark"),
        (b'{"seller_id":"SYN-S","seller_listing_ref":"SYN-R",', "invalid_json"),
        (b'{"seller_id":"SYN-S","seller_listing_ref":"SYN-R","a":1,"a":2}', "duplicate_key"),
        (b'{"seller_listing_ref":"SYN-R"}', "missing_identity"),
        (b'{"seller_id":" SYN-S","seller_listing_ref":"SYN-R"}', "invalid_identity"),
        (b"\xff", "not_utf8"),
        (b"x" * (MAX_RAW_BYTES + 1), "too_large"),
        (b"x" * (MAX_RAW_BYTES + 3), "too_large"),
    ],
    ids=lambda value: value if isinstance(value, str) else f"{len(value)}-bytes",
)
def test_intake_rejections_are_reported_by_reason(tmp_path, capsysbinary, data, reason):
    code, out, err = _normalize(capsysbinary, _file(tmp_path, data))
    assert (code, out) == (1, b"")
    assert err.startswith(f"error: listing rejected at intake: {reason}:".encode())
    assert b"Traceback" not in err


def test_a_missing_file_is_one_line_error(tmp_path, capsysbinary):
    code, out, err = _normalize(capsysbinary, tmp_path / "absent.json")
    assert (code, out) == (1, b"")
    assert err.startswith(b"error: cannot access absent.json")


def test_normalize_runs_offline_in_a_clean_process(tmp_path):
    # The CLI path itself: no settings, no database/HTTP/model module, no network attempt.
    path = _file(tmp_path, LISTING + b"\n")
    child = (
        "import json, runpy, socket, sys\n"
        "attempts = []\n"
        "def refuse(*a, **k):\n"
        "    attempts.append('net')\n"
        "    raise OSError('network access is refused in this test')\n"
        "for name in ('create_connection', 'getaddrinfo', 'gethostbyname'):\n"
        "    setattr(socket, name, refuse)\n"
        "for name in ('connect', 'connect_ex', 'sendto', 'bind'):\n"
        "    setattr(socket.socket, name, refuse)\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "from ecommerce_search.normalization.cli import main\n"
        "code = main(['normalize', '--file', sys.argv[2]])\n"
        "loaded = sorted(n for n in sys.modules if n.split('.')[0] in {'sqlalchemy', 'psycopg',\n"
        "    'httpx', 'torch', 'sentence_transformers', 'fastapi', 'dotenv', 'pydantic_settings'}\n"
        "    or n.startswith(('ecommerce_search.config', 'ecommerce_search.db',\n"
        "    'ecommerce_search.models', 'ecommerce_search.search',\n"
        "    'ecommerce_search.normalization.store')))\n"
        "sys.stderr.write(json.dumps({'code': code, 'loaded': loaded, 'attempts': attempts}))\n"
    )
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("POSTGRES_", "DB_", "APP_ENV", "EMBEDDING_", "RERANKER_", "JEV_"))
    }
    result = subprocess.run(  # noqa: S603 - fixed argv, sys.executable
        [sys.executable, "-I", "-c", child, str(ROOT / "src"), str(path)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        timeout=120,
        check=False,
    )
    report = json.loads(result.stderr)
    assert report == {"code": 0, "loaded": [], "attempts": []}
    printed = json.loads(result.stdout.decode("utf-8"))
    assert printed["output_sha256"] == normalize_listing(parse_raw_listing(LISTING)).output_sha256()


# ---- submit / show without a database ---------------------------------------------------


def test_submit_rejects_bad_input_before_settings_or_database(tmp_path, capsysbinary, monkeypatch):
    _no_database(monkeypatch)
    bad = _file(tmp_path, b'{"seller_id":"SYN-S"}')
    assert cli.main(["submit", "--database", "scratch_only", "--file", str(bad)]) == 1
    captured = capsysbinary.readouterr()
    assert b"missing_identity" in captured.err and b"target database" not in captured.out
    two = _file(tmp_path, LISTING + b"\n" + LISTING, "two.json")
    assert cli.main(["submit", "--database", "scratch_only", "--file", str(two)]) == 1
    assert b"bulk import" in capsysbinary.readouterr().err


def test_submit_without_database_settings_fails_cleanly(tmp_path, capsysbinary, monkeypatch):
    monkeypatch.delenv("POSTGRES_PASSWORD", raising=False)
    path = _file(tmp_path, LISTING)
    assert cli.main(["submit", "--database", "scratch_only", "--file", str(path)]) == 1
    captured = capsysbinary.readouterr()
    assert b"target database: scratch_only" in captured.out
    assert captured.err.startswith(b"error: database settings are incomplete")


class _FakeEngine:
    disposed = False

    def dispose(self):
        self.disposed = True


@pytest.mark.parametrize(
    "error",
    [
        store.NotMigrated("not migrated; run `uv run alembic upgrade head`. Nothing was written"),
        store.NormalizationMismatch("changed output under the same rules version"),
    ],
)
def test_store_errors_become_one_line(tmp_path, capsysbinary, monkeypatch, error):
    engine = _FakeEngine()
    monkeypatch.setattr(cli, "_engine", lambda _database: engine)

    def fail(_engine, _raw):
        raise error

    monkeypatch.setattr(store, "submit_listing", fail)
    path = _file(tmp_path, LISTING)
    assert cli.main(["submit", "--database", "scratch_only", "--file", str(path)]) == 1
    assert capsysbinary.readouterr().err == f"error: {error}\n".encode()
    assert engine.disposed


def test_show_failures_become_one_line(capsysbinary, monkeypatch):
    engine = _FakeEngine()
    monkeypatch.setattr(cli, "_engine", lambda _database: engine)

    def fail(*_args):
        raise store.IntegrityFailure("listing 'SYN-S'/'SYN-R' version 1 failed its integrity check")

    monkeypatch.setattr(store, "read_listing", fail)
    argv = ["show", "--database", "scratch_only", "--seller", "SYN-S", "--ref", "SYN-R"]
    assert cli.main(argv) == 1
    captured = capsysbinary.readouterr()
    assert captured.out == b"" and b"integrity check" in captured.err
    assert engine.disposed


@pytest.mark.parametrize(
    "argv",
    [
        ["show", "--seller", "S", "--ref", "R"],  # no database: there is no default
        ["submit", "--file", "x.json"],
        ["normalize"],
        ["show", "--database", "d", "--seller", "S", "--ref", "R", "--version", "0"],
        ["show", "--database", "d", "--seller", "S", "--ref", "R", "--version", "01"],
        ["show", "--database", "d", "--seller", "S", "--ref", "R", "--version", "-1"],
        ["show", "--database", "d", "--seller", "S", "--ref", "R", "--version", "x"],
        ["show", "--database", "d", "--seller", "S"],
    ],
)
def test_invalid_arguments_exit_2(argv, monkeypatch):
    _no_database(monkeypatch)
    with pytest.raises(SystemExit) as excinfo:
        cli.main(argv)
    assert excinfo.value.code == 2
