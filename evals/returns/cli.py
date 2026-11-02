"""Return-reason evaluation command line (run from the repository root).

    python -m evals.returns split --bundle <bundle.json> --out <split.json>
    python -m evals.returns kit --bundle <bundle.json> --split <split.json> --out-dir <dir>
    python -m evals.returns validate --split <split.json> --set dev --labels <labels.csv>
    python -m evals.returns score --split <split.json> --set dev --labels <labels.csv> \
        (--results <results.json> | --run-id <n>) [--final]

* `split` writes `return-reason-split-1` (refuses to overwrite).
* `kit` checks that the split matches the bundle and seed. It then writes `dev-kit.html` and
  `test-kit.html` (blind, offline) and the blank sheets `dev-labels.csv` and
  `test-labels.csv`. It refuses if any of the four already exists, so a human's labels are
  never overwritten.
* `validate` checks a label sheet against `return-reason-labels-1` and prints counts only.
* `score` compares baseline results with labels (`scoring.py`).
  - `--results` reads a JSON array of `ClassificationResult` objects, as printed by
    `python -m ecommerce_search.returns.classification classify`.
  - `--run-id` reads a stored run with the POSTGRES_* settings as the classifier role. Every
    result is source-verified (`store.read_result`).
  - The test set needs `--final`.

Exit status: 0 on success, 1 for invalid labels, 2 for any other error. Messages never quote
input values, label cells or credentials.
"""

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter, ValidationError

from ecommerce_search.returns.classification.contracts import ClassificationResult
from evals.returns import kit, labels, scoring, source, split

EXIT_OK, EXIT_INVALID, EXIT_ERROR = 0, 1, 2
RESULTS_MAX_BYTES = 32 * 1024 * 1024
_RESULTS = TypeAdapter(list[ClassificationResult])


class CliError(Exception):
    """A user-facing error with a fixed, already-safe message."""


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise CliError("a JSON input could not be read") from None


def _write_new(path: Path, text: str) -> None:
    try:
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
    except FileExistsError:
        raise CliError("refusing to overwrite an existing file") from None


def _split_for(bundle_path: Path, split_path: Path) -> tuple[source.Bundle, dict[str, Any]]:
    bundle = source.load_bundle(bundle_path)
    recorded = _read_json(split_path)
    try:
        split.check_split(recorded, bundle)
    except (ValueError, KeyError, TypeError):
        raise CliError("the split does not match the bundle and seed") from None
    return bundle, recorded


def _set_ids(split_path: Path, set_name: str) -> tuple[dict[str, Any], frozenset[str]]:
    recorded = _read_json(split_path)
    try:
        if recorded.get("split_version") != split.SPLIT_VERSION:
            raise CliError("unknown split version")
        return recorded, frozenset(recorded["sets"][set_name])
    except (AttributeError, KeyError, TypeError):
        raise CliError("the split file is not valid") from None


def _labels(path: Path, ids: frozenset[str]) -> dict[str, labels.HumanLabel]:
    try:
        data = path.read_bytes()
    except OSError:
        raise CliError("the label sheet could not be read") from None
    return labels.parse_labels(data, ids)


def _results_from_file(path: Path) -> tuple[dict[str, ClassificationResult], dict[str, Any]]:
    try:
        data = path.read_bytes()
    except OSError:
        raise CliError("the results file could not be read") from None
    if len(data) > RESULTS_MAX_BYTES:
        raise CliError("the results file is larger than allowed")
    try:
        parsed = _RESULTS.validate_json(data)
    except ValidationError:
        raise CliError("the results file is not a list of classification results") from None
    return {r.return_id: r for r in parsed}, {"source": "results_file"}


