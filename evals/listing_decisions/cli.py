"""Command line for the offline M14 listing evaluation (`python -m evals.listing_decisions`).

* `evaluate --set FILE --raw-dir DIR --out FILE [--outcomes FILE] [--split test
  --allow-test-split]`: write the `le-report-1` report. The output file must not exist. Exit 0
  when every check passed, 1 when a check failed (the report is still written), 2 on invalid
  input (nothing is written).

* `prepare --flipkart-csv FILE --smartphones-csv FILE --laptops-csv FILE --out-dir DIR
  [--seed S]`: build the frozen `le-1` set from the pinned local source files (M14-6). The output
  directory must not exist; use a git-ignored location such as `data/processed/`. Exit 0 on
  success, 2 on invalid input (nothing is written).

There is no labelling command and no runner: nothing here makes a provider call.
"""

import argparse
import sys
from pathlib import Path

from evals.listing_decisions.contracts import EvalSetError, Split, load_set
from evals.listing_decisions.evaluate import evaluate, render
from evals.listing_decisions.prepare import DEFAULT_SEED, prepare


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
    build = commands.add_parser("prepare")
    build.add_argument("--flipkart-csv", type=Path, required=True)
    build.add_argument("--smartphones-csv", type=Path, required=True)
    build.add_argument("--laptops-csv", type=Path, required=True)
    build.add_argument("--out-dir", type=Path, required=True)
    build.add_argument("--seed", default=DEFAULT_SEED)
    return parser


def _prepare(args: argparse.Namespace) -> int:
    inputs = {
        "flipkart": args.flipkart_csv,
        "smartphones": args.smartphones_csv,
        "laptop_price": args.laptops_csv,
    }
    try:
        manifest = prepare(inputs, args.out_dir, seed=args.seed)
    except EvalSetError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"set_sha256 {manifest['set_sha256']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "prepare":
        return _prepare(args)
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
