"""Candidate retrieval for the pool (M9 Session C2): every frozen source, in process, offline.

The six sources are the frozen M3–M8 endpoints, called through the real FastAPI application
(`create_app`) with an in-process test client, exactly as the M5–M8 benchmark harnesses do, at
`top_k = POOL_SOURCE_TOP_K` (20):

| system            | endpoint             | score field     | app               |
|-------------------|----------------------|-----------------|-------------------|
| `v0_lexical`      | `/search`            | `lexical_score` | base              |
| `dense_only`      | `/search/dense`      | `dense_score`   | base              |
| `v1_hybrid`       | `/search/hybrid`     | `rrf_score`     | base              |
| `v2_filtered`     | `/search/filtered`   | `rrf_score`     | base              |
| `v3_reranked_d10` | `/search/reranked`   | `rerank_score`  | reranker depth 10 |
| `v3_reranked_d20` | `/search/reranked`   | `rerank_score`  | reranker depth 20 |

Nothing in `src/` is changed or monkeypatched; the two V3 apps differ only in the documented
`search_rerank_depth` setting. Scores are recorded as `repr(float)` strings (exact); a V3 result
beyond the rerank depth has no rerank score (null).

Safety (checked before any database or model work, in the parent and again in each child):
the approved settings, both pinned model snapshots verified offline against their manifests, a
clean runtime tree (`src/`, `migrations/`, `data/seed/`, `uv.lock`, `alembic.ini` equal to
HEAD), hub offline mode, and a guard refusing outbound non-loopback sockets. Databases are
uniquely named `ecommerce_search_bench_<12 hex>` scratch databases, created and dropped here;
the development database is refused by name.
"""

import contextlib
import importlib.metadata
import json
import os
import re
import socket
import subprocess
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from evals.contracts.canonical import ContractError
from evals.contracts.catalog import ROOT
from evals.contracts.constants import (
    BLIND_ORDER_VERSION,
    POOL_POLICY_VERSION,
    POOL_SOURCE_TOP_K,
    V3_RERANK_DEPTHS,
)
from evals.contracts.models import (
    NamedValue,
    RerankerMode,
    RunContext,
    SourceRun,
    SourceSystem,
)
from evals.contracts.pooling import FetchedSource

OFFLINE_ENV = {"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1"}
SCRATCH_PREFIX = "ecommerce_search_bench_"
SCRATCH_RE = re.compile(r"ecommerce_search_bench_[0-9a-f]{12}")
SEED_PRODUCTS = 240
MIGRATION_HEAD = "0004"
RUNTIME_PATHS: tuple[str, ...] = ("src", "migrations", "data/seed", "uv.lock", "alembic.ini")
LIBRARIES: tuple[str, ...] = (
    "torch",
    "sentence-transformers",
    "transformers",
    "tokenizers",
    "fastapi",
    "sqlalchemy",
    "psycopg",
    "pgvector",
)

# The approved effective configuration (the M8 frozen defaults). Values are refused, not merely
# recorded. The V3 sources override only `search_rerank_depth`.
APPROVED_SETTINGS: dict[str, object] = {
    "search_lexical_k": 50,
    "search_dense_k": 50,
    "search_candidate_k": 50,
    "search_rrf_k": 100,
    "search_default_top_k": 10,
    "search_max_query_length": 200,
    "embedding_batch_size": 32,
    "reranker_batch_size": 16,
    "reranker_enabled": True,
    "search_rerank_depth": 20,
    "embedding_models_dir": None,
}


class SourceError(ContractError):
    """A source could not be run safely or returned an unexpected response."""


@dataclass(frozen=True)
class SourceSpec:
    system: SourceSystem
    endpoint: str
    search_version: str
    score_field: str
    app: str  # "base", "d10" or "d20"


