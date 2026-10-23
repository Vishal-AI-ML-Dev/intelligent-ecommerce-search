"""Assistant evaluation (Milestone 17).

Usage:
    uv run python -m evals.assistant [mocked] [--cases PATH] [--out DIR]
    uv run python -m evals.assistant live --cases PATH --expected-sha256 HEX \\
        --max-requests N --min-interval-seconds S --out DIR [--confirm-live] \\
        [--input-usd-per-mtok X --output-usd-per-mtok Y --price-source TEXT]
    uv run python -m evals.assistant judgements --report REPORT.json --judgements FILE.jsonl

`mocked` sends nothing. `live` without `--confirm-live` is a dry run that prints the plan and
sends nothing; with it, it sends real provider requests and is run only with explicit owner
approval of the case file (by SHA-256), the request cap and the interval. `--max-requests` and
`--min-interval-seconds` are this harness's limits, not the provider account's quotas.
Exit status 1 when any case fails or the run stopped early.
"""

import argparse
import json
import sys
from collections.abc import Callable
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from ecommerce_search.assistant.workflow import AssistantLimits
from ecommerce_search.generation.provider import GenerationProvider
from evals.assistant.contracts import EvalCase, file_sha256, load_cases
from evals.assistant.judgements import load_judgements, summarize_judgements
from evals.assistant.report import build_report, render_markdown
from evals.assistant.runner import run_cases, run_live

DEFAULT_CASES = Path(__file__).parent / "fixtures" / "synthetic-v1" / "cases.jsonl"
ProviderFactory = Callable[[Any], GenerationProvider]


def live_refusal(cases: tuple[EvalCase, ...], sha256: str, expected_sha256: str) -> str | None:
    if sha256 != expected_sha256:
        return "case file SHA-256 does not match the approved value"
    if any(case.split != "eval" for case in cases):
        return "live runs use eval cases only"
    if any(case.case_review is None for case in cases):
        return "every eval case needs a recorded case review"
    if any(case.scripted for case in cases):
        return "eval cases must not script provider results"
    return None


def _write(out: Path | None, report: dict[str, Any]) -> str:
    markdown = render_markdown(report)
    if out is not None:
        out.mkdir(parents=True, exist_ok=True)
        (out / "report.json").write_text(json.dumps(report, indent=2) + "\n", "utf-8")
        (out / "report.md").write_text(markdown, "utf-8")
    return markdown


def _gemini(settings: Any) -> GenerationProvider:
    from ecommerce_search.generation.gemini_provider import GeminiConfig, GeminiGenerationProvider

    return GeminiGenerationProvider(
        GeminiConfig(
            api_key=settings.gemini_api_key,
            model=settings.assistant_generation_model,
            timeout_seconds=settings.assistant_generation_timeout_seconds,
        )
    )


def _live(args: argparse.Namespace, provider_factory: ProviderFactory) -> int:
    cases = load_cases(args.cases)
    sha256 = file_sha256(args.cases)
    refusal = live_refusal(cases, sha256, args.expected_sha256)
    if refusal is not None:
        print(f"refused: {refusal}; no request sent", file=sys.stderr)
        return 2
    price_args = (args.input_usd_per_mtok, args.output_usd_per_mtok, args.price_source)
    if any(v is not None for v in price_args) and not all(v is not None for v in price_args):
        print("refused: give all three price options or none", file=sys.stderr)
        return 2
    try:
        if args.price_source is not None:
            Decimal(args.input_usd_per_mtok) + Decimal(args.output_usd_per_mtok)
    except InvalidOperation:
        print("refused: prices must be decimal numbers", file=sys.stderr)
        return 2
    prices = (
        None
        if args.price_source is None
        else {
            "input_usd_per_mtok": args.input_usd_per_mtok,
            "output_usd_per_mtok": args.output_usd_per_mtok,
            "price_source": args.price_source,
        }
    )
    app_limits = {
        "max_requests": args.max_requests,
        "min_interval_seconds": args.min_interval_seconds,
        "note": "harness limits, not provider quotas",
    }
    generation_cases = sum(case.generation == "on" for case in cases)
    plan = {
        "cases": len(cases),
        "cases_sha256": sha256,
        "generation_on_cases": generation_cases,
        "max_possible_requests_without_cap": 2 * generation_cases,
        **app_limits,
    }
    if not args.confirm_live:
        print(json.dumps({"dry_run": True, **plan}, indent=2))
        return 0

    from ecommerce_search.config import Settings

    settings = Settings()
    if settings.gemini_api_key is None:
        print("GEMINI_API_KEY is not configured; no request sent.", file=sys.stderr)
        return 2
    limits = AssistantLimits(
        evidence_k=settings.assistant_evidence_k,
        max_output_tokens=settings.assistant_generation_max_output_tokens,
        deadline_seconds=settings.assistant_generation_deadline_seconds,
    )
    run = run_live(
        cases,
        provider_factory(settings),
        max_requests=args.max_requests,
        min_interval_seconds=args.min_interval_seconds,
        limits=limits,
    )
    report = build_report(run, cases_sha256=sha256, prices=prices, app_limits=app_limits)
    sys.stdout.write(_write(args.out, report))
    complete = run.stopped is None and not run.not_run
    return 0 if complete and report["cases_passed"] == report["cases"] else 1


def _judgements(args: argparse.Namespace) -> int:
    report = json.loads(args.report.read_text("utf-8"))
    judgements = load_judgements(args.judgements, report, file_sha256(args.report))
    print(json.dumps(summarize_judgements(judgements), indent=2))
    return 0


def main(argv: list[str] | None = None, provider_factory: ProviderFactory = _gemini) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.assistant", description=__doc__)
    sub = parser.add_subparsers(dest="command")
    mocked = sub.add_parser("mocked")
    live = sub.add_parser("live")
    judge = sub.add_parser("judgements")
    for p in (parser, mocked):
        p.add_argument("--cases", type=Path, default=DEFAULT_CASES)
        p.add_argument("--out", type=Path)
    live.add_argument("--cases", type=Path, required=True)
    live.add_argument("--expected-sha256", required=True)
    live.add_argument("--max-requests", type=int, required=True)
    live.add_argument("--min-interval-seconds", type=float, required=True)
    live.add_argument("--out", type=Path, required=True)
    live.add_argument("--confirm-live", action="store_true")
    live.add_argument("--input-usd-per-mtok")
    live.add_argument("--output-usd-per-mtok")
    live.add_argument("--price-source")
    judge.add_argument("--report", type=Path, required=True)
    judge.add_argument("--judgements", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "live":
        if args.max_requests < 1 or args.min_interval_seconds < 0:
            parser.error("--max-requests must be >= 1 and --min-interval-seconds >= 0")
        return _live(args, provider_factory)
    if args.command == "judgements":
        return _judgements(args)
    cases = load_cases(args.cases)
    report = build_report(run_cases(cases), cases_sha256=file_sha256(args.cases))
    sys.stdout.write(_write(args.out, report))
    return 0 if report["cases_passed"] == report["cases"] else 1


if __name__ == "__main__":
    sys.exit(main())
