"""Per-run persistence of saved API responses (`ar-1`) and per-call telemetry (Milestone 17).

Each processed case is written as soon as it finishes (appended, flushed and synced), so a later
provider error or stop never discards earlier responses. A response record holds the complete
`AssistantResponse` the workflow returned (`model_dump(mode="json")`: outcome, notice,
clarification, summary, recommendations in their returned order with cited facts, requirement
checks and explanations, evidence, generation info, versions, latency), bound to the run ID, the
evaluated code commit, the case-file and annotation-file SHA-256 values and the case fingerprint.
It never contains an API key or the provider prompt; synthetic shopper and catalog text may
legitimately appear in it. The review sheet labels its content as saved API responses, not
verified browser output. All files are written with LF line endings.
"""

import json
import os
import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from evals.assistant.contracts import EvalCase, case_sha256
from evals.assistant.runner import CaseResult

RESPONSES_VERSION = "ar-1"
RUN_ID = re.compile(r"^[a-z0-9][a-z0-9-]{2,63}$")
CODE_PATHS = ("src", "evals", "tests")
IMPORTABLE = (".py", ".pyw", ".pth", ".pyd", ".so")


@dataclass(frozen=True)
class RunBinding:
    run_id: str
    code_commit: str
    cases_sha256: str
    annotations_sha256: str | None
    selection_sha256: str | None = None


def classify_status(lines: list[str]) -> dict[str, list[str]]:
    """From `git status --porcelain --ignored --untracked-files=all`: tracked modifications, and
    untracked or ignored files Python could import or execute."""
    tracked, untracked = [], []
    for line in lines:
        if len(line) < 4:
            continue
        code, path = line[:2], line[3:].strip().strip('"')
        if code in ("??", "!!"):
            if path.endswith(IMPORTABLE):
                untracked.append(path)
        else:
            tracked.append(path)
    return {"tracked_modified": sorted(tracked), "untracked_code": sorted(untracked)}


def git_provenance(root: Path = Path(".")) -> dict[str, Any]:
    """The full evaluated commit and any change in the evaluation code paths."""

    def git(*args: str) -> str:
        return subprocess.run(  # noqa: S603 - fixed argv
            ["git", *args],  # noqa: S607 - git from PATH, as every harness here
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        ).stdout

    status = git("status", "--porcelain", "--ignored", "--untracked-files=all", "--", *CODE_PATHS)
    return {"code_commit": git("rev-parse", "HEAD").strip(), **classify_status(status.splitlines())}


class RunWriter:
    """Appends one record per processed case to `responses.jsonl` and its calls to `calls.jsonl`."""

    def __init__(self, out: Path, binding: RunBinding):
        self.out, self.binding = out, binding
        out.mkdir(parents=True, exist_ok=True)
        self.responses = out / "responses.jsonl"
        self.calls = out / "calls.jsonl"
        self.seq = 0

    def _append(self, path: Path, records: list[dict[str, Any]]) -> None:
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def write(self, result: CaseResult) -> None:
        binding = {k: v for k, v in asdict(self.binding).items() if v is not None}
        binding.setdefault("annotations_sha256", None)
        response = None if result.response is None else result.response.model_dump(mode="json")
        record = {
            "format": RESPONSES_VERSION,
            **binding,
            "case_id": result.case.case_id,
            "case_sha256": case_sha256(result.case),
            "error": result.error,
            "response": response,
        }
        calls = []
        for call in result.calls:
            self.seq += 1
            calls.append({"run_id": self.binding.run_id, "seq": self.seq, **asdict(call)})
        self._append(self.responses, [record])
        if calls:
            self._append(self.calls, calls)


def load_responses(path: Path) -> list[dict[str, Any]]:
    lines = path.read_text("utf-8").splitlines() if path.exists() else []
    return [json.loads(line) for line in lines if line.strip()]


def _facts(recommendation: dict[str, Any]) -> str:
    return "; ".join(f"{f['field']}={f['value']}" for f in recommendation["facts"]) or "none"


def render_review_sheet(
    records: list[dict[str, Any]], cases: dict[str, EvalCase], report: dict[str, Any]
) -> str:
    """A human review form for the `aj-1` rubric: the saved API response per case."""
    diagnostics = {d["case_id"] for d in report.get("diagnostics", [])}
    lines = [
        f"# Review sheet: run `{report.get('run_id')}`",
        "",
        "**Saved API responses** (`responses.jsonl`), not verified browser output. Fill in the "
        "`aj-1` fields by hand; nothing here is a judgement.",
        "",
        f"Report SHA-256 is recorded by the judgement file; responses SHA-256: "
        f"`{report.get('responses_sha256')}`; code commit `{report.get('code_commit')}`.",
        "",
    ]
    for record in records:
        case = cases[record["case_id"]]
        response = record["response"]
        tag = " (diagnostic, not scored)" if case.case_id in diagnostics else ""
        lines += [f"## `{case.case_id}`{tag}", "", f"**Message:** `{case.message}`", ""]
        if response is None:
            lines += [f"No response: {record['error']}", "", "---", ""]
            continue
        lines += [
            f"**Outcome:** {response['outcome']}"
            + (f" ({response['abstain_reason']})" if response["abstain_reason"] else ""),
            "",
            f"**Notice:** {response['notice']}",
            "",
        ]
        if response["clarification"]:
            lines += [f"**Clarification question:** {response['clarification']['question']}", ""]
        if response["summary"]:
            lines += [f"**Summary:** {response['summary']}", ""]
        for n, rec in enumerate(response["recommendations"], start=1):
            checks = "; ".join(
                f"{c['field']} {c['requested']}: {c['status']}" for c in rec["constraint_checks"]
            )
            lines += [
                f"{n}. `{rec['product_id']}` ({rec['verification']})",
                f"   - Cited catalog facts: {_facts(rec)}",
                f"   - Requirement checks: {checks or 'none'}",
                f"   - Explanation: {rec['explanation']}",
            ]
        products = [r["product_id"] for r in response["recommendations"]]
        lines += [
            "",
            f"Generation: {response['generation']['status']}, "
            f"{response['generation']['calls']} call(s), "
            f"validation {response['generation']['validation']}.",
            "",
            "**aj-1 (by hand):** outcome_appropriate: ___ · misleading: ___ · "
            + (" · ".join(f"{p} fit: ___" for p in products) or "products: none")
            + " · notes: ___",
            "",
            "---",
            "",
        ]
    return "\n".join(lines)