SOURCES: tuple[SourceSpec, ...] = (
    SourceSpec(SourceSystem.V0_LEXICAL, "/search", "v0_lexical", "lexical_score", "base"),
    SourceSpec(SourceSystem.DENSE_ONLY, "/search/dense", "dense_only", "dense_score", "base"),
    SourceSpec(SourceSystem.V1_HYBRID, "/search/hybrid", "v1_hybrid", "rrf_score", "base"),
    SourceSpec(SourceSystem.V2_FILTERED, "/search/filtered", "v2_filtered", "rrf_score", "base"),
    SourceSpec(
        SourceSystem.V3_RERANKED_D10, "/search/reranked", "v3_reranked", "rerank_score", "d10"
    ),
    SourceSpec(
        SourceSystem.V3_RERANKED_D20, "/search/reranked", "v3_reranked", "rerank_score", "d20"
    ),
)
APP_DEPTH = {"d10": V3_RERANK_DEPTHS[0], "d20": V3_RERANK_DEPTHS[1]}


# ---- preflight ------------------------------------------------------------------------------


def check_settings(settings: Any) -> tuple[NamedValue, ...]:
    differing = []
    for name, approved in APPROVED_SETTINGS.items():
        value = getattr(settings, name, "<missing>")
        if type(value) is not type(approved) or value != approved:
            differing.append(f"{name}={value!r} (approved {approved!r})")
    if differing:
        raise SourceError("the approved settings are required: " + ", ".join(sorted(differing)))
    return tuple(
        NamedValue(name=name, value=json.dumps(value))
        for name, value in sorted(APPROVED_SETTINGS.items())
        if name != "embedding_models_dir"
    )


def verify_model_pins() -> tuple[NamedValue, ...]:
    """Verify both pinned snapshots offline (manifest + configuration). Never downloads."""
    from ecommerce_search.embeddings.fetch import RERANKER_PROFILE, verify_manifest
    from ecommerce_search.embeddings.spec import (
        DEFAULT_EMBEDDING_MODEL_ID,
        EMBEDDING_MODELS,
        repository_models_dir,
    )
    from ecommerce_search.reranking.cross_encoder_provider import snapshot_config_problems
    from ecommerce_search.reranking.spec import DEFAULT_RERANKER_MODEL_ID, RERANKER_MODELS

    models_dir = repository_models_dir()
    if models_dir is None:
        raise SourceError("no models directory is known (not a source checkout)")
    embedding = EMBEDDING_MODELS[DEFAULT_EMBEDDING_MODEL_ID]
    reranker = RERANKER_MODELS[DEFAULT_RERANKER_MODEL_ID]
    problems = verify_manifest(models_dir, embedding.model_id, embedding.revision)
    if problems:
        raise SourceError(f"the pinned embedding snapshot failed verification: {problems[:3]}")
    problems = verify_manifest(
        models_dir, reranker.model_id, reranker.revision, profile=RERANKER_PROFILE
    )
    if problems:
        raise SourceError(f"the pinned reranker snapshot failed verification: {problems[:3]}")
    if snapshot_config_problems(models_dir, reranker):
        raise SourceError("the pinned reranker snapshot configuration differs from its spec")
    values = {
        "embedding_config_sha256": embedding.config_sha256(),
        "embedding_model_id": embedding.model_id,
        "embedding_revision": embedding.revision,
        "reranker_config_sha256": reranker.config_sha256(),
        "reranker_model_id": reranker.model_id,
        "reranker_revision": reranker.revision,
    }
    return tuple(NamedValue(name=k, value=v) for k, v in sorted(values.items()))


def component_versions() -> tuple[NamedValue, ...]:
    from ecommerce_search.embeddings.text import EMBEDDING_TEXT_VERSION
    from ecommerce_search.filtering.policy import FILTER_POLICY_VERSION
    from ecommerce_search.query_understanding.lexicon import LEXICON_VERSION
    from ecommerce_search.query_understanding.parser import PARSER_VERSION
    from ecommerce_search.reranking.documents import RERANK_DOCUMENT_VERSION
    from ecommerce_search.search.documents import DOCUMENT_VERSION
    from ecommerce_search.search.hybrid import RRF_K_STATUS

    values = {
        "document_version": DOCUMENT_VERSION,
        "embedding_text_version": EMBEDDING_TEXT_VERSION,
        "filter_policy": FILTER_POLICY_VERSION,
        "lexicon": LEXICON_VERSION,
        "parser": PARSER_VERSION,
        "rerank_document_version": RERANK_DOCUMENT_VERSION,
        "rrf_k_status": RRF_K_STATUS,
    }
    return tuple(NamedValue(name=k, value=v) for k, v in sorted(values.items()))


