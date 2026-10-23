"""Mocked assistant evaluation (Milestone 17 groundwork). No network calls; no live mode.

Usage:
    uv run python -m evals.assistant [--cases PATH] [--out DIR]

Prints the Markdown report; with --out also writes report.json and report.md. Exit status 1
when any case fails.
"""

import argparse
import json
import sys
from pathlib import Path

from evals.assistant.contracts import load_cases
from evals.assistant.report import build_report, render_markdown
from evals.assistant.runner import run_cases

DEFAULT_CASES = Path(__file__).parent / "fixtures" / "synthetic-v1" / "cases.jsonl"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.assistant", description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    report = build_report(run_cases(load_cases(args.cases)))
    markdown = render_markdown(report)
    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / "report.json").write_text(json.dumps(report, indent=2) + "\n", "utf-8")
        (args.out / "report.md").write_text(markdown, "utf-8")
    sys.stdout.write(markdown)
    return 0 if report["cases_passed"] == report["cases"] else 1


if __name__ == "__main__":
    sys.exit(main())
