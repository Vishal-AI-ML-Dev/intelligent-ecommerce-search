"""Gate-tooling commands of `python -m evals.query_decisions` (offline; no call, no key).

Every command takes an explicit `--root` (the directory holding `partitions/`, `thresholds/`,
`consumption/` and `acceptance/`) with no default, so nothing is written to a real diagnostic
folder by accident. Batches are sealed diagnostic work directories, given with `--source DIR
IDS` (comma-separated query ids) or `--batch DIR RUN_ID`.

* `partition create --root R --id ID --role calibration|evaluation
  --screening none|parser_abstains_on_category --source DIR IDS [--source ...]`
* `partition show --root R --id ID`: load and validate one partition.
* `partition check --root R --calibration ID --evaluation ID`: refuse overlapping partitions.
* `sweep --root R --calibration ID --batch DIR RUN_ID [...] --threshold T [--threshold ...]
  [--out FILE]`: gate fills at the supplied thresholds on a **calibration** partition only;
  an evaluation partition is refused, so evaluation data is never used for selection. Nothing
  is selected.
* `freeze --root R --id ID --calibration ID --threshold T --model M` plus every acceptance
  criterion (`--confidence-level`, `--wrong-commit-definition`,
  `--max-wrong-commit-upper-bound`, `--min-coverage`, `--min-accepted-fills`,
  `--max-unknown-or-fallback-rate`, `--max-unclear-rate`), all required with no default, and an
  optional `--selection-note`.
* `accept --root R --threshold ID --evaluation ID --batch DIR RUN_ID [...]`: score and record
  one confirmation with the consumption safeguards of `acceptance.confirm_acceptance`.

Partitions, thresholds, consumption records and acceptance reports are write-once; `--out`
files are created exclusively. Refusals print one `error:` line and exit with code 2.
"""

import argparse
import json
from decimal import Decimal
from pathlib import Path

from evals.query_decisions.acceptance import confirm_acceptance
from evals.query_decisions.category_metrics import threshold_sweep
from evals.query_decisions.contracts import QueryDecisionError
from evals.query_decisions.gate import AcceptanceCriteria, freeze_threshold
from evals.query_decisions.partitions import (
    BatchRun,
    PartitionRole,
    SourceSelection,
    build_partition,
    check_disjoint,
    load_partition,
    resolve_members,
    write_partition,
)

GATE_COMMANDS = frozenset({"partition", "sweep", "freeze", "accept"})


def add_gate_commands(commands: argparse._SubParsersAction) -> None:
    partition = commands.add_parser("partition")
    actions = partition.add_subparsers(dest="partition_command", required=True)
    create = actions.add_parser("create")
    _root(create)
    create.add_argument("--id", required=True)
    create.add_argument("--role", required=True, choices=[r.value for r in PartitionRole])
    create.add_argument(
        "--screening", required=True, choices=["none", "parser_abstains_on_category"]
    )
    create.add_argument("--source", required=True, action="append", nargs=2, metavar=("DIR", "IDS"))
    show = actions.add_parser("show")
    _root(show)
    show.add_argument("--id", required=True)
    check = actions.add_parser("check")
    _root(check)
    check.add_argument("--calibration", required=True)
    check.add_argument("--evaluation", required=True)

    sweep = commands.add_parser("sweep")
    _root(sweep)
    sweep.add_argument("--calibration", required=True)
    _batches(sweep)
    sweep.add_argument("--threshold", required=True, action="append", type=Decimal)
    sweep.add_argument("--out", type=Path)

    freeze = commands.add_parser("freeze")
    _root(freeze)
    freeze.add_argument("--id", required=True)
    freeze.add_argument("--calibration", required=True)
    freeze.add_argument("--threshold", required=True, type=Decimal)
    freeze.add_argument("--model", required=True)
    freeze.add_argument("--confidence-level", required=True, type=Decimal)
    freeze.add_argument("--wrong-commit-definition", required=True, choices=["strict", "lenient"])
    freeze.add_argument("--max-wrong-commit-upper-bound", required=True, type=Decimal)
    freeze.add_argument("--min-coverage", required=True, type=Decimal)
    freeze.add_argument("--min-accepted-fills", required=True, type=int)
    freeze.add_argument("--max-unknown-or-fallback-rate", required=True, type=Decimal)
    freeze.add_argument("--max-unclear-rate", required=True, type=Decimal)
    freeze.add_argument("--selection-note")

    accept = commands.add_parser("accept")
    _root(accept)
    accept.add_argument("--threshold", required=True)
    accept.add_argument("--evaluation", required=True)
    _batches(accept)


