"""Concise report of a mocked assistant evaluation run (Milestone 17 groundwork).

Every report states that it is mocked, which authorship the cases have and that none is
human-reviewed. Latency, tokens and cost are not measured: the provider is a script.
"""

from collections import Counter
from typing import Any

from evals.assistant.runner import CaseResult

REPORT_VERSION = "aq-report-1"
SCOPE = (
    "Mocked run: fixed retrieval and scripted provider output through the real M16 workflow. "
    "Pass rates describe harness and workflow behaviour only, not live-model faithfulness, "
    "relevance or prompt-injection resistance. Not the M17 human-reviewed answer set; M17 is "
    "not complete."
)
NOT_MEASURED = "not_measured_mocked"


def _rate(passed: int, total: int) -> str:
    return f"{passed}/{total}"


def build_report(results: tuple[CaseResult, ...]) -> dict[str, Any]:
    check_totals: Counter[str] = Counter()
    check_passed: Counter[str] = Counter()
    tag_totals: Counter[str] = Counter()
    tag_passed: Counter[str] = Counter()
    failures = []
    for result in results:
        for check in result.checks:
            check_totals[check.check] += 1
            check_passed[check.check] += check.passed
            if not check.passed:
                failures.append(
                    {"case_id": result.case.case_id, "check": check.check, "detail": check.detail}
                )
        if result.error:
            failures.append(
                {"case_id": result.case.case_id, "check": "run", "detail": result.error}
            )
        for tag in result.case.tags:
            tag_totals[tag] += 1
            tag_passed[tag] += result.passed
    return {
        "report_version": REPORT_VERSION,
        "mode": "mocked",
        "scope": SCOPE,
        "cases": len(results),
        "cases_passed": sum(result.passed for result in results),
        "authorship": dict(sorted(Counter(r.case.authorship for r in results).items())),
        "human_reviewed_cases": 0,
        "checks": {
            name: _rate(check_passed[name], total) for name, total in sorted(check_totals.items())
        },
        "tags": {
            name: _rate(tag_passed[name], total) for name, total in sorted(tag_totals.items())
        },
        "failures": failures,
        "latency": NOT_MEASURED,
        "tokens": NOT_MEASURED,
        "cost": NOT_MEASURED,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        f"# Assistant evaluation ({report['mode']}, {report['report_version']})",
        "",
        report["scope"],
        "",
        f"Cases passed: {report['cases_passed']}/{report['cases']}. "
        f"Authorship: {report['authorship']}. Human-reviewed cases: "
        f"{report['human_reviewed_cases']}.",
        f"Latency, tokens, cost: {NOT_MEASURED}.",
        "",
        "| Check | Passed |",
        "|---|---|",
        *(f"| {name} | {rate} |" for name, rate in report["checks"].items()),
        "",
        "| Tag | Cases passed |",
        "|---|---|",
        *(f"| {name} | {rate} |" for name, rate in report["tags"].items()),
        "",
        "## Failures",
        "",
    ]
    if report["failures"]:
        lines += [f"- {f['case_id']} / {f['check']}: {f['detail']}" for f in report["failures"]]
    else:
        lines.append("None.")
    return "\n".join(lines) + "\n"
