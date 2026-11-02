"""Offline baseline command line. No database, network or model.

    python -m ecommerce_search.returns.classification classify <inputs.json>
    python -m ecommerce_search.returns.classification taxonomy

`classify` reads a JSON array of `ClassificationInput` objects (`contracts.py`) and prints a
JSON array of `ClassificationResult` objects on stdout. `taxonomy` prints the §19 categories,
their suggested areas and the categories the baseline never predicts.

Exit status: 0 on success; 2 on a usage or input error, with a fixed message on stderr that
contains no path or input value.
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


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m ecommerce_search.returns.classification",
        description="Deterministic return-reason baseline (offline; no database or model).",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("classify", help="classify a JSON array of inputs").add_argument(
        "path", type=Path
    )
    commands.add_parser("taxonomy", help="print the categories and suggested areas")
    args = parser.parse_args(argv)
    if args.command == "taxonomy":
        print(json.dumps(_taxonomy(), indent=2))
        return EXIT_OK
    try:
        inputs = _load(args.path)
    except CliError as exc:
        print(f"classification {args.command}: {exc}", file=sys.stderr)
        return EXIT_ERROR
    results = [classify(item).model_dump(mode="json") for item in inputs]
    print(json.dumps(results, indent=2))
    return EXIT_OK