def _root(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--root", required=True, type=Path)


def _batches(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--batch", required=True, action="append", nargs=2, metavar=("DIR", "RUN_ID")
    )


def _batch_runs(pairs: list[list[str]]) -> list[BatchRun]:
    return [BatchRun(Path(directory), run_id) for directory, run_id in pairs]


def run_gate_command(args: argparse.Namespace) -> None:
    """Run one gate command; errors propagate to the CLI's single error handler."""
    if args.command == "partition":
        _partition(args)
    elif args.command == "sweep":
        calibration, digest = load_partition(args.root, args.calibration)
        if calibration.role is not PartitionRole.CALIBRATION:
            raise QueryDecisionError(
                "thresholds are swept on a calibration partition only, never on evaluation data"
            )
        records = resolve_members(calibration, _batch_runs(args.batch))
        report = {
            "calibration_partition_id": calibration.partition_id,
            "calibration_partition_sha256": digest,
            "synthetic": calibration.synthetic,
            "note": "fills at the supplied thresholds only; no threshold is selected here",
            "sweep": threshold_sweep(records, args.threshold),
        }
        emit_json(report, args.out)
    elif args.command == "freeze":
        criteria = AcceptanceCriteria(
            confidence_level=args.confidence_level,
            wrong_commit_definition=args.wrong_commit_definition,
            max_wrong_commit_upper_bound=args.max_wrong_commit_upper_bound,
            min_coverage=args.min_coverage,
            min_accepted_fills=args.min_accepted_fills,
            max_unknown_or_fallback_rate=args.max_unknown_or_fallback_rate,
            max_unclear_rate=args.max_unclear_rate,
        )
        frozen, digest = freeze_threshold(
            args.root,
            threshold_id=args.id,
            calibration_partition_id=args.calibration,
            threshold=args.threshold,
            model=args.model,
            criteria=criteria,
            selection_note=args.selection_note,
        )
        print(f"threshold {frozen.threshold_id} frozen at {frozen.threshold}; sha256 {digest}")
    else:
        confirmation = confirm_acceptance(
            args.root,
            threshold_id=args.threshold,
            evaluation_partition_id=args.evaluation,
            batches=_batch_runs(args.batch),
        )
        state = "identical repeat; nothing written" if confirmation.repeated else "recorded"
        print(
            f"acceptance {confirmation.report['outcome']} ({state}); "
            f"report {confirmation.report_path}"
        )


def _partition(args: argparse.Namespace) -> None:
    if args.partition_command == "create":
        sources = [
            SourceSelection(Path(directory), tuple(i for i in ids.split(",") if i))
            for directory, ids in args.source
        ]
        seal = build_partition(args.id, PartitionRole(args.role), sources, screening=args.screening)
        digest = write_partition(args.root, seal)
        print(
            f"partition {seal.partition_id} ({seal.role.value}) written: "
            f"{seal.member_count} members; sha256 {digest}"
        )
    elif args.partition_command == "show":
        seal, digest = load_partition(args.root, args.id)
        print(
            f"partition {seal.partition_id} ({seal.role.value}): {seal.member_count} members; "
            f"screening {seal.screening}; synthetic {str(seal.synthetic).lower()}; "
            f"sealed {seal.sealed_at.isoformat()}; sha256 {digest}"
        )
    else:
        calibration, _ = load_partition(args.root, args.calibration)
        evaluation, _ = load_partition(args.root, args.evaluation)
        check_disjoint(calibration, evaluation)
        print(f"partitions {calibration.partition_id} and {evaluation.partition_id} are disjoint")


def emit_json(report: dict, out: Path | None) -> None:
    text = json.dumps(report, indent=2, sort_keys=True)
    if out is None:
        print(text)
        return
    with open(out, "x", encoding="utf-8", newline="\n") as handle:  # never overwrites
        handle.write(text + "\n")
    print(f"report written to {out}")
