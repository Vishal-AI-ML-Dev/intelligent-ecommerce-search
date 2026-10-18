"""Shared helpers for the M12 query-decision diagnostic tests.

Every query and label built here is a SYNTHETIC TEST FIXTURE (`source="synthetic_test_fixture"`):
software-test data only, never human-authored queries or human labels.
"""

import json
import socket
from datetime import UTC, datetime, timedelta
from pathlib import Path

from evals.contracts.canonical import encode_jsonl
from evals.query_decisions.contracts import (
    CATEGORY_LABELS,
    DATASET_ID,
    INTENT_LABELS,
    SCHEMA_VERSION,
    CategoryGold,
    DecisionLabel,
    IntentGold,
    LabelSeal,
    load_queries,
    queries_sha256,
)
from evals.query_decisions.labeling import (
    LABELS_FILE,
    QUERIES_FILE,
    load_sealed,
    parse_gold,
    seal_labels,
)
from evals.query_decisions.runner import JOURNAL_FILE, RUNS_DIR, RunHeader, RunMode, run_diagnostic

from ecommerce_search.decision.provider import (
    CategoryProposal,
    DecisionResult,
    IntentProposal,
    JevOutcome,
)

_LOOPBACK_HOSTS = {"127.0.0.1", "::1"}
QUERY_COUNT = 6
FIXTURE_HANDLE = "ann-9"  # a fixture handle; no real annotator labelled anything here
T0 = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)


def _guard(*args, **kwargs):
    raise RuntimeError("network access is not allowed in query-decision unit tests")


def install_network_guard(monkeypatch) -> None:
    """No name resolution, no outbound connection. Loopback connect stays allowed only because
    the Windows asyncio loop builds its self-pipe with one (the dry-run provider owns a loop)."""
    monkeypatch.setattr(socket, "create_connection", _guard)
    monkeypatch.setattr(socket, "getaddrinfo", _guard)
    real_connect = socket.socket.connect

    def guarded_connect(self, address):
        host = address[0] if isinstance(address, tuple) else None
        if host not in _LOOPBACK_HOSTS:
            raise RuntimeError("network access is not allowed in query-decision unit tests")
        return real_connect(self, address)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)


# (query id, text, category gold, intent gold) — synthetic fixtures for software tests only.
FIXTURE_ROWS = [
    ("dq-001", "hp laptop 16gb ram", "laptop", "none"),
    ("dq-002", "gaming laptop under 70k", "laptop", "gaming"),
    ("dq-003", "headfones for travel", "headphones", "travel"),
    ("dq-004", "refrigerator double door", "none", "none"),
    (
        "dq-005",
        "something light for college",
        "ambiguous:laptop,phone",
        "multi:student,lightweight",
    ),
    ("dq-006", "xyz qqq", "unclear", "unclear"),
]


def fixture_query_line(query_id: str, text: str) -> str:
    return json.dumps(
        {
            "schema_version": SCHEMA_VERSION,
            "dataset_id": DATASET_ID,
            "query_id": query_id,
            "text": text,
            "source": "synthetic_test_fixture",
            "author_handle": None,
            "author_tool": None,
            "language_style": "english",
            "stratum": "fixture",
        }
    )


def make_seal(*, human: int = 0, ai: int = 0, fixtures: int = 0, **overrides) -> LabelSeal:
    """A `LabelSeal` built in memory with the given source counts (no query file behind it)."""
    values = {
        "schema_version": SCHEMA_VERSION,
        "dataset_id": DATASET_ID,
        "question_set_version": "jq-1",
        "queries_sha256": "a" * 64,
        "labels_sha256": "b" * 64,
        "query_count": human + ai + fixtures,
        "human_authored_queries": human,
        "ai_authored_queries": ai,
        "synthetic_fixture_queries": fixtures,
        "annotator_handle": FIXTURE_HANDLE,
        "synthetic": fixtures > 0,
        "sealed_at": T0,
        "evidence_scope": "development_diagnostic_not_held_out",
    }
    values.update(overrides)
    return LabelSeal(**values)


