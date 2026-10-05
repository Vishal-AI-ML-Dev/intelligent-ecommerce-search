"""Command line of the AI-assisted weak-label development set `weak_dev_v1`.

PROVISIONAL — AI-assisted weak labels with a 100-pair non-blind human audit. Not final
human-reviewed test evidence. The strict Golden Dataset lifecycle is untouched.

Commands:

* `propose`: two fresh offline runs (pinned local cross-encoder), byte comparison, then write
  the proposals, audit queue and proposal manifest (public) and the signals (private).
* `verify`: re-verify the stored generation without a model (byte-identical regeneration).
* `status`: audit progress counts.
* `audit --annotator ann-1`: the non-blind accept/override audit of the 100 queued pairs.
* `validate`: re-check the audit decision file.
* `assemble --annotator ann-1`: after all 100 decisions, seal `weak_dev_sealed`.
* `audit-report`, `metrics`: provisional reports on the sealed weak set.
* `assemble-machine-only --acknowledge-unaudited`: the machine-only path (no human audit):
  568 unaudited machine labels, provisional V0–V3 metrics and report, built twice in fresh
  processes (`evals.weak_labels.machine_only`).
* `verify-machine-only`: regenerate every machine-only file and compare bytes.

Usage:
  uv run --offline python -m evals.weak_labels propose
  uv run --offline python -m evals.weak_labels verify
  uv run python -m evals.weak_labels status
  uv run python -m evals.weak_labels audit --annotator ann-1
  uv run python -m evals.weak_labels validate
  uv run python -m evals.weak_labels assemble --annotator ann-1
  uv run --offline python -m evals.weak_labels audit-report
  uv run --offline python -m evals.weak_labels metrics
  uv run --offline python -m evals.weak_labels assemble-machine-only --acknowledge-unaudited
  uv run --offline python -m evals.weak_labels verify-machine-only
"""

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

from evals.contracts.canonical import ContractError
from evals.runners.golden_io import write_exclusive
from evals.weak_labels import machine_only, reports
from evals.weak_labels.assembly import assemble, verify_sealed
from evals.weak_labels.audit import AUDIT_ROOT, AuditContext, AuditStore, audit
from evals.weak_labels.contracts import AUDIT_SIZE, BANNER, MACHINE_WARNING
from evals.weak_labels.generation import (
    DEFAULT_WEAK_DIR,
    WEAKSET_ROOT,
    load_facts,
    read_generation,
    safe_weak_dir,
    verify_generation,
)


def _now() -> datetime:
    return datetime.now(UTC)


