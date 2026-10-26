"""Return data command line (contracts §4.1): `validate` and `import`.

    python -m ecommerce_search.returns validate <bundle.json | csv-dir>
    python -m ecommerce_search.returns import <bundle.json | csv-dir>

Both commands call the same domain service as the API (`returns.service`).

**Input.** A file argument is the set's `bundle.json`; its local name is not used. A directory
gives one part per regular file, named by its file name, so only allowlisted names are
accepted and any other file is reported as `unexpected part #n`. Each file is read with a cap
just above the per-file limit, so oversized input is reported by Phase A, not loaded whole.

**Connection.** Both commands connect with the POSTGRES_* settings. `import` requires
RETURNS_IMPORTER_LABEL, which is supplied provenance and not an authenticated identity. Before
any write, `import` refuses every role except the least-privilege importer. There is no
fallback to other credentials. `verify` (§4.7) is not part of this adapter yet.

**Output.** The `ImportReport` is printed as JSON on stdout. Exit status:
- 0: `valid`, `imported`, `already_imported` or `no_new_records`;
- 1: `invalid`;
- 2: a usage, input, configuration, role or database error, with a fixed message on stderr.

Messages never contain credentials, connection strings, paths or input values.
"""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

from ecommerce_search.config import Settings, get_settings
from ecommerce_search.db.engine import create_db_engine
from ecommerce_search.returns.bundle import BUNDLE_NAME, FILE_MAX_BYTES, Part
from ecommerce_search.returns.service import (
    ImportReport,
    ImportRoleError,
    ImportStorageError,
    commit_import,
    validate_import,
)

EXIT_OK = 0
EXIT_INVALID = 1
EXIT_ERROR = 2


class CliError(Exception):
    """A user-facing error with a fixed, already-safe message."""


def _settings() -> Settings:
    try:
        return get_settings()
    except ValidationError as exc:
        fields = sorted({str(e["loc"][0]) for e in exc.errors() if e["loc"]})
        raise CliError(f"settings are incomplete or invalid: {', '.join(fields)}") from None


def _read(path: Path) -> bytes:
    with path.open("rb") as handle:
        return handle.read(FILE_MAX_BYTES + 1)


def read_parts(path: Path) -> list[Part]:
    """The upload parts of a bundle file or a CSV-set directory (not recursive)."""
    try:
        if path.is_file():
            return [Part(BUNDLE_NAME, _read(path))]
        if path.is_dir():
            entries = sorted(path.iterdir(), key=lambda entry: entry.name)
            if any(not entry.is_file() for entry in entries):
                raise CliError("the CSV directory must contain only regular files")
            return [Part(entry.name, _read(entry)) for entry in entries]
    except OSError:
        raise CliError("the input could not be read") from None
    raise CliError("the input is not a file or a directory")


def _run(command: str, path: Path) -> ImportReport:
    settings = _settings()
    label = settings.returns_importer_label
    if command == "import" and label is None:
        raise CliError("RETURNS_IMPORTER_LABEL is required for import")
    parts = read_parts(path)
    engine = create_db_engine(settings)
    try:
        if command == "import":
            return commit_import(engine, parts, imported_by_label=label)
        return validate_import(engine, parts)
    finally:
        engine.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m ecommerce_search.returns",
        description="Validate or import a return-data-v1 bundle.json or CSV-set directory.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("validate", "check an upload against stored data; never writes"),
        ("import", "validate and commit an upload as the importer role"),
    ):
        commands.add_parser(name, help=help_text).add_argument("path", type=Path)
    args = parser.parse_args(argv)
    try:
        report = _run(args.command, args.path)
    except (CliError, ImportRoleError, ImportStorageError) as exc:
        print(f"returns {args.command}: {exc}", file=sys.stderr)
        return EXIT_ERROR
    print(report.model_dump_json(indent=2))
    return EXIT_INVALID if report.status == "invalid" else EXIT_OK
