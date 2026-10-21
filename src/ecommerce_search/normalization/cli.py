"""Seller listing normalization command line (Milestone 13).

Commands (standard library `argparse`):

* `normalize --file F`: offline. Prints the deterministic normalization of one listing. It
  needs no settings, database, network or model, and imports no database code.
* `submit --database NAME --file F`: stores one listing (exact raw bytes, version and
  normalization) in the named database, atomically. There is no default database.
* `show --database NAME --seller S --ref R [--version N]`: read-only. Prints one stored
  version (default: the latest) and its normalization runs exactly as recorded, after
  checking them against their own stored hashes. Old rules are never re-run.

File rule: a file holds exactly ONE raw listing, as one line. A single final line terminator
(LF or CRLF) is not part of the listing; any other line break is rejected. There is no bulk
import. The listing bytes then go through the intake contract (`intake.parse_raw_listing`).

Expected failures (intake rejection, file and database errors, integrity failures) become one
concise line on stderr and exit code 1; they never show tracebacks, URLs, credentials or SQL.
Output is written as UTF-8 regardless of the console encoding.
"""

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from ecommerce_search.normalization import rules as nr
from ecommerce_search.normalization.intake import (
    MAX_RAW_BYTES,
    IntakeReason,
    ListingIntakeError,
    RawListing,
    parse_raw_listing,
)
from ecommerce_search.normalization.normalizer import normalize_listing


class CliError(Exception):
    """A user-facing operational error with an already-sanitized message."""


def _write(stream, text: str) -> None:
    stream.flush()
    stream.buffer.write(text.encode("utf-8"))
    stream.buffer.flush()


def _out(text: str) -> None:
    _write(sys.stdout, text + "\n")


def _dump(value: object) -> None:
    _out(json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False))


def read_listing_file(path: Path) -> bytes:
    """The raw listing bytes of a one-line file (see the module docstring)."""
    with path.open("rb") as handle:
        data = handle.read(MAX_RAW_BYTES + 3)
    if len(data) > MAX_RAW_BYTES + 2:
        raise ListingIntakeError(
            IntakeReason.TOO_LARGE, f"the file is larger than {MAX_RAW_BYTES} bytes plus a line end"
        )
    if data.endswith(b"\r\n"):
        data = data[:-2]
    elif data.endswith(b"\n"):
        data = data[:-1]
    if b"\n" in data or b"\r" in data:
        raise CliError(
            f"{path.name} must contain exactly one listing on one line (bulk import is not "
            "supported); nothing was processed"
        )
    return data


def _read(path: Path) -> RawListing:
    return parse_raw_listing(read_listing_file(path))


def _engine(database: str):
    from pydantic import ValidationError

    from ecommerce_search.config import get_settings
    from ecommerce_search.db.engine import create_db_engine

    try:
        settings = get_settings()
    except ValidationError:
        raise CliError(
            "database settings are incomplete or invalid (is POSTGRES_PASSWORD set?)"
        ) from None
    return create_db_engine(settings.model_copy(update={"postgres_db": database}))


def _with_database(database: str, action):
    """Run `action(engine)`. The store's sanitized errors and database driver errors become
    one CliError line."""
    import psycopg
    from sqlalchemy.exc import SQLAlchemyError

    from ecommerce_search.normalization.store import StoreError

    engine = _engine(database)
    try:
        return action(engine)
    except StoreError as exc:
        raise CliError(str(exc)) from None
    except (SQLAlchemyError, psycopg.Error) as exc:
        raise CliError(
            f"database error ({type(exc).__name__}); check that the database exists, is reachable "
            "and has been migrated to head (`uv run alembic upgrade head`). Details are "
            "intentionally not shown"
        ) from None
    finally:
        engine.dispose()


# ---- commands -------------------------------------------------------------------------------


def cmd_normalize(args: argparse.Namespace) -> int:
    raw = _read(args.file)
    result = normalize_listing(raw)
    result.verify_evidence(raw)
    _dump(
        {
            "seller_id": raw.identity.seller_id,
            "seller_listing_ref": raw.identity.seller_listing_ref,
            "raw_sha256": raw.raw_sha256,
            "output_sha256": result.output_sha256(),
            "normalization": result.model_dump(mode="json"),
        }
    )
    return 0