def _context(weak_dir: Path, *, with_provenance: bool) -> AuditContext:
    facts = load_facts(with_provenance=with_provenance)
    generation = (
        verify_generation(facts, weak_dir) if with_provenance else read_generation(facts, weak_dir)
    )
    return AuditContext(facts=facts, generation=generation)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m evals.weak_labels", description=BANNER)
    parser.add_argument("--weak-dir", type=Path, default=DEFAULT_WEAK_DIR)
    parser.add_argument("--audit-root", type=Path, default=AUDIT_ROOT)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("propose", help="two fresh offline runs, compare, write proposals")
    commands.add_parser("verify", help="re-verify the stored generation; writes nothing")
    commands.add_parser("status", help="audit progress counts")
    run = commands.add_parser("audit", help="non-blind accept/override audit")
    run.add_argument("--annotator", required=True)
    commands.add_parser("validate", help="re-check the audit decision file")
    seal = commands.add_parser("assemble", help="seal weak_dev_sealed after 100 decisions")
    seal.add_argument("--annotator", required=True)
    commands.add_parser("audit-report", help="provisional audit report (after the seal)")
    commands.add_parser("metrics", help="provisional V0-V3 metrics (after the seal)")
    machine = commands.add_parser(
        "assemble-machine-only",
        help="finalize unaudited machine-only labels, provisional metrics and report",
    )
    machine.add_argument("--acknowledge-unaudited", action="store_true")
    commands.add_parser("verify-machine-only", help="regenerate machine-only outputs; no writes")
    machine_child = commands.add_parser("_machine_only", help=argparse.SUPPRESS)
    machine_child.add_argument("--out", type=Path, required=True)
    child = commands.add_parser("_generate", help=argparse.SUPPRESS)
    child.add_argument("--out", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):  # the banner's em dash on a non-UTF-8 console
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    try:
        if args.command == "_generate":  # pragma: no cover - loads the model
            from evals.weak_labels.runner import child_main

            return child_main(args.out)
        if args.command == "propose":  # pragma: no cover - loads the model
            from evals.weak_labels.runner import propose

            return propose(args.weak_dir)
        if args.command == "_machine_only":  # pragma: no cover - a fresh child process
            return machine_only.child_main(args.out)
        if args.command in ("assemble-machine-only", "verify-machine-only"):
            paths = machine_only.MachinePaths(weak_dir=args.weak_dir, audit_root=args.audit_root)
            print(MACHINE_WARNING)
            if args.command == "assemble-machine-only":
                manifest = machine_only.assemble_machine_only(
                    paths, acknowledge_unaudited=args.acknowledge_unaudited
                )
                print("determinism: two fresh-process builds byte-identical")
            else:
                manifest = machine_only.verify_machine_only(paths)
                print("verified: every machine-only file regenerates byte for byte")
            print(
                f"{manifest.status}: {manifest.label_count} machine_unreviewed labels; human "
                f"audit coverage {manifest.human_audit_coverage}/{manifest.label_count}"
            )
            return 0
        if args.command == "verify":
            context = _context(args.weak_dir, with_provenance=True)
            manifest = context.generation.manifest
            print(f"verified weak_dev_v1 proposals {context.generation.manifest_sha256}")
            print(
                f"{manifest.proposal_count} proposals, {manifest.audit_count} audit pairs, "
                f"{manifest.requires_review_count} flagged for human review"
            )
            return 0
        if args.command in ("audit-report", "metrics"):
            context = _context(args.weak_dir, with_provenance=args.command == "metrics")
            weak = verify_sealed(context, args.weak_dir)
            if args.command == "audit-report":
                data = reports.audit_report(context, weak)
                files = reports.report_files(
                    "audit_report", data, reports.audit_report_markdown(data)
                )
            else:
                data = reports.provisional_metrics(context, weak)
                files = reports.report_files(
                    "provisional_metrics", data, reports.metrics_markdown(data)
                )
            write_exclusive(safe_weak_dir(args.weak_dir, WEAKSET_ROOT), files)
            print(BANNER)
            print("wrote " + ", ".join(sorted(files)))
            return 0
        context = _context(args.weak_dir, with_provenance=False)
        store = AuditStore(context, args.audit_root)
        if args.command == "audit":
            print(BANNER)
            audit(context, store, annotator=args.annotator, now=_now)
            return 0
        if args.command == "assemble":
            sealed = assemble(
                context, store, annotator=args.annotator, now=_now(), weak_dir=args.weak_dir
            )
            print(BANNER)
            print(
                f"weak_dev_sealed: {sealed.label_count} labels, {sealed.audited_count} audited "
                f"({sealed.accepted_count} accepted, {sealed.override_count} overridden), "
                f"{sealed.machine_unreviewed_count} machine_unreviewed"
            )
            return 0
        decisions = store.load(now=_now())
        if args.command == "status":
            print(f"proposal_manifest {context.generation.manifest_sha256}")
            print(f"{len(decisions)} of {AUDIT_SIZE} audit pairs decided")
            if store.lock_path.exists():
                print("an audit lock is present (a session is running or was hard-killed)")
        else:
            print(f"audit decision file valid: {len(decisions)} decision(s)")
    except ContractError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0
