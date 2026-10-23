"""M17 abe-v1 live run 001 (owner-approved 2026-10-23): the unchanged CLI entry point
(`evals.assistant.__main__.main`, frozen code db1c450) with a provider factory that wraps the
unchanged Gemini adapter in a per-call telemetry observer.

The observer only records call metadata (sequence, UTC start, elapsed ms, failure code, reported
token counts, draft outcome and number of selected IDs), appending and flushing one JSON line per
call so partial telemetry survives a stop. It never records keys, prompts or response text, and
it refuses a call beyond the approved cap of 58 (the harness's own cap guard runs first).
"""

import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from evals.assistant import __main__ as cli

CAP = 58
OUT = Path("evals/results/m17/abe-v1-live-001")
ARGV = [
    "live",
    "--cases", "evals/assistant/drafts/abe-v1/cases.draft.jsonl",
    "--expected-sha256", "3ce04c99e115e0cd6304f35c0d3be943cf9b8408c344ac87bafe68e3e38f7829",
    "--annotations", "evals/assistant/drafts/abe-v1/annotations.json",
    "--expected-annotations-sha256",
    "e1fdebeb84818d222332b17f17a04b9a3e2a3704d04c0d0c41c6836e51daca5b",
    "--max-requests", "58",
    "--min-interval-seconds", "10",
    "--out", str(OUT),
    "--confirm-live",
]


class Observer:
    def __init__(self, inner, path: Path):
        self.inner, self.path, self.sent = inner, path, 0
        self.provider, self.model = inner.provider, inner.model

    def generate(self, request):
        if self.sent >= CAP:
            raise RuntimeError("observer cap reached; call not sent")
        self.sent += 1
        started_utc = datetime.now(UTC).isoformat(timespec="milliseconds")
        started = time.perf_counter()
        result = self.inner.generate(request)
        elapsed = round((time.perf_counter() - started) * 1000, 3)
        draft = result.draft
        record = {
            "seq": self.sent,
            "started_utc": started_utc,
            "elapsed_ms": elapsed,
            "failure": result.failure,
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "draft_outcome": None if draft is None else draft.outcome,
            "selected_count": None if draft is None else len(draft.selected),
        }
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(record) + "\n")
            handle.flush()
        return result

    def close(self):
        close = getattr(self.inner, "close", None)
        if callable(close):
            close()


def factory(settings):
    OUT.mkdir(parents=True, exist_ok=True)
    return Observer(cli._gemini(settings), OUT / "calls.raw.jsonl")


if __name__ == "__main__":
    sys.exit(cli.main(ARGV, provider_factory=factory))
