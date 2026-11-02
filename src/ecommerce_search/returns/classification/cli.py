"""Baseline command line. No network or model.

    python -m ecommerce_search.returns.classification classify <inputs.json>
    python -m ecommerce_search.returns.classification taxonomy
    python -m ecommerce_search.returns.classification record \
        --dataset-id <id> --dataset-version <n> [--min-pattern-size 3]

`classify` (offline) reads a JSON array of `ClassificationInput` objects (`contracts.py`) and
prints a JSON array of `ClassificationResult` objects on stdout. `taxonomy` prints the §19
categories, their suggested areas and the categories the baseline never predicts.

`record` connects with the POSTGRES_* settings as the least-privilege classifier role (any
other role is refused before a write), classifies every return of one M18 dataset version,
records the run, then records per-SKU complaint groups of that run (`store.py`). Both steps are
idempotent. It prints `{"classification": RunReport, "groups": GroupReport}`: counts of inferred
outputs, not accuracy.

Exit status: 0 on success; 2 on a usage, input, configuration, role or database error, with a
fixed message on stderr that contains no path, input value or credential.
"""

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from ecommerce_search.returns.classification.baseline import classify
from ecommerce_search.returns.classification.contracts import (
    CONTRACT_VERSION,
    RULES_VERSION,
    ClassificationInput,
)
from ecommerce_search.returns.classification.taxonomy import (
    NOT_PREDICTED_BY_BASELINE,
    SUGGESTED_AREAS,
    TAXONOMY_VERSION,
)

EXIT_OK = 0
EXIT_ERROR = 2
INPUT_MAX_BYTES = 5 * 1024 * 1024
_INPUTS = TypeAdapter(list[ClassificationInput])


class CliError(Exception):
    """A user-facing error with a fixed, already-safe message."""


def _load(path: Path) -> list[ClassificationInput]:
    try:
        with path.open("rb") as handle:
            data = handle.read(INPUT_MAX_BYTES + 1)
    except OSError:
        raise CliError("the input could not be read") from None
    if len(data) > INPUT_MAX_BYTES:
        raise CliError("the input is larger than allowed")
    try:
        return _INPUTS.validate_json(data)
    except ValidationError as exc:
        raise CliError(f"the input is not valid ({exc.error_count()} error(s))") from None


def _taxonomy() -> dict[str, object]:
    return {
        "taxonomy_version": TAXONOMY_VERSION,
        "contract_version": CONTRACT_VERSION,
        "rules_version": RULES_VERSION,
        "categories": [
            {
                "category": category.value,
                "suggested_responsible_areas": [area.value for area in areas],
                "predicted_by_baseline": category not in NOT_PREDICTED_BY_BASELINE,
            }
            for category, areas in SUGGESTED_AREAS.items()
        ],
    }


def _record(dataset_id: str, dataset_version: int, min_pattern_size: int) -> dict[str, object]:
    from sqlalchemy.exc import SQLAlchemyError

    from ecommerce_search.config import get_settings
    from ecommerce_search.db.engine import create_db_engine
    from ecommerce_search.returns.classification import store

    try:
        settings = get_settings()
    except ValidationError as exc:
        fields = sorted({str(e["loc"][0]) for e in exc.errors() if e["loc"]})
        raise CliError(f"settings are incomplete or invalid: {', '.join(fields)}") from None
    engine = create_db_engine(settings)
    try:
        run = store.record_classification_run(engine, dataset_id, dataset_version)
        groups = store.record_group_run(engine, run.run_id, min_pattern_size)
    except store.StoreError as exc:
        raise CliError(str(exc)) from None
    except SQLAlchemyError:
        raise CliError("the database operation failed") from None
    finally:
        engine.dispose()
    return {"classification": run.model_dump(mode="json"), "groups": groups.model_dump(mode="json")}


def main(argv: Sequence[str] | None = None) -> int:
    from ecommerce_search.returns.classification.grouping import DEFAULT_MIN_PATTERN_SIZE

    parser = argparse.ArgumentParser(
        prog="python -m ecommerce_search.returns.classification",
        description="Deterministic return-reason baseline (no network or model).",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("classify", help="classify a JSON array of inputs").add_argument(
        "path", type=Path
    )
    commands.add_parser("taxonomy", help="print the categories and suggested areas")
    record = commands.add_parser("record", help="classify and group one stored dataset version")
    record.add_argument("--dataset-id", required=True)
    record.add_argument("--dataset-version", required=True, type=int)
    record.add_argument("--min-pattern-size", type=int, default=DEFAULT_MIN_PATTERN_SIZE)
    args = parser.parse_args(argv)
    if args.command == "taxonomy":
        print(json.dumps(_taxonomy(), indent=2))
        return EXIT_OK
    if args.command == "record":
        if args.min_pattern_size < 2:
            print("classification record: --min-pattern-size must be at least 2", file=sys.stderr)
            return EXIT_ERROR
        try:
            report = _record(args.dataset_id, args.dataset_version, args.min_pattern_size)
        except CliError as exc:
            print(f"classification record: {exc}", file=sys.stderr)
            return EXIT_ERROR
        print(json.dumps(report, indent=2))
        return EXIT_OK
    try:
        inputs = _load(args.path)
    except CliError as exc:
        print(f"classification {args.command}: {exc}", file=sys.stderr)
        return EXIT_ERROR
    results = [classify(item).model_dump(mode="json") for item in inputs]
    print(json.dumps(results, indent=2))
    return EXIT_OK
