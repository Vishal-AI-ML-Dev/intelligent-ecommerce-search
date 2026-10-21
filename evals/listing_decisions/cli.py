"""Command line for the offline M14 listing evaluation (`python -m evals.listing_decisions`).

* `evaluate --set FILE --raw-dir DIR --out FILE [--outcomes FILE] [--split test
  --allow-test-split]`: write the `le-report-1` report. The output file must not exist. Exit 0
  when every check passed, 1 when a check failed (the report is still written), 2 on invalid
  input (nothing is written).

* `prepare --flipkart-csv FILE --smartphones-csv FILE --laptops-csv FILE --out-dir DIR
  [--seed S]`: build the frozen `le-1` set from the pinned local source files (M14-6). The output
  directory must not exist; use a git-ignored location such as `data/processed/`. Exit 0 on
  success, 2 on invalid input (nothing is written).

* `run --set-dir DIR --run-id ID --max-items N`: the capped, journalled runner over the
  set's dev items. Dry run by default: synthetic offline transport, no credentials, no
  network. `--live` sends listing content to Jev. It needs `--input-tokens-per-call`,
  `--output-tokens-per-call`, `--price-input-usd-per-million`, `--price-output-usd-per-million`,
  `--price-checked-on`, `--price-source`, `--max-estimated-usd` and `--send-approval-ref`, and a
  separate owner send approval. `--smoke` limits a run to 5 items. `--resume-approved`
  continues a blocked run, never one with unknown cost.
* `export --set-dir DIR --run-id ID --out FILE`: write `le-outcomes-1` from the journal.
* `status --set-dir DIR --run-id ID`: print the run's accounting (known spend and
  unaccounted attempts reported separately).

There is no labelling command. Only `run --live` makes a provider call.
"""

import argparse
import dataclasses
import json
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

from evals.listing_decisions.contracts import EvalSetError, Split, load_set
from evals.listing_decisions.evaluate import evaluate, render
from evals.listing_decisions.prepare import DEFAULT_SEED, prepare
from evals.listing_decisions.runner import (
    JOURNAL_FILE,
    RUN_FILE,
    RUNS_DIR,
    Allowance,
    Pricing,
    RunHeader,
    RunMode,
    dry_run_factory,
    export_outcomes,
    live_factory,
    make_header,
    read_journal,
    run,
    summarize,
)


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
    runner = commands.add_parser("run")
    runner.add_argument("--set-dir", type=Path, required=True)
    runner.add_argument("--run-id", required=True)
    runner.add_argument("--max-items", type=int, required=True)
    runner.add_argument("--max-attempts", type=int)
    runner.add_argument("--live", action="store_true")
    runner.add_argument("--smoke", action="store_true")
    runner.add_argument("--resume-approved", action="store_true")
    runner.add_argument("--input-tokens-per-call", type=int)
    runner.add_argument("--output-tokens-per-call", type=int)
    runner.add_argument("--price-input-usd-per-million", type=Decimal)
    runner.add_argument("--price-output-usd-per-million", type=Decimal)
    runner.add_argument("--price-checked-on", type=date.fromisoformat)
    runner.add_argument("--price-source")
    runner.add_argument("--max-estimated-usd", type=Decimal)
    runner.add_argument("--send-approval-ref")
    for name in ("export", "status"):
        sub = commands.add_parser(name)
        sub.add_argument("--set-dir", type=Path, required=True)
        sub.add_argument("--run-id", required=True)
        if name == "export":
            sub.add_argument("--out", type=Path, required=True)
    return parser


def _priced(args: argparse.Namespace) -> dict:
    allowance = pricing = None
    if args.input_tokens_per_call is not None or args.output_tokens_per_call is not None:
        allowance = Allowance(
            input_tokens_per_call=args.input_tokens_per_call,
            output_tokens_per_call=args.output_tokens_per_call,
        )
    price_args = (
        args.price_input_usd_per_million,
        args.price_output_usd_per_million,
        args.price_checked_on,
        args.price_source,
    )
    if any(v is not None for v in price_args):
        pricing = Pricing(
            input_usd_per_million=args.price_input_usd_per_million,
            output_usd_per_million=args.price_output_usd_per_million,
            checked_on=args.price_checked_on,
            source=args.price_source,
        )
    return {
        "allowance": allowance,
        "pricing": pricing,
        "max_estimated_usd": args.max_estimated_usd,
    }


def _run(args: argparse.Namespace) -> int:
    from pydantic import ValidationError

    try:
        eval_set = load_set(args.set_dir / "set.json")
        header = make_header(
            eval_set,
            run_id=args.run_id,
            mode=RunMode.LIVE if args.live else RunMode.DRY_RUN,
            max_items=args.max_items,
            max_attempts=args.max_attempts,
            smoke=args.smoke,
            send_approval_ref=args.send_approval_ref,
            **_priced(args),
        )
        secret = None
        if header.mode is RunMode.LIVE:
            from ecommerce_search.config import get_settings

            factory, secret = live_factory(get_settings())
        else:
            factory = dry_run_factory()
        summary = run(
            args.set_dir,
            eval_set,
            header,
            factory,
            resume_approved=args.resume_approved,
            secret=secret,
        )
    except (EvalSetError, ValidationError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(dataclasses.asdict(summary), default=str, sort_keys=True))
    return 0 if summary.complete else 1


def _export_or_status(args: argparse.Namespace) -> int:
    try:
        if args.command == "export":
            counts = export_outcomes(args.set_dir, args.run_id, args.out)
            print(json.dumps(counts, sort_keys=True))
            return 0
        run_dir = args.set_dir / RUNS_DIR / args.run_id
        header = RunHeader.model_validate_json((run_dir / RUN_FILE).read_bytes())
        summary = summarize(header, read_journal(run_dir / JOURNAL_FILE))
    except (EvalSetError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(dataclasses.asdict(summary), default=str, sort_keys=True))
    return 0


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
    if args.command == "run":
        return _run(args)
    if args.command in ("export", "status"):
        return _export_or_status(args)
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