def _results_from_run(
    run_id: int, recorded: dict[str, Any]
) -> tuple[dict[str, ClassificationResult], dict[str, Any]]:
    from sqlalchemy import text
    from sqlalchemy.exc import SQLAlchemyError

    from ecommerce_search.config import get_settings
    from ecommerce_search.db.engine import create_db_engine
    from ecommerce_search.returns.classification import store

    try:
        settings = get_settings()
    except ValidationError:
        raise CliError("settings are incomplete or invalid") from None
    engine = create_db_engine(settings)
    try:
        with engine.connect() as conn:
            summary = store.run_summary(conn, run_id)
            if (summary.dataset_id, summary.dataset_version) != (
                recorded["dataset_id"],
                recorded["dataset_version"],
            ):
                raise CliError("the run is over another dataset version than the split")
            ids = conn.execute(
                text("SELECT id FROM return_classifications WHERE run_id = :r ORDER BY id"),
                {"r": run_id},
            ).scalars()
            results = [store.read_result(conn, cid) for cid in list(ids)]
    except store.StoreError as exc:
        raise CliError(str(exc)) from None
    except SQLAlchemyError:
        raise CliError("the database operation failed") from None
    finally:
        engine.dispose()
    info = {
        "source": "stored_run",
        "run_id": run_id,
        "rules_version": summary.rules_version,
        "contract_version": summary.contract_version,
        "taxonomy_version": summary.taxonomy_version,
    }
    return {r.return_id: r for r in results}, info


def _run(args: argparse.Namespace) -> int:
    if args.command == "split":
        result = split.make_split(source.load_bundle(args.bundle))
        _write_new(args.out, json.dumps(result, indent=2, sort_keys=True) + "\n")
        print(json.dumps({"split_version": result["split_version"], "sizes": result["sizes"]}))
        return EXIT_OK
    if args.command == "kit":
        bundle, recorded = _split_for(args.bundle, args.split)
        by_id = {item.return_id: item for item in bundle.items}
        targets = [
            args.out_dir / f"{name}{suffix}"
            for name in ("dev", "test")
            for suffix in ("-kit.html", "-labels.csv")
        ]
        if any(t.exists() for t in targets):
            raise CliError("refusing to overwrite an existing kit or label sheet")
        args.out_dir.mkdir(parents=True, exist_ok=True)
        for name in ("dev", "test"):
            order = recorded["presentation_order"][name]
            sheet = args.out_dir / f"{name}-labels.csv"
            html = kit.render_kit(name, [by_id[rid] for rid in order], sheet.name)
            _write_new(args.out_dir / f"{name}-kit.html", html)
            labels.write_template(sheet, order)
        print(json.dumps({"written": sorted(t.name for t in targets)}))
        return EXIT_OK
    recorded, ids = _set_ids(args.split, args.set)
    parsed = _labels(args.labels, ids)
    if args.command == "validate":
        print(json.dumps({"set": args.set, "labelled": len(parsed), "set_size": len(ids)}))
        return EXIT_OK
    if args.results is not None:
        results, info = _results_from_file(args.results)
    else:
        results, info = _results_from_run(args.run_id, recorded)
    info |= {"split_version": recorded["split_version"], "seed": recorded["seed"]}
    report = scoring.score(args.set, ids, results, parsed, final=args.final, run_info=info)
    print(json.dumps(report, indent=2))
    return EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.returns")
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("split", help="write the seeded dev/test split")
    p.add_argument("--bundle", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p = commands.add_parser("kit", help="write the blind kits and blank label sheets")
    p.add_argument("--bundle", type=Path, required=True)
    p.add_argument("--split", type=Path, required=True)
    p.add_argument("--out-dir", type=Path, required=True)
    for name in ("validate", "score"):
        p = commands.add_parser(name)
        p.add_argument("--split", type=Path, required=True)
        p.add_argument("--set", choices=("dev", "test"), required=True)
        p.add_argument("--labels", type=Path, required=True)
        if name == "score":
            source_group = p.add_mutually_exclusive_group(required=True)
            source_group.add_argument("--results", type=Path)
            source_group.add_argument("--run-id", type=int)
            p.add_argument("--final", action="store_true")
    args = parser.parse_args(argv)
    try:
        return _run(args)
    except labels.LabelError as exc:
        print(f"labels invalid: {exc}", file=sys.stderr)
        return EXIT_INVALID
    except (CliError, source.SourceError, scoring.ScoringError) as exc:
        print(f"evals.returns {args.command}: {exc}", file=sys.stderr)
        return EXIT_ERROR
