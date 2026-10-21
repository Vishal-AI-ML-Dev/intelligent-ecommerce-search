"""Command line for the offline M14 listing evaluation (`python -m evals.listing_decisions`).

* `evaluate --set FILE --raw-dir DIR --out FILE [--outcomes FILE] [--split test
  --allow-test-split]`: write the `le-report-1` report. The output file must not exist. Exit 0
  when every check passed, 1 when a check failed (the report is still written), 2 on invalid
  input (nothing is written).

There is no labelling command and no runner: M14-4 makes no provider call.
"""

import argparse
import sys
from pathlib import Path

from evals.listing_decisions.contracts import EvalSetError, Split, load_set
from evals.listing_decisions.evaluate import evaluate, render


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m evals.listing_decisions")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("evaluate")
    run.add_argument("--set", type=Path, required=True)
    run.add_argument("--raw-dir", type=Path, required=True)
    run.add_argument("--outcomes", type=Path)
    run.add_argument("--out", type=Path, required=True)
    run.add_argument("--split", choices=[s.value for s in Split], default=Split.DEV.value)
    run.add_argument("--allow-test-split", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report, failed = evaluate(
            load_set(args.set),
            args.raw_dir,
            args.outcomes,
            split=Split(args.split),
            allow_test_split=args.allow_test_split,
        )
        with args.out.open("xb") as handle:
            handle.write(render(report))
    except FileExistsError:
        print("error: the output file already exists", file=sys.stderr)
        return 2
    except EvalSetError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    for check in failed:
        print(f"failed check: {check}", file=sys.stderr)
    return 1 if failed else 0
