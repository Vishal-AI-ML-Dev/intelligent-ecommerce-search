"""Assistant evaluation (Milestone 17).

Usage:
    uv run python -m evals.assistant [mocked] [--cases PATH] [--annotations PATH] [--out DIR]
        [--run-id ID]
    uv run python -m evals.assistant live --run-id ID --cases PATH --expected-sha256 HEX \\
        --annotations PATH --expected-annotations-sha256 HEX \\
        --max-requests N --min-interval-seconds S --out DIR [--only IDS.txt] [--confirm-live] \\
        [--input-usd-per-mtok X --output-usd-per-mtok Y --price-source TEXT]
    uv run python -m evals.assistant judgements --report REPORT.json --judgements FILE.jsonl \\
        [--annotations FILE.json [--provenance FILE.json] [--pending FILE.json] [--markdown]]
    uv run python -m evals.assistant draft --spec SPEC.json --out DIR
    uv run python -m evals.assistant claims --run-dir RUN --cases CASES --spec SPEC --out DIR \
        [--adjudications FILE]

`mocked` sends nothing. `live` without `--confirm-live` is a dry run that prints the plan and
sends nothing; with it, it sends real provider requests and is run only with explicit owner
approval of the case file (by SHA-256), the request cap and the interval. `--max-requests` and
`--min-interval-seconds` are this harness's limits, not the provider account's quotas.
`eval` cases need their `aa-1` annotations (`annotations.py`): a missing, mismatched or
unapproved annotation refuses the run (fail closed); `live` also checks the annotation file's
approved SHA-256.
A run with `--out` saves every processed case at once (`responses.jsonl`, saved API responses,
and `calls.jsonl`, per-call telemetry; `persist.py`), then `report.json`/`report.md` bound to
their SHA-256 values and `review-sheet.md`. Runs are never merged: a non-empty `--out` is
refused. `--only IDS.txt` runs a subset of the case file: the whole frozen file and its
annotations are validated first, then the listed cases run in file order (empty, unknown or
duplicate IDs are refused); the selection's SHA-256 and IDs are recorded and the report is marked
as a subset run with no full-dataset verdict. `live` needs `--run-id` and refuses uncommitted
tracked changes or untracked/ignored importable files under src/, evals/ or tests/; the full
evaluated commit is recorded.
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
from evals.assistant.judgements import (
    human_review_report,
    load_judgements,
    render_human_review,
    summarize_judgements,
)
from evals.assistant.persist import (
    RUN_ID,
    RunBinding,
    RunWriter,
    git_provenance,
    load_responses,
    render_review_sheet,
)
from evals.assistant.report import build_report, render_markdown
from evals.assistant.runner import RunResult, run_cases, run_live

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
        text = json.dumps(report, indent=2) + "\n"
        (out / "report.json").write_text(text, "utf-8", newline="\n")
        (out / "report.md").write_text(markdown, "utf-8", newline="\n")
    return markdown


SUBSET_NOTE = (
    "subset run: the manifest thresholds were set for the full case-file populations "
    "(e.g. false refusals <= 3/28); they are not converted or applied to this subset. "
    "Counts are reported only; no full-dataset verdict."
)


def select_cases(
    cases: tuple[EvalCase, ...], path: Path | None, cases_sha256: str
) -> tuple[tuple[EvalCase, ...], dict[str, Any] | None]:
    """The listed cases in file order, after the whole file has been validated."""
    if path is None:
        return cases, None
    ids = [line.strip() for line in path.read_text("utf-8").splitlines() if line.strip()]
    if not ids:
        raise ValueError("the selection is empty")
    if len(set(ids)) != len(ids):
        raise ValueError("the selection lists a case more than once")
    unknown = sorted(set(ids) - {case.case_id for case in cases})
    if unknown:
        raise ValueError(f"the selection lists unknown cases {unknown}")
    chosen = set(ids)
    selected = tuple(case for case in cases if case.case_id in chosen)
    return selected, {
        "selection_file": path.name,
        "selection_sha256": file_sha256(path),
        "case_ids": [case.case_id for case in selected],
        "parent_cases": len(cases),
        "parent_cases_sha256": cases_sha256,
        "threshold_applicability": SUBSET_NOTE,
    }


def _out_refusal(out: Path) -> str | None:
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        return f"output directory {out} exists and is not empty; runs are never merged"
    return None


def _provenance_refusal(provenance: dict[str, Any]) -> str | None:
    if provenance["tracked_modified"] or provenance["untracked_code"]:
        changed = provenance["tracked_modified"] + provenance["untracked_code"]
        return f"evaluation code differs from commit {provenance['code_commit']}: {changed}"
    return None


def _finish(
    out: Path | None,
    run: RunResult,
    cases: tuple[EvalCase, ...],
    binding: dict[str, Any],
    **report_args: Any,
) -> dict[str, Any]:
    """The report, bound to the SHA-256 of the files saved during the run, then the sheet."""
    if out is not None:
        for name in ("responses", "calls"):
            path = out / f"{name}.jsonl"
            binding[f"{name}_sha256"] = file_sha256(path) if path.exists() else None
    report = build_report(run, binding=binding, **report_args)
    sys.stdout.write(_write(out, report))
    if out is not None:
        records = load_responses(out / "responses.jsonl")
        sheet = render_review_sheet(records, {c.case_id: c for c in cases}, report)
        (out / "review-sheet.md").write_text(sheet + "\n", "utf-8", newline="\n")
    return report


def _gemini(settings: Any) -> GenerationProvider:
    from ecommerce_search.generation.gemini_provider import GeminiConfig, GeminiGenerationProvider

    return GeminiGenerationProvider(
        GeminiConfig(
            api_key=settings.gemini_api_key,
            model=settings.assistant_generation_model,
            timeout_seconds=settings.assistant_generation_timeout_seconds,
        )
    )


def _settings() -> Any:
    from ecommerce_search.config import Settings

    return Settings()


def _live(
    args: argparse.Namespace,
    provider_factory: ProviderFactory,
    provenance_check: Callable[[], dict[str, Any]],
    settings_factory: Callable[[], Any] = _settings,
) -> int:
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
    provenance = provenance_check()
    if refusal is None:
        refusal = _out_refusal(args.out) or _provenance_refusal(provenance)
    if refusal is None:
        try:
            run_cases_, selection = select_cases(cases, args.only, sha256)
        except ValueError as exc:
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
    generation_cases = sum(case.generation == "on" for case in run_cases_)
    settings = settings_factory()
    run_settings = {
        "assistant_generation_model": settings.assistant_generation_model,
        "assistant_generation_timeout_seconds": settings.assistant_generation_timeout_seconds,
        "assistant_generation_deadline_seconds": settings.assistant_generation_deadline_seconds,
        "assistant_generation_max_output_tokens": settings.assistant_generation_max_output_tokens,
        "assistant_evidence_k": settings.assistant_evidence_k,
        "gemini_api_key_configured": settings.gemini_api_key is not None,
    }
    plan = {
        "run_id": args.run_id,
        "code_commit": provenance["code_commit"],
        "out": str(args.out),
        "run_settings": run_settings,
        "cases": len(run_cases_),
        "cases_sha256": sha256,
        "annotations_sha256": args.expected_annotations_sha256,
        "case_selection": selection,
        "generation_on_cases": generation_cases,
        "max_possible_requests_without_cap": 2 * generation_cases,
        **app_limits,
    }
    if not args.confirm_live:
        print(json.dumps({"dry_run": True, **plan}, indent=2))
        return 0

    if settings.gemini_api_key is None:
        print("GEMINI_API_KEY is not configured; no request sent.", file=sys.stderr)
        return 2
    limits = AssistantLimits(
        evidence_k=settings.assistant_evidence_k,
        max_output_tokens=settings.assistant_generation_max_output_tokens,
        deadline_seconds=settings.assistant_generation_deadline_seconds,
    )
    writer = RunWriter(
        args.out,
        RunBinding(
            args.run_id,
            provenance["code_commit"],
            sha256,
            args.expected_annotations_sha256,
            None if selection is None else selection["selection_sha256"],
        ),
    )
    run = run_live(
        run_cases_,
        provider_factory(settings),
        max_requests=args.max_requests,
        min_interval_seconds=args.min_interval_seconds,
        limits=limits,
        on_result=writer.write,
    )
    binding = {
        "run_id": args.run_id,
        "code_commit": provenance["code_commit"],
        "code_provenance": provenance,
        "run_settings": run_settings,
        "case_selection": selection,
    }
    report = _finish(
        args.out,
        run,
        cases,
        binding,
        cases_sha256=sha256,
        prices=prices,
        app_limits=app_limits,
        annotations=annotations,
        annotations_sha256=args.expected_annotations_sha256,
    )
    complete = run.stopped is None and not run.not_run
    return 0 if complete and report["cases_passed"] == report["cases_scored"] else 1


def _bound_sidecar(path: Path | None, report_sha256: str) -> dict[str, Any] | None:
    if path is None:
        return None
    data = json.loads(path.read_text("utf-8"))
    if data.get("report_sha256") != report_sha256:
        raise ValueError(f"{path.name} is bound to a different report")
    return data


def _judgements(args: argparse.Namespace) -> int:
    report = json.loads(args.report.read_text("utf-8"))
    report_sha256 = file_sha256(args.report)
    judgements = load_judgements(args.judgements, report, report_sha256)
    if args.annotations is None:
        print(json.dumps(summarize_judgements(judgements, report), indent=2))
        return 0
    if file_sha256(args.annotations) != report.get("annotations_sha256"):
        print("refused: annotations are not the ones this run used", file=sys.stderr)
        return 2
    annotation_set = json.loads(args.annotations.read_text("utf-8"))
    if annotation_set["cases_sha256"] != report["cases_sha256"]:
        print("refused: annotations are bound to a different case file", file=sys.stderr)
        return 2
    names = {"known_limitation_diagnostic": "diagnostic", "contract_limitation": "contract"}
    classes = {
        a["case_id"]: names.get(a["report_class"], "scored") for a in annotation_set["annotations"]
    }
    selection = report.get("case_selection")
    population = classes if selection is None else {c: classes[c] for c in selection["case_ids"]}
    planned = {name: list(population.values()).count(name) for name in set(classes.values())}
    try:
        provenance = _bound_sidecar(args.provenance, report_sha256)
        pending = _bound_sidecar(args.pending, report_sha256)
    except ValueError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    summary = human_review_report(
        judgements,
        report,
        classes,
        provenance=None if provenance is None else provenance["judgements"],
        pending=pending,
        planned=planned,
    )
    sys.stdout.write(
        render_human_review(summary) if args.markdown else json.dumps(summary, indent=2) + "\n"
    )
    return 0


def _claims(args: argparse.Namespace) -> int:
    """Post-hoc unsupported-claims extraction for one run (`claims.py`); never pools runs."""
    from evals.assistant.claims import extract, load_adjudications, summarize

    refusal = _out_refusal(args.out)
    if refusal is not None:
        print(f"refused: {refusal}", file=sys.stderr)
        return 2
    if not (args.run_dir / "responses.jsonl").exists():
        print("refused: this run has no saved responses; measurement unavailable", file=sys.stderr)
        return 2
    cases = {case.case_id: case for case in load_cases(args.cases)}
    spec = json.loads(args.spec.read_text("utf-8"))
    claims, omissions, binding = extract(args.run_dir, cases, spec)
    args.out.mkdir(parents=True, exist_ok=True)
    claims_path = args.out / "claims.uc-1.jsonl"
    lines = "".join(json.dumps(c, ensure_ascii=False) + "\n" for c in claims)
    claims_path.write_text(lines, "utf-8", newline="\n")
    lines = "".join(json.dumps(o, ensure_ascii=False) + "\n" for o in omissions)
    (args.out / "omissions.jsonl").write_text(lines, "utf-8", newline="\n")
    claims_sha256 = file_sha256(claims_path)
    adjudications = load_adjudications(args.adjudications, claims_sha256)
    summary = summarize(claims, omissions, adjudications, binding)
    summary["claims_sha256"] = claims_sha256
    text = json.dumps(summary, indent=2) + "\n"
    (args.out / "claims-summary.json").write_text(text, "utf-8", newline="\n")
    print(json.dumps(summary, indent=2))
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


def main(
    argv: list[str] | None = None,
    provider_factory: ProviderFactory = _gemini,
    provenance_check: Callable[[], dict[str, Any]] = git_provenance,
    settings_factory: Callable[[], Any] = _settings,
) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.assistant", description=__doc__)
    sub = parser.add_subparsers(dest="command")
    mocked = sub.add_parser("mocked")
    live = sub.add_parser("live")
    judge = sub.add_parser("judgements")
    draft = sub.add_parser("draft")
    claims = sub.add_parser("claims")
    claims.add_argument("--run-dir", type=Path, required=True)
    claims.add_argument("--cases", type=Path, required=True)
    claims.add_argument("--spec", type=Path, required=True)
    claims.add_argument("--out", type=Path, required=True)
    claims.add_argument("--adjudications", type=Path)
    for p in (parser, mocked):
        p.add_argument("--cases", type=Path, default=DEFAULT_CASES)
        p.add_argument("--annotations", type=Path)
        p.add_argument("--out", type=Path)
        p.add_argument("--run-id", default="mocked")
        p.add_argument("--only", type=Path)
    live.add_argument("--run-id", required=True)
    live.add_argument("--cases", type=Path, required=True)
    live.add_argument("--expected-sha256", required=True)
    live.add_argument("--annotations", type=Path, required=True)
    live.add_argument("--expected-annotations-sha256", required=True)
    live.add_argument("--max-requests", type=int, required=True)
    live.add_argument("--min-interval-seconds", type=float, required=True)
    live.add_argument("--out", type=Path, required=True)
    live.add_argument("--only", type=Path)
    live.add_argument("--confirm-live", action="store_true")
    live.add_argument("--input-usd-per-mtok")
    live.add_argument("--output-usd-per-mtok")
    live.add_argument("--price-source")
    judge.add_argument("--report", type=Path, required=True)
    judge.add_argument("--judgements", type=Path, required=True)
    judge.add_argument("--annotations", type=Path)
    judge.add_argument("--provenance", type=Path)
    judge.add_argument("--pending", type=Path)
    judge.add_argument("--markdown", action="store_true")
    draft.add_argument("--spec", type=Path, required=True)
    draft.add_argument("--out", type=Path, required=True)
    draft.add_argument("--recapture", action="store_true")
    args = parser.parse_args(argv)
    if getattr(args, "run_id", None) is not None and not RUN_ID.match(args.run_id):
        parser.error("--run-id must match ^[a-z0-9][a-z0-9-]{2,63}$")
    if args.command == "live":
        if args.max_requests < 1 or args.min_interval_seconds < 0:
            parser.error("--max-requests must be >= 1 and --min-interval-seconds >= 0")
        return _live(args, provider_factory, provenance_check, settings_factory)
    if args.command == "judgements":
        return _judgements(args)
    if args.command == "draft":
        return _draft(args)
    if args.command == "claims":
        return _claims(args)
    cases = load_cases(args.cases)
    sha256 = file_sha256(args.cases)
    try:
        annotations = annotations_for(cases, sha256, args.annotations)
    except AnnotationError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    annotations_sha256 = None if args.annotations is None else file_sha256(args.annotations)
    try:
        selected, selection = select_cases(cases, args.only, sha256)
    except ValueError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    writer = None
    binding: dict[str, Any] = {"run_id": args.run_id, "case_selection": selection}
    if args.out is not None:
        refusal = _out_refusal(args.out)
        if refusal is not None:
            print(f"refused: {refusal}", file=sys.stderr)
            return 2
        provenance = provenance_check()
        binding.update(code_commit=provenance["code_commit"], code_provenance=provenance)
        writer = RunWriter(
            args.out,
            RunBinding(
                args.run_id,
                provenance["code_commit"],
                sha256,
                annotations_sha256,
                None if selection is None else selection["selection_sha256"],
            ),
        )
    run = run_cases(selected, on_result=None if writer is None else writer.write)
    report = _finish(
        args.out,
        run,
        cases,
        binding,
        cases_sha256=sha256,
        annotations=annotations,
        annotations_sha256=annotations_sha256,
    )
    return 0 if report["cases_passed"] == report["cases_scored"] else 1


if __name__ == "__main__":
    sys.exit(main())
