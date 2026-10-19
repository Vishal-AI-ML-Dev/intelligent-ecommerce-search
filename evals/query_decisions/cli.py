"""Command line for the M12 query-decision diagnostic (`python -m evals.query_decisions`).

Commands (the work directory defaults to the git-ignored `data/processed/m12_diagnostic/`):

* `label --annotator ann-1`: blind labelling, query text only (see `labeling`).
* `seal`: seal the queries and labels once every query is labelled.
* `run --run-id ID --max-attempts N`: dry run (default). Drives the real provider against an
  in-memory synthetic transport; no network, no key, not Jev output.
* `run --run-id ID --max-attempts N --live --price-input-usd-per-million P
  --price-output-usd-per-million P --price-checked-on YYYY-MM-DD --max-estimated-usd X`:
  paid live calls. Needs explicit owner approval before it is ever run.
* `run ... --live --approve-ai-authored`: records the operator's decision to send AI-authored
  queries in a live run (required when the batch has any). It does not authorize paid calls.
  Batches with synthetic test fixtures are refused live even with it.
* `run ... --resume-approved`: continue a run blocked by a stop or an unknown outcome.
* `evaluate --run-id ID [--out FILE]`: offline diagnostic report (JSON, `qd-diagnostic-2`).
* `report --run-id ID [--out FILE] [--bins N]`: offline metrics report (JSON, `qd-report-1`):
  category and intent metrics, raw-probability diagnostics, latency and estimated cost.
"""

import argparse
import json
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

from ecommerce_search.decision.jev_provider import JevConfigurationError
from evals.contracts.canonical import ContractError
from evals.query_decisions.contracts import QueryDecisionError
from evals.query_decisions.labeling import WORK_ROOT, label_session, load_sealed, seal_labels
from evals.query_decisions.runner import (
    DRY_RUN_MODEL,
    RunHeader,
    RunMode,
    dry_run_provider,
    live_provider_factory,
    live_refusal,
    run_diagnostic,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m evals.query_decisions")
    parser.add_argument("--work-dir", type=Path, default=WORK_ROOT)
    commands = parser.add_subparsers(dest="command", required=True)
    label = commands.add_parser("label")
    label.add_argument("--annotator", required=True)
    commands.add_parser("seal")
    run = commands.add_parser("run")
    run.add_argument("--run-id", required=True)
    run.add_argument("--max-attempts", type=int, required=True)
    run.add_argument("--live", action="store_true")
    run.add_argument("--price-input-usd-per-million", type=Decimal)
    run.add_argument("--price-output-usd-per-million", type=Decimal)
    run.add_argument("--price-checked-on", type=date.fromisoformat)
    run.add_argument("--max-estimated-usd", type=Decimal)
    run.add_argument("--approve-ai-authored", action="store_true")
    run.add_argument("--resume-approved", action="store_true")
    evaluate = commands.add_parser("evaluate")
    evaluate.add_argument("--run-id", required=True)
    evaluate.add_argument("--out", type=Path)
    report = commands.add_parser("report")
    report.add_argument("--run-id", required=True)
    report.add_argument("--out", type=Path)
    report.add_argument("--bins", type=int, default=10)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "label":
            saved = label_session(args.work_dir, args.annotator, ask=input, say=print)
            print(f"saved {saved} label(s)")
        elif args.command == "seal":
            seal = seal_labels(args.work_dir)
            print(f"sealed {seal.query_count} queries; labels {seal.labels_sha256}")
        elif args.command == "run":
            return _run(args)
        elif args.command == "report":
            from evals.query_decisions.reporting import build_report

            _emit(build_report(args.work_dir, args.run_id, bins=args.bins), args.out)
        else:
            from evals.query_decisions.evaluate import evaluate

            _emit(evaluate(args.work_dir, args.run_id), args.out)
    except (ContractError, QueryDecisionError, ValueError, FileExistsError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    except JevConfigurationError:  # fixed text only; never the input or a chained exception
        print("error: jev configuration invalid", file=sys.stderr)
        return 2
    return 0


def _emit(report: dict, out: Path | None) -> None:
    text = json.dumps(report, indent=2, sort_keys=True)
    if out is None:
        print(text)
        return
    with open(out, "x", encoding="utf-8", newline="\n") as handle:  # never overwrites
        handle.write(text + "\n")
    print(f"report written to {out}")


def _run(args: argparse.Namespace) -> int:
    seal, _queries, _labels = load_sealed(args.work_dir)
    secret = None
    if args.live:
        # Refuse before Settings reads .env and the key. run_diagnostic checks again.
        live_arguments = (
            args.price_input_usd_per_million,
            args.price_output_usd_per_million,
            args.price_checked_on,
            args.max_estimated_usd,
        )
        refusal = live_refusal(
            seal,
            prices_complete=None not in live_arguments,
            ai_authored_approved=args.approve_ai_authored,
        )
        if refusal is not None:
            raise QueryDecisionError(refusal)
        from ecommerce_search.config import Settings

        factory, model, secret = live_provider_factory(Settings())
    else:
        factory, model = dry_run_provider, DRY_RUN_MODEL
    header = RunHeader(
        run_id=args.run_id,
        mode=RunMode.LIVE if args.live else RunMode.DRY_RUN,
        queries_sha256=seal.queries_sha256,
        labels_sha256=seal.labels_sha256,
        model=model,
        max_attempts=args.max_attempts,
        price_usd_per_million_input=args.price_input_usd_per_million,
        price_usd_per_million_output=args.price_output_usd_per_million,
        price_checked_on=args.price_checked_on,
        max_estimated_usd=args.max_estimated_usd,
        ai_authored_approved=args.approve_ai_authored,
    )
    summary = run_diagnostic(
        args.work_dir, header, factory, secret=secret, resume_approved=args.resume_approved
    )
    return 0 if summary.stopped is None else 3