def cmd_submit(args: argparse.Namespace) -> int:
    from ecommerce_search.normalization.store import submit_listing

    raw = _read(args.file)  # intake before any settings or database access
    _out(f"target database: {args.database}")
    outcome = _with_database(args.database, lambda engine: submit_listing(engine, raw))
    _out(f"submit: {outcome.summary()}")
    return 0


def _iso(value) -> str | None:
    return None if value is None else value.isoformat()


def cmd_show(args: argparse.Namespace) -> int:
    from dataclasses import asdict

    from ecommerce_search.normalization.store import read_listing

    stored = _with_database(
        args.database, lambda engine: read_listing(engine, args.seller, args.ref, args.version)
    )
    current_version, current_digest = nr.RULES_VERSION, nr.rules_digest()
    _dump(
        {
            "seller_id": stored.seller_id,
            "seller_listing_ref": stored.seller_listing_ref,
            "version_number": stored.version_number,
            "latest_version_number": stored.version_count,
            "submitted_at": _iso(stored.submitted_at),
            "raw_sha256": stored.raw_sha256,
            "raw_text": stored.raw_bytes.decode("utf-8"),
            "integrity": (
                "verified against the stored hashes and row counts; historical rules were not "
                "re-run"
            ),
            "current_rules": {"rules_version": current_version, "rules_digest": current_digest},
            "runs": [
                {
                    "rules_version": run.rules_version,
                    "rules_digest": run.rules_digest,
                    "taxonomy_version": run.taxonomy_version,
                    "output_sha256": run.output_sha256,
                    "created_at": _iso(run.created_at),
                    # Informational only; a historical digest need not equal today's.
                    "same_as_current_rules": (
                        (run.rules_version, run.rules_digest) == (current_version, current_digest)
                    ),
                    "candidates": [asdict(c) for c in run.candidates],
                    "findings": [asdict(f) for f in run.findings],
                }
                for run in stored.runs
            ],
        }
    )
    return 0


# ---- parser ---------------------------------------------------------------------------------


def _positive_int(value: str) -> int:
    if not value.isascii() or not value.isdigit() or value.startswith("0"):
        raise argparse.ArgumentTypeError("must be a positive integer")
    return int(value)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m ecommerce_search.normalization", description=__doc__
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def add_db(p: argparse.ArgumentParser, why: str) -> None:
        p.add_argument(
            "--database", required=True, help=f"database name on the configured server ({why})"
        )

    p = sub.add_parser("normalize", help="offline: print the normalization of one listing file")
    p.add_argument("--file", type=Path, required=True, help="file holding one raw listing line")
    p.set_defaults(func=cmd_normalize)

    p = sub.add_parser("submit", help="store one listing and its normalization atomically")
    add_db(p, "REQUIRED: the database that will be written; no default")
    p.add_argument("--file", type=Path, required=True, help="file holding one raw listing line")
    p.set_defaults(func=cmd_submit)

    p = sub.add_parser("show", help="read-only: print a stored listing version as recorded")
    add_db(p, "REQUIRED: the database to read; no default")
    p.add_argument("--seller", required=True, help="exact seller_id")
    p.add_argument("--ref", required=True, help="exact seller_listing_ref")
    p.add_argument(
        "--version", type=_positive_int, help="stored version number (default: the latest)"
    )
    p.set_defaults(func=cmd_show)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ListingIntakeError as exc:
        return _fail(f"listing rejected at intake: {exc}")
    except CliError as exc:
        return _fail(str(exc))
    except OSError as exc:
        name = Path(exc.filename).name if exc.filename else "a file"
        return _fail(f"cannot access {name}: {exc.strerror or type(exc).__name__}")


def _fail(message: str) -> int:
    _write(sys.stderr, f"error: {message}\n")
    return 1


if __name__ == "__main__":
    sys.exit(main())