def library_versions() -> tuple[NamedValue, ...]:
    out = []
    for name in sorted(LIBRARIES):
        try:
            out.append(NamedValue(name=name, value=importlib.metadata.version(name)))
        except importlib.metadata.PackageNotFoundError:
            out.append(NamedValue(name=name, value="not-installed"))
    return tuple(out)


def _git(*args: str) -> str:
    result = subprocess.run(  # noqa: S603 - fixed argv
        ["git", *args],  # noqa: S607 - git from PATH, as every harness here
        cwd=ROOT,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise SourceError(f"git {args[0]} failed")
    return result.stdout.decode("utf-8").strip()


def runtime_trees(git: Callable[..., str] = _git) -> tuple[NamedValue, ...]:
    """The committed object ids of every runtime path; refuses a dirty runtime tree, so the
    retrieval code that ran is exactly the committed one."""
    dirty = git("status", "--porcelain", "--untracked-files=all", "--", *RUNTIME_PATHS)
    if dirty:
        raise SourceError("runtime paths differ from HEAD; the frozen search code must be clean")
    return tuple(
        NamedValue(name=path, value=git("rev-parse", f"HEAD:{path}"))
        for path in sorted(RUNTIME_PATHS)
    )


def build_run_context(
    *,
    queries_manifest_sha256: str,
    dataset: tuple[str, str, str],
    settings: tuple[NamedValue, ...],
    model_pins: tuple[NamedValue, ...],
    components: tuple[NamedValue, ...],
    libraries: tuple[NamedValue, ...],
    trees: tuple[NamedValue, ...],
) -> RunContext:
    return RunContext(
        queries_manifest_sha256=queries_manifest_sha256,
        dataset_id=dataset[0],
        dataset_version=dataset[1],
        dataset_checksum_sha256=dataset[2],
        pool_policy=POOL_POLICY_VERSION,
        blind_order=BLIND_ORDER_VERSION,
        source_top_k=POOL_SOURCE_TOP_K,
        settings=settings,
        model_pins=model_pins,
        component_versions=components,
        library_versions=libraries,
        runtime_trees=trees,
    )


def install_network_guard() -> None:
    """Refuse outbound non-loopback name resolution and connections in this process."""
    loopback = {"127.0.0.1", "::1", "localhost"}
    real_getaddrinfo, real_create = socket.getaddrinfo, socket.create_connection

    def guarded_getaddrinfo(host, *args, **kwargs):
        if host is not None and host not in loopback:
            raise OSError("network access is disabled during pool generation")
        return real_getaddrinfo(host, *args, **kwargs)

    def guarded_create(address, *args, **kwargs):
        if address[0] not in loopback:
            raise OSError("network access is disabled during pool generation")
        return real_create(address, *args, **kwargs)

    socket.getaddrinfo = guarded_getaddrinfo  # type: ignore[assignment]
    socket.create_connection = guarded_create  # type: ignore[assignment]
    os.environ.update(OFFLINE_ENV)


# ---- scratch databases ----------------------------------------------------------------------


def new_scratch_name() -> str:
    return f"{SCRATCH_PREFIX}{uuid.uuid4().hex[:12]}"


def assert_scratch_name(name: str, development_db: str) -> str:
    if name == development_db:
        raise SourceError("refusing to use the development database")
    if not SCRATCH_RE.fullmatch(name):
        raise SourceError("scratch database names must match ecommerce_search_bench_<12 hex>")
    return name


@contextlib.contextmanager
def scratch_database(admin, name: str, development_db: str) -> Iterator[str]:
    """CREATE a uniquely named scratch database and always DROP it, even on failure."""
    from sqlalchemy import text

    assert_scratch_name(name, development_db)
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    try:
        yield name
    finally:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


def database_exists(admin, name: str) -> bool:  # pragma: no cover - reads the server
    from sqlalchemy import text

    with admin.connect() as conn:
        found = conn.execute(
            text("SELECT count(*) FROM pg_database WHERE datname = :name"), {"name": name}
        ).scalar_one()
    return bool(found)


# ---- calling the sources --------------------------------------------------------------------


def _score(value: object, what: str) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise SourceError(f"{what}: score is not a number")
    text = repr(float(value))
    if not re.fullmatch(r"-?[0-9]+(?:\.[0-9]+)?(?:[eE][-+]?[0-9]+)?", text):
        raise SourceError(f"{what}: score {text!r} is not finite")
    return text


def _canonical(items: Sequence[object]) -> tuple[str, ...]:
    return tuple(json.dumps(i, sort_keys=True, separators=(",", ":")) for i in items)


def parse_response(
    spec: SourceSpec, query_id: str, body: Mapping[str, Any], pins: Mapping[str, str]
) -> FetchedSource:
    """Check one response against its frozen contract and turn it into a private source run."""
    what = f"{query_id}/{spec.system.value}"
    if body.get("search_version") != spec.search_version:
        raise SourceError(f"{what}: unexpected search_version")
    if body.get("top_k") != POOL_SOURCE_TOP_K:
        raise SourceError(f"{what}: the response does not echo top_k={POOL_SOURCE_TOP_K}")
    results = body.get("results")
    if not isinstance(results, list) or body.get("result_count") != len(results):
        raise SourceError(f"{what}: inconsistent results")
    if [r.get("rank") for r in results] != list(range(1, len(results) + 1)):
        raise SourceError(f"{what}: ranks are not 1..n")
    if spec.system is not SourceSystem.V0_LEXICAL and (
        body.get("embedding_model_id"),
        body.get("embedding_model_revision"),
    ) != (pins["embedding_model_id"], pins["embedding_revision"]):
        raise SourceError(f"{what}: embedding model differs from the pin")
    reranker = None
    if spec.app != "base":
        block = body.get("reranker") or {}
        if (block.get("model_id"), block.get("model_revision")) != (
            pins["reranker_model_id"],
            pins["reranker_revision"],
        ):
            raise SourceError(f"{what}: reranker model differs from the pin")
        if block.get("enabled") is not True or block.get("rerank_depth") != APP_DEPTH[spec.app]:
            raise SourceError(f"{what}: reranker mode is not the requested depth")
        reranker = RerankerMode(
            enabled=True,
            depth=block["rerank_depth"],
            status=block["status"],
            reranked_count=block["reranked_count"],
        )
    understanding = body.get("query_understanding")
    policy = body.get("filter_policy")
    run = SourceRun(
        query_id=query_id,
        system=spec.system,
        endpoint=spec.endpoint,
        search_version=spec.search_version,
        top_k=POOL_SOURCE_TOP_K,
        product_ids=tuple(str(r["product_id"]) for r in results),
        applied_filters=_canonical(body.get("applied_filters") or []),
        ignored_constraints=_canonical(body.get("ignored_constraints") or []),
        parser_version=understanding["provider_version"] if understanding else None,
        filter_policy_version=policy["version"] if policy else None,
        dense_status=body.get("dense_status"),
        reranker=reranker,
    )
    scores = tuple(_score(r.get(spec.score_field), what) for r in results)
    return FetchedSource(run=run, scores=scores)


def fetch_sources(
    clients: Mapping[str, Any],
    queries: Sequence[tuple[str, str]],
    pins: Mapping[str, str],
    schemas: Mapping[str, Callable[[Any], object]] | None = None,
) -> list[FetchedSource]:
    """Call every source for every (query_id, text) at top_k=20. Any non-200 response, schema
    violation or contract mismatch raises; no response is retried, skipped or repaired."""
    fetched = []
    for query_id, text in queries:
        for spec in SOURCES:
            response = clients[spec.app].get(
                spec.endpoint, params={"q": text, "top_k": POOL_SOURCE_TOP_K}
            )
            if response.status_code != 200:
                raise SourceError(f"{query_id}/{spec.system.value}: status {response.status_code}")
            body = response.json()
            if schemas is not None:
                schemas[spec.endpoint](body)
            fetched.append(parse_response(spec, query_id, body, pins))
    return fetched


def response_schemas() -> dict[str, Callable[[Any], object]]:
    from ecommerce_search.api.schemas import (
        DenseSearchResponse,
        FilteredSearchResponse,
        HybridSearchResponse,
        RerankedSearchResponse,
        SearchResponse,
    )

    return {
        "/search": SearchResponse.model_validate,
        "/search/dense": DenseSearchResponse.model_validate,
        "/search/hybrid": HybridSearchResponse.model_validate,
        "/search/filtered": FilteredSearchResponse.model_validate,
        "/search/reranked": RerankedSearchResponse.model_validate,
    }


def retrieve_on_scratch_database(  # pragma: no cover - needs PostgreSQL and the local models
    settings: Any, database: str, queries: Sequence[tuple[str, str]], pins: Mapping[str, str]
) -> list[FetchedSource]:
    """Create the scratch database, migrate it to head, ingest the frozen seed, embed it with
    the pinned model, run every source for every query, and drop the database."""
    from alembic import command
    from alembic.config import Config
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import Session

    from ecommerce_search.api.app import create_app
    from ecommerce_search.embeddings.sentence_transformers_provider import (
        SentenceTransformerEmbedder,
    )
    from ecommerce_search.ingestion.service import ingest_file
    from ecommerce_search.search.dense_indexing import embed, embedding_status

    admin = create_engine(settings.database_url(database="postgres"), isolation_level="AUTOCOMMIT")
    engine = None
    try:
        with scratch_database(admin, database, settings.postgres_db):
            config = Config(str(ROOT / "alembic.ini"))
            config.set_main_option("script_location", str(ROOT / "migrations"))
            config.attributes["database"] = database
            command.upgrade(config, "head")
            engine = create_engine(settings.database_url(database=database))
            with engine.connect() as conn:
                head = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            if head != MIGRATION_HEAD:
                raise SourceError(f"the scratch database is not at migration head {MIGRATION_HEAD}")
            seed = ROOT / "data" / "seed" / "catalog_seed_v1.jsonl"
            outcome = ingest_file(engine, seed, seed.with_name("catalog_seed_v1.provenance.json"))
            if outcome.result is None or outcome.result.inserted != SEED_PRODUCTS:
                raise SourceError("the committed seed did not ingest as 240 products")
            spec = settings.embedding_spec()
            provider = SentenceTransformerEmbedder(
                spec, settings.resolved_models_dir(), settings.embedding_batch_size
            )
            provider.load()
            first = embed(engine, provider)
            with Session(engine) as session:
                status = embedding_status(session, spec, provider)
            if first.inserted != SEED_PRODUCTS or not status.current:
                raise SourceError("embeddings are not current for all 240 products")
            del provider
            with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
                conn.execute(text("ANALYZE"))
            engine.dispose()
            engine = None
            base = settings.model_copy(update={"postgres_db": database})
            apps = {
                "base": create_app(base),
                **{
                    key: create_app(
                        base.model_copy(
                            update={"reranker_enabled": True, "search_rerank_depth": depth}
                        )
                    )
                    for key, depth in APP_DEPTH.items()
                },
            }
            with contextlib.ExitStack() as stack:
                clients = {key: stack.enter_context(TestClient(app)) for key, app in apps.items()}
                return fetch_sources(clients, queries, pins, response_schemas())
    finally:
        if engine is not None:
            engine.dispose()
        admin.dispose()