def write_fixture_queries(work_dir: Path, rows=FIXTURE_ROWS) -> None:
    lines = [fixture_query_line(query_id, text) for query_id, text, _, _ in rows]
    (work_dir / QUERIES_FILE).write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_fixture_labels(work_dir: Path, rows=FIXTURE_ROWS) -> None:
    queries = load_queries((work_dir / QUERIES_FILE).read_bytes())
    digest = queries_sha256(queries)
    labels = [
        DecisionLabel(
            schema_version=SCHEMA_VERSION,
            dataset_id=DATASET_ID,
            query_id=query_id,
            queries_sha256=digest,
            annotator_handle=FIXTURE_HANDLE,
            labelled_at=T0 + timedelta(minutes=index),
            labelling_view="query_text_only",
            category=parse_gold(category, CategoryGold),
            semantic_intent=parse_gold(intent, IntentGold),
        )
        for index, (query_id, _, category, intent) in enumerate(rows)
    ]
    (work_dir / LABELS_FILE).write_bytes(encode_jsonl(labels))


def make_sealed_dir(tmp_path: Path) -> Path:
    write_fixture_queries(tmp_path)
    write_fixture_labels(tmp_path)
    seal_labels(tmp_path, now=lambda: T0 + timedelta(hours=1))
    return tmp_path


# --- scripted Jev outcomes (fabricated for software tests only; never Jev output) -------------


def outcome(category: str = "laptop", intent: str = "none", *, fallback=None, tokens=100):
    if fallback is not None:
        return JevOutcome(model="jev-1.13.0", latency_ms=5.0, fallback_reason=fallback)

    def proposal(kind, labels, top, enum_label):
        probabilities = {
            label: (0.9 if label == top else 0.1 / (len(labels) - 1)) for label in labels
        }
        return kind(
            label=enum_label,
            raw_choice=top,
            top_labels=(top,),
            provider_probabilities=probabilities,
            provider_confidence=0.8,
        )

    return JevOutcome(
        model="jev-1.13.0",
        response_model="jev-1.13.0",
        latency_ms=5.0,
        input_tokens=tokens,
        output_tokens=0,
        category=proposal(
            CategoryProposal, CATEGORY_LABELS, category, None if category == "none" else category
        ),
        semantic_intent=proposal(
            IntentProposal, INTENT_LABELS, intent, None if intent == "none" else intent
        ),
    )


class ScriptedProvider:
    """Returns scripted outcomes in order and checks that each call was journalled first."""

    def __init__(self, work_dir, run_id, outcomes):
        self.journal = work_dir / RUNS_DIR / run_id / JOURNAL_FILE
        self.outcomes = list(outcomes)
        self.calls = 0
        self.closed = False

    def understand(self, query, deterministic_result):
        self.calls += 1
        last = json.loads(self.journal.read_text(encoding="utf-8").splitlines()[-1])
        assert last["event"] == "dispatch", "the attempt must be journalled before the call"
        item = self.outcomes.pop(0)
        if isinstance(item, BaseException):
            raise item
        return DecisionResult(
            provider="jev", provider_version="jq-1", understanding=deterministic_result, jev=item
        )

    def close(self):
        self.closed = True


def header(work_dir, run_id="r1", **overrides):
    seal, _, _ = load_sealed(work_dir)
    values = {
        "run_id": run_id,
        "mode": RunMode.DRY_RUN,
        "queries_sha256": seal.queries_sha256,
        "labels_sha256": seal.labels_sha256,
        "model": "jev-1.13.0",
        "max_attempts": QUERY_COUNT,
    }
    values.update(overrides)
    return RunHeader(**values)


def run(work_dir, outcomes, run_header=None, **kwargs):
    run_header = run_header or header(work_dir)
    provider = ScriptedProvider(work_dir, run_header.run_id, outcomes)
    clock = iter(T0 + timedelta(seconds=i) for i in range(1000))
    summary = run_diagnostic(
        work_dir,
        run_header,
        lambda: provider,
        now=lambda: next(clock),
        say=lambda _: None,
        **kwargs,
    )
    return summary, provider
