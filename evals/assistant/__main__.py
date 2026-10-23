"""Assistant evaluation (Milestone 17).

Usage:
    uv run python -m evals.assistant [mocked] [--cases PATH] [--annotations PATH] [--out DIR]
    uv run python -m evals.assistant live --cases PATH --expected-sha256 HEX \\
        --annotations PATH --expected-annotations-sha256 HEX \\
        --max-requests N --min-interval-seconds S --out DIR [--confirm-live] \\
        [--input-usd-per-mtok X --output-usd-per-mtok Y --price-source TEXT]
    uv run python -m evals.assistant judgements --report REPORT.json --judgements FILE.jsonl
    uv run python -m evals.assistant draft --spec SPEC.json --out DIR

`mocked` sends nothing. `live` without `--confirm-live` is a dry run that prints the plan and
sends nothing; with it, it sends real provider requests and is run only with explicit owner
approval of the case file (by SHA-256), the request cap and the interval. `--max-requests` and
`--min-interval-seconds` are this harness's limits, not the provider account's quotas.
`eval` cases need their `aa-1` annotations (`annotations.py`): a missing, mismatched or
unapproved annotation refuses the run (fail closed); `live` also checks the annotation file's
approved SHA-256.
`draft` builds an unsealed draft eval set: it captures V3 evidence once from the local database
and cached models (read-only, offline, no provider), then reuses that frozen capture on every
rebuild (`--recapture` is refused once reviews exist). It records the owner's decisions from
`reviews.jsonl`, each bound to its case's content hash, attaches matching owner approvals from
`annotation_approvals.jsonl`, and writes `cases.draft.jsonl`, `annotations.json`,
`provenance.json` and `review-packet.md`. It makes no review or approval and seals nothing.
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
from evals.assistant.annotations import AnnotationError, Annotations, load_annotations
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


def annotations_for(
    cases: tuple[EvalCase, ...], cases_sha256: str, path: Path | None
) -> Annotations | None:
    """Fail closed: eval cases need a valid, approved annotation set; dev cases may omit it."""
    if path is None:
        if any(case.split == "eval" for case in cases):
            raise AnnotationError("eval cases need their annotations (--annotations)")
        return None
    return load_annotations(path, cases, cases_sha256)


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
    if refusal is None and file_sha256(args.annotations) != args.expected_annotations_sha256:
        refusal = "annotation file SHA-256 does not match the approved value"
    if refusal is None:
        try:
            annotations = annotations_for(cases, sha256, args.annotations)
        except AnnotationError as exc:
            refusal = str(exc)
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
        "annotations_sha256": args.expected_annotations_sha256,
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
    report = build_report(
        run,
        cases_sha256=sha256,
        prices=prices,
        app_limits=app_limits,
        annotations=annotations,
        annotations_sha256=args.expected_annotations_sha256,
    )
    sys.stdout.write(_write(args.out, report))
    complete = run.stopped is None and not run.not_run
    return 0 if complete and report["cases_passed"] == report["cases_scored"] else 1


def _judgements(args: argparse.Namespace) -> int:
    report = json.loads(args.report.read_text("utf-8"))
    judgements = load_judgements(args.judgements, report, file_sha256(args.report))
    print(json.dumps(summarize_judgements(judgements), indent=2))
    return 0


def _draft(args: argparse.Namespace) -> int:
    from evals.assistant.annotations import affects_eligibility
    from evals.assistant.draft import (
        DraftSpec,
        apply_reviews,
        build,
        build_annotations,
        captures_json,
        frozen_capture,
        load_annotation_approvals,
        load_reviews,
        render_packet,
    )

    spec = DraftSpec.model_validate_json(args.spec.read_text("utf-8"))
    path = args.out / "cases.draft.jsonl"
    provenance_path = args.out / "provenance.json"
    reviews = load_reviews(args.out / "reviews.jsonl")
    if args.recapture or not provenance_path.exists():
        if reviews:
            print("refused: re-capturing would change reviewed cases", file=sys.stderr)
            return 2
        from evals.assistant.capture import capture

        run = capture({c.case_id: c.message for c in spec.cases if c.counterpart is None})
    else:  # frozen evidence: a rebuild never searches again
        previous = json.loads(provenance_path.read_text("utf-8"))
        run = frozen_capture(previous, load_cases(path))
    built, details = build(spec, run)
    cases, dropped = apply_reviews(built, reviews)
    args.out.mkdir(parents=True, exist_ok=True)
    # LF on every platform, so the SHA-256 matches the committed blob (.gitattributes eol=lf).
    path.write_text("".join(c.model_dump_json() + "\n" for c in cases), "utf-8", newline="\n")
    load_cases(path)  # the draft must satisfy the aq-2 contract
    sha256 = file_sha256(path)
    reviewed = sum(c.case_review is not None for c in cases)
    approvals = load_annotation_approvals(args.out / "annotation_approvals.jsonl")
    annotation_set = build_annotations(spec, cases, details, reviews, approvals, sha256)
    annotations_path = args.out / "annotations.json"
    annotations_path.write_text(
        annotation_set.model_dump_json(indent=2) + "\n", "utf-8", newline="\n"
    )
    annotations_sha256 = file_sha256(annotations_path)
    pending = [
        a.case_id for a in annotation_set.annotations if affects_eligibility(a) and not a.approval
    ]
    provenance = {
        "status": spec.status,
        "spec_version": spec.spec_version,
        "set_name": spec.set_name,
        "authorship_note": spec.authorship_note,
        "policies": [p.model_dump(mode="json") for p in spec.policies],
        "reviews_recorded": reviewed,
        "dropped_cases": dropped,
        "all_reviewed": reviewed == len(cases),
        "sealed": False,
        "cases_file": path.name,
        "cases_sha256_draft": sha256,
        "annotations_file": annotations_path.name,
        "annotations_sha256_draft": annotations_sha256,
        "annotations_pending_approval": pending,
        "captured_at": run.captured_at.isoformat(),
        "top_k": run.top_k,
        "environment": run.environment,
        "catalog_extras_used_for_fit": run.extras,
        "captures": captures_json(run),
        "cases": details,
    }
    provenance_path.write_text(
        json.dumps(provenance, indent=2, default=str) + "\n", "utf-8", newline="\n"
    )
    (args.out / "review-packet.md").write_text(
        render_packet(spec, cases, details, run, sha256, annotation_set, annotations_sha256),
        "utf-8",
        newline="\n",
    )
    flagged = sum(bool(d["flags"]) for d in details.values())
    summary = {"cases": len(cases), "reviewed": reviewed, "dropped": len(dropped)}
    summary["annotations_pending_approval"] = len(pending)
    print(json.dumps({**summary, "flagged": flagged, "cases_sha256_draft": sha256}))
    return 0


def main(argv: list[str] | None = None, provider_factory: ProviderFactory = _gemini) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.assistant", description=__doc__)
    sub = parser.add_subparsers(dest="command")
    mocked = sub.add_parser("mocked")
    live = sub.add_parser("live")
    judge = sub.add_parser("judgements")
    draft = sub.add_parser("draft")
    for p in (parser, mocked):
        p.add_argument("--cases", type=Path, default=DEFAULT_CASES)
        p.add_argument("--annotations", type=Path)
        p.add_argument("--out", type=Path)
    live.add_argument("--cases", type=Path, required=True)
    live.add_argument("--expected-sha256", required=True)
    live.add_argument("--annotations", type=Path, required=True)
    live.add_argument("--expected-annotations-sha256", required=True)
    live.add_argument("--max-requests", type=int, required=True)
    live.add_argument("--min-interval-seconds", type=float, required=True)
    live.add_argument("--out", type=Path, required=True)
    live.add_argument("--confirm-live", action="store_true")
    live.add_argument("--input-usd-per-mtok")
    live.add_argument("--output-usd-per-mtok")
    live.add_argument("--price-source")
    judge.add_argument("--report", type=Path, required=True)
    judge.add_argument("--judgements", type=Path, required=True)
    draft.add_argument("--spec", type=Path, required=True)
    draft.add_argument("--out", type=Path, required=True)
    draft.add_argument("--recapture", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "live":
        if args.max_requests < 1 or args.min_interval_seconds < 0:
            parser.error("--max-requests must be >= 1 and --min-interval-seconds >= 0")
        return _live(args, provider_factory)
    if args.command == "judgements":
        return _judgements(args)
    if args.command == "draft":
        return _draft(args)
    cases = load_cases(args.cases)
    sha256 = file_sha256(args.cases)
    try:
        annotations = annotations_for(cases, sha256, args.annotations)
    except AnnotationError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    report = build_report(
        run_cases(cases),
        cases_sha256=sha256,
        annotations=annotations,
        annotations_sha256=None if args.annotations is None else file_sha256(args.annotations),
    )
    sys.stdout.write(_write(args.out, report))
    return 0 if report["cases_passed"] == report["cases_scored"] else 1


if __name__ == "__main__":
    sys.exit(main())
