"""M14-1: the listing-decision contracts, jl-1 state builder and deterministic baseline import and
run in a clean subprocess with outbound network blocked, and without the TypeSafe SDK, settings,
`.env`, database, HTTP client or model modules.

The child replaces the socket entry points before any project import, so any connection or name
lookup is recorded and refused. State hashes must match across processes (`-I` gives each child
its own random hash seed) and the in-process result.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

from listing_decision_support import LAPTOP, raw_of
from test_listing_decision_state import MANY

from ecommerce_search.decision.listing_provider import (
    DeterministicListingDecisionProvider,
    decide_listing,
)
from ecommerce_search.normalization.intake import parse_raw_listing
from ecommerce_search.normalization.normalizer import normalize_listing

ROOT = Path(__file__).resolve().parents[3]
LISTINGS = [raw_of(LAPTOP).raw_bytes, raw_of(MANY).raw_bytes]

_CHILD = """
import json, socket, sys
attempts = []
def _refuse(name):
    def refuse(*args, **kwargs):
        attempts.append(name)
        raise OSError("network access is refused in this test")
    return refuse
for name in ("create_connection", "getaddrinfo", "gethostbyname", "gethostbyname_ex",
             "create_server", "socketpair"):
    setattr(socket, name, _refuse(name))
for name in ("connect", "connect_ex", "sendto", "bind"):
    setattr(socket.socket, name, _refuse("socket." + name))
sys.path.insert(0, sys.argv[1])
from ecommerce_search.decision import listing_questions
from ecommerce_search.decision.listing_provider import (
    DeterministicListingDecisionProvider, decide_listing,
)
from ecommerce_search.normalization import intake, normalizer
hashes = []
provider = DeterministicListingDecisionProvider()
for line in sys.stdin.buffer.read().split(b"\\n"):
    raw = intake.parse_raw_listing(line)
    state, outcome = decide_listing(raw, normalizer.normalize_listing(raw), provider)
    listing_questions.model_inputs(state)
    hashes.append([state.state_sha256(), outcome.model_dump_json()])
loaded = sorted(
    name for name in sys.modules
    if name.split(".")[0] in {"typesafe_sdk", "httpx", "httpx2", "sqlalchemy", "psycopg",
                              "requests", "urllib3", "torch", "sentence_transformers",
                              "transformers", "fastapi", "dotenv"}
    or name.startswith(("ecommerce_search.config", "ecommerce_search.db",
                        "ecommerce_search.api", "ecommerce_search.search",
                        "ecommerce_search.embeddings", "ecommerce_search.reranking",
                        "ecommerce_search.models"))
)
print(json.dumps({"loaded": loaded, "attempts": attempts, "hashes": hashes,
                  "digest": listing_questions.question_set_digest()}))
"""


def _run_child(tmp_path) -> dict:
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(
            ("POSTGRES_", "DB_", "APP_ENV", "EMBEDDING_", "RERANKER_", "JEV_", "TYPESAFE_")
        )
    }
    result = subprocess.run(  # noqa: S603 - fixed argv, sys.executable
        [sys.executable, "-I", "-c", _CHILD, str(ROOT / "src")],
        cwd=tmp_path,  # no repository .env in the working directory
        env=env,
        input=b"\n".join(LISTINGS),
        capture_output=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")[-2000:]
    return json.loads(result.stdout)


def test_listing_decisions_run_offline_without_sdk_settings_or_database(tmp_path):
    first = _run_child(tmp_path)
    second = _run_child(tmp_path)
    assert first["loaded"] == []
    assert first["attempts"] == []
    provider = DeterministicListingDecisionProvider()
    in_process = []
    for line in LISTINGS:
        raw = parse_raw_listing(line)
        state, outcome = decide_listing(raw, normalize_listing(raw), provider)
        in_process.append([state.state_sha256(), outcome.model_dump_json()])
    assert first["hashes"] == second["hashes"] == in_process
    assert first["digest"] == second["digest"]
