"""scripts/benchmark_reranked.py: predeclared protocol, guards, contract, derivation, verify.

Pure tests only: no database, no model, no network (strict socket guard)."""

import contextlib
import copy
import importlib.util
import json
import re
import socket
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from ecommerce_search.embeddings.spec import DEFAULT_EMBEDDING_MODEL_ID, EMBEDDING_MODELS
from ecommerce_search.reranking.spec import DEFAULT_RERANKER_MODEL_ID, RERANKER_MODELS

ROOT = Path(__file__).resolve().parents[3]

pytestmark = pytest.mark.usefixtures("no_socket_connect")


def _load():
    name = "benchmark_reranked_under_test"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / "benchmark_reranked.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


bench = _load()

# The reviewed copy of the committed M5 query set. Any edit to the query set must fail here.
REVIEWED_QUERIES = (
    "laptop",
    "hp laptop",
    "8gb laptop",
    "iphone",
    "zzqxv",
    "coding ke liye laptop",
    "coding laptop",
    "student laptop",
    "lightweight laptop",
    "premium phone",
    "running shoes",
    "noise cancelling headphones",
    "256gb ssd laptop",
    "apple phone",
    "nike shoes",
    "wireless headphones",
    "anc headphones",
)
HEAD = "0123456789abcdef0123456789abcdef01234567"
MODELS = {
    "embedding": bench.check_model(
        Path("."), EMBEDDING_MODELS[DEFAULT_EMBEDDING_MODEL_ID], lambda *_: []
    ),
    "reranker": bench.check_reranker(
        Path("."),
        RERANKER_MODELS[DEFAULT_RERANKER_MODEL_ID],
        verify=lambda *_: [],
        config_problems=lambda *_: [],
    ),
}


# ---- predeclared protocol ------------------------------------------------------------------------


def test_queries_are_the_committed_m5_set_in_order_with_its_checksum():
    identity = bench.query_identity()
    assert tuple(identity["queries"]) == REVIEWED_QUERIES == bench.QUERIES
    assert identity["query_set_sha256"] == bench.QUERY_SET_SHA256 == bench.hybrid.QUERY_SET_SHA256
    assert bench.QUERY_SET_SHA256 == (
        "ea1df08db89a8bceb0e5b6437f18e75a30aed997fb70d727d080b441ebbd78d3"
    )
    assert identity["query_sha256"] == [bench.sha256_text(q) for q in REVIEWED_QUERIES]


@pytest.mark.parametrize(
    "queries",
    [
        REVIEWED_QUERIES[:-1],
        (*REVIEWED_QUERIES[:-1], "anc headphone"),
        (REVIEWED_QUERIES[1], REVIEWED_QUERIES[0], *REVIEWED_QUERIES[2:]),
        (*REVIEWED_QUERIES[:-1], REVIEWED_QUERIES[0]),
    ],
)
def test_added_removed_reworded_or_reordered_queries_are_refused(queries):
    with pytest.raises(bench.ProvenanceError):
        bench.query_identity(queries)


def test_arms_rotation_and_settings_are_exactly_predeclared():
    assert bench.ARMS == {
        "disabled": {"reranker_enabled": False, "search_rerank_depth": 20},
        "depth10": {"reranker_enabled": True, "search_rerank_depth": 10},
        "depth20": {"reranker_enabled": True, "search_rerank_depth": 20},
    }
    assert bench.ARM_SCHEDULE == {
        1: ("disabled", "depth10", "depth20"),
        2: ("depth10", "depth20", "disabled"),
        3: ("depth20", "disabled", "depth10"),
    }
    # A Latin square: each arm once per run and once in every position.
    for run, order in bench.ARM_SCHEDULE.items():
        assert sorted(order) == sorted(bench.ARMS)
        assert bench.arm_order(run) == order
    for position in range(3):
        assert sorted(o[position] for o in bench.ARM_SCHEDULE.values()) == sorted(bench.ARMS)
    with pytest.raises(bench.BenchmarkError):
        bench.arm_order(4)
    common = {
        "top_k": 10,
        "search_lexical_k": 50,
        "search_dense_k": 50,
        "search_candidate_k": 50,
        "search_rrf_k": 100,
    }
    assert bench.arm_settings("disabled") == {
        "reranker_enabled": False,
        "search_rerank_depth": 20,
        **common,
    }
    assert bench.arm_settings("depth10") == {
        "reranker_enabled": True,
        "search_rerank_depth": 10,
        **common,
    }
    assert bench.arm_settings("depth20") == {
        "reranker_enabled": True,
        "search_rerank_depth": 20,
        **common,
    }
    with pytest.raises(bench.BenchmarkError):
        bench.arm_settings("depth30")
    assert (bench.ENDPOINT, bench.METHOD, bench.TOP_K) == ("/search/reranked", "GET", 10)


def test_cold_warmup_and_timed_counts_are_exact():
    plan = bench.request_plan()
    assert plan[0] == ("cold", 0, 0)
    assert sum(1 for k, _, _ in plan if k == "cold") == 1
    for qi in range(17):
        warm = [s for k, q, s in plan if k == "warmup" and q == qi]
        timed = [s for k, q, s in plan if k == "timed" and q == qi]
        assert warm == list(range(20))
        assert timed == list(range(200))
    assert len(plan) == 1 + 17 * 220 == 3741
    assert bench.expected_request_count() == 3 * 3 * 3741 == 33669
    assert bench.PREDECLARED["runs"] == 3
    assert bench.PREDECLARED["cold_per_arm"] == 1
    assert bench.PREDECLARED["warmup_per_query"] == 20
    assert bench.PREDECLARED["timed_per_query"] == 200
    assert bench.PREDECLARED["excluded_samples"] == "none"
    assert bench.PREDECLARED["retries"] == "none"


def test_nearest_rank_percentiles_and_summary():
    s = bench.summarize([float(v) for v in range(200, 0, -1)])
    assert (s["n"], s["min"], s["p50"], s["p95"], s["p99"], s["max"], s["mean"]) == (
        200,
        1.0,
        100.0,
        190.0,
        198.0,
        200.0,
        100.5,
    )
    s = bench.summarize([5.0])
    assert s["p50"] == s["p95"] == s["p99"] == 5.0
    s = bench.summarize([1.0, 2.0, 3.0, 4.0, 10.0])
    assert (s["p50"], s["p95"], s["p99"]) == (3.0, 10.0, 10.0)


def test_null_values_are_counted_never_replaced():
    assert bench._metric_summary([None, None]) == {"count": 0, "null_count": 2, "stats": None}
    out = bench._metric_summary([1.0, None, 3.0])
    assert (out["count"], out["null_count"], out["stats"]["mean"]) == (2, 1, 2.0)


def test_experiment_id_and_artifact_names_are_deterministic(tmp_path):
    assert bench.experiment_id_for(HEAD) == "rerank-m8-latency-0123456789ab"
    assert bench.experiment_id_for(HEAD) == bench.experiment_id_for(HEAD)
    with pytest.raises(bench.BenchmarkError):
        bench.experiment_id_for("HEAD")
    paths = bench.artifact_paths(tmp_path, "rerank-m8-latency-0123456789ab")
    assert {k: p.name for k, p in paths.items()} == {
        "raw": "rerank-m8-latency-0123456789ab.samples.jsonl",
        "summary": "rerank-m8-latency-0123456789ab.json",
        "report": "rerank-m8-latency-0123456789ab.md",
    }


def test_output_directory_with_earlier_output_is_refused(tmp_path):
    fresh = tmp_path / "rerank_benchmark"
    assert bench.check_fresh_output(fresh) == fresh
    fresh.mkdir()
    assert bench.check_fresh_output(fresh) == fresh
    (fresh / "old.json").write_text("{}", encoding="utf-8")
    with pytest.raises(bench.BenchmarkError, match="already contains files"):
        bench.check_fresh_output(fresh)


def test_the_real_output_directory_is_ignored_and_under_data_processed():
    assert bench.OUTPUT_DIR == ROOT / "data" / "processed" / "rerank_benchmark"
    assert bench.check_output_dir(ROOT, bench.OUTPUT_DIR) == bench.OUTPUT_DIR.resolve()


# ---- provenance and settings guards --------------------------------------------------------------


def fake_git(status=b"", head=HEAD, ancestor=True, changed=b""):
    def git(root, *args, stdin=None):
        if args[0] == "status":
            return status
        if args[0] == "rev-parse":
            return head.encode() + b"\n"
        if args[0] == "merge-base":
            if not ancestor:
                raise bench.GitError("not an ancestor")
            return b""
        if args[0] == "diff":
            return changed
        raise AssertionError(args)

    return git


def test_provenance_requires_clean_tree_production_ancestor_and_unchanged_runtime():
    out = bench.check_provenance(ROOT, git=fake_git())
    assert out == {
        "harness_head": HEAD,
        "production_commit": "558bd39e69cc503410beb7efcb648fac169708b2",
        "guarded": list(bench.GUARDED_PATHS),
    }
    with pytest.raises(bench.ProvenanceError, match="not clean"):
        bench.check_provenance(ROOT, git=fake_git(status=b"?? x.py\n"))
    with pytest.raises(bench.ProvenanceError, match="ancestor"):
        bench.check_provenance(ROOT, git=fake_git(ancestor=False))
    with pytest.raises(bench.ProvenanceError, match="runtime-affecting"):
        bench.check_provenance(ROOT, git=fake_git(changed=b"src/ecommerce_search/api/app.py\n"))
    assert "src/" in bench.GUARDED_PATHS and "migrations/" in bench.GUARDED_PATHS


def test_approved_settings_pass_and_any_other_value_is_refused(make_settings):
    settings = make_settings()
    base = bench.check_base_settings(settings, "provisional")
    assert base["search_rrf_k"] == 100 and base["search_rerank_depth"] == 20
    for override in (
        {"search_rrf_k": 60},
        {"search_candidate_k": 40},
        {"search_lexical_k": 40},
        {"search_dense_k": 40},
        {"search_rerank_depth": 10},
        {"reranker_enabled": False},
        {"reranker_batch_size": 8},
        {"embedding_batch_size": 16},
        {"embedding_models_dir": Path("elsewhere")},
    ):
        with pytest.raises(bench.ProvenanceError, match="approved settings"):
            bench.check_base_settings(make_settings(**override), "provisional")
    with pytest.raises(bench.ProvenanceError, match="RRF_K_STATUS"):
        bench.check_base_settings(settings, "candidate_pending_selection")
    wrong_pin = SimpleNamespace(**{**settings.__dict__, "reranker_model_revision": "0" * 40})
    with pytest.raises(bench.ProvenanceError, match="registry pin"):
        bench.check_base_settings(wrong_pin, "provisional")


def test_reranker_pin_is_refused_when_the_snapshot_fails_verification():
    spec = RERANKER_MODELS[DEFAULT_RERANKER_MODEL_ID]
    ok = bench.check_reranker(Path("."), spec, lambda *_: [], lambda *_: [])
    assert ok["config_sha256"] == spec.config_sha256()
    assert (ok["model_id"], ok["revision"]) == (spec.model_id, spec.revision)
    with pytest.raises(bench.ProvenanceError, match="verification"):
        bench.check_reranker(Path("."), spec, lambda *_: ["bad hash"], lambda *_: [])
    with pytest.raises(bench.ProvenanceError, match="configuration"):
        bench.check_reranker(Path("."), spec, lambda *_: [], lambda *_: ["bad config"])
    with pytest.raises(bench.ProvenanceError, match="models directory"):
        bench.check_reranker(None, spec, lambda *_: [], lambda *_: [])


@pytest.mark.parametrize("name", ["ecommerce_search", "postgres", "ecommerce_search_test_x"])
def test_development_and_non_scratch_databases_are_refused(name):
    with pytest.raises(bench.BenchmarkError):
        bench.assert_scratch_name(name, "ecommerce_search")
    assert bench.assert_scratch_name("ecommerce_search_bench_0123456789ab", "ecommerce_search")


def child_args(tmp_path, **overrides):
    values = {
        "run": 1,
        "warmup": 20,
        "samples": 200,
        "experiment_id": "rerank-m8-latency-0123456789ab",
        "out": str(tmp_path / "run1.jsonl"),
        "database": "ecommerce_search_bench_0123456789ab",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_child_accepts_only_the_predeclared_arguments(tmp_path):
    bench.check_child_args(child_args(tmp_path), "ecommerce_search")
    for override in (
        {"run": 4},
        {"run": 0},
        {"warmup": 0},
        {"samples": 100},
        {"experiment_id": "hybrid-m5-cl-x"},
        {"out": str(ROOT / "data" / "processed" / "x.jsonl")},
        {"database": "ecommerce_search"},
        {"database": "other_db"},
    ):
        with pytest.raises(bench.BenchmarkError):
            bench.check_child_args(child_args(tmp_path, **override), "ecommerce_search")


# ---- cleanup and network -------------------------------------------------------------------------


class FakeConn:
    def __init__(self, log):
        self.log = log

    def execute(self, statement, params=None):
        self.log.append(str(statement))
        return self


class FakeAdmin:
    def __init__(self):
        self.log = []

    @contextlib.contextmanager
    def connect(self):
        yield FakeConn(self.log)


@pytest.mark.parametrize("failure", [RuntimeError("child failed"), KeyboardInterrupt()])
def test_scratch_database_is_dropped_on_failure_in_parent_and_child(failure):
    admin = FakeAdmin()
    name = bench.new_scratch_name()
    with pytest.raises(type(failure)), bench.dropped_after(admin, name, "ecommerce_search"):
        raise failure
    assert admin.log == [f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)']
    admin = FakeAdmin()
    with pytest.raises(type(failure)), bench.scratch_database(admin, name, "ecommerce_search"):
        raise failure
    assert admin.log == [
        f'CREATE DATABASE "{name}"',
        f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)',
    ]


def test_cleanup_refuses_the_development_database_before_any_sql():
    admin = FakeAdmin()
    with pytest.raises(bench.BenchmarkError), bench.dropped_after(admin, "ecommerce_search", "x"):
        pass
    with (
        pytest.raises(bench.BenchmarkError),
        bench.scratch_database(admin, "ecommerce_search", "ecommerce_search"),
    ):
        pass
    assert admin.log == []


def test_network_guard_refuses_non_loopback(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [("ok",)])
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: "conn")
    bench.install_network_guard()
    assert socket.getaddrinfo("127.0.0.1", 5432) == [("ok",)]
    assert socket.create_connection(("127.0.0.1", 5432)) == "conn"
    with pytest.raises(OSError, match="network access is disabled"):
        socket.getaddrinfo("huggingface.co", 443)
    with pytest.raises(OSError, match="network access is disabled"):
        socket.create_connection(("huggingface.co", 443))


def test_offline_environment_is_forced_for_runs():
    assert bench.OFFLINE_ENV == {
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "HF_HUB_DISABLE_TELEMETRY": "1",
    }


# ---- synthetic, contract-valid raw records -------------------------------------------------------

DEPTH20_RRF = [12, 1, 3, 15, 2, 4, 5, 6, 7, 8]


def pid(rank):
    return f"P-{rank:03d}"


def rows_for(arm):
    """A fixed fused list (candidate_count 50) and each arm's top 10 of it."""
    if arm == "disabled":
        rrf = list(range(1, 11))
        scores = [None] * 10
    elif arm == "depth10":
        rrf = list(range(10, 0, -1))
        scores = [5.0 - 0.5 * i for i in range(10)]
    else:
        rrf = DEPTH20_RRF
        scores = [4.0 - 0.25 * i for i in range(10)]
    rows = []
    for position, (r, score) in enumerate(zip(rrf, scores, strict=True), start=1):
        rows.append(
            {
                "rank": position,
                "product_id": pid(r),
                "rrf_rank": r,
                "rerank_rank": None if score is None else position,
                "rerank_score": score,
                "rrf_score": round(2 / (100 + r), 12),
                "lexical_rank": r if r <= 12 else None,
                "dense_rank": r,
            }
        )
    return rows


def body_for(arm, kind, timing_seed=0.0, first_applied=False):
    enabled = bench.ARMS[arm]["reranker_enabled"]
    depth = bench.ARMS[arm]["search_rerank_depth"]
    t = 1.0 + timing_seed
    return {
        "search_version": "v3_reranked",
        "embedding_model_id": MODELS["embedding"]["model_id"],
        "embedding_model_revision": MODELS["embedding"]["revision"],
        "reranker": {
            "enabled": enabled,
            "status": "applied" if enabled else "disabled",
            "model_id": MODELS["reranker"]["model_id"],
            "model_revision": MODELS["reranker"]["revision"],
            "document_version": "rd-1",
            "max_length": 512,
            "rerank_depth": depth,
            "reranked_count": depth if enabled else 0,
            "score_type": "logit",
        },
        "fusion": dict(bench.EXPECTED_FUSION),
        "lexical_hit_count": 12,
        "dense_hit_count": 50,
        "overlap_count": 12,
        "fused_count": 50,
        "candidate_count": 50,
        "top_k": 10,
        "result_count": 10,
        "results": rows_for(arm),
        "filter_policy": {"version": "fp-1"},
        "applied_filters": [{"field": "category", "operator": "eq", "value": "laptop"}],
        "ignored_constraints": [],
        "query_understanding": {
            "usage": "filter_source",
            "provider": "deterministic",
            "provider_version": "qu-1",
            "understanding": {"category": "laptop"},
        },
        "latency_ms": {
            "total_ms": 10 * t,
            "query_understanding_ms": 0.1 * t,
            "filter_translation_ms": 0.05 * t,
            "model_load_ms": 500.0 if kind == "cold" else None,
            "query_embedding_ms": 4 * t,
            "lexical_ms": 1 * t,
            "vector_ms": 2 * t,
            "document_read_ms": 1.5 * t if enabled else None,
            "rrf_ms": 0.02 * t,
            "reranker_load_ms": 300.0 if first_applied else None,
            "rerank_document_ms": 0.3 * t if enabled else None,
            "reranking_ms": 20 * t if enabled else None,
        },
    }


def sample_record(run, arm, position, kind, qi, si, body=None):
    if body is None:
        seed = (si % 7) * 0.1 + qi * 0.01
        enabled = bench.ARMS[arm]["reranker_enabled"]
        body = body_for(arm, kind, seed, first_applied=enabled and kind == "cold")
    part = bench.body_to_record(body, 0.2 + (si % 5) * 0.01, 15.0 + (si % 11) * 0.1)
    part.pop("embedding_model_id")
    part.pop("embedding_model_revision")
    record = {
        "experiment_id": bench.experiment_id_for(HEAD),
        "run": run,
        "sequence": None,
        "record": "sample",
        "arm": arm,
        "arm_position": position,
        "arm_order": list(bench.arm_order(run)),
        "query_index": qi,
        "query_sha256": bench.sha256_text(bench.QUERIES[qi]),
        "kind": kind,
        "sample_index": si,
        "timestamp_utc": f"2026-10-03T12:{run:02d}:{position:02d}.{si:06d}+00:00",
        "settings": bench.arm_settings(arm),
        "models": bench.record_models(MODELS),
        "status_code": 200,
        **part,
    }
    record["hashes"] = bench.record_hashes(record)
    return record


def resources(rss=200 * 2**20, peak=400 * 2**20):
    return {"rss_bytes": rss, "peak_rss_bytes": peak}


def make_header():
    from ecommerce_search.embeddings.text import EMBEDDING_TEXT_VERSION

    return {
        "experiment_id": bench.experiment_id_for(HEAD),
        "generated_at_utc": "2026-10-03T12:00:00+00:00",
        "provenance": {
            "harness_head": HEAD,
            "production_commit": bench.PRODUCTION_COMMIT,
            "guarded": list(bench.GUARDED_PATHS),
        },
        "models": MODELS,
        "catalog": bench.committed_catalog_identity(),
        "embedding_text_version": EMBEDDING_TEXT_VERSION,
        "query_identity": bench.query_identity(),
        "protocol": bench.PREDECLARED,
        "base_settings": {
            k: v for k, v in bench.BASE_SETTINGS.items() if k != "embedding_models_dir"
        },
        "arm_settings": {arm: bench.arm_settings(arm) for arm in bench.ARMS},
        "scope": bench.SCOPE,
        "nullability_rules": bench.NULLABILITY_RULES,
        "metric_definitions": bench.METRIC_DEFINITIONS,
    }


def make_records(header):
    records = []
    catalog = header["catalog"]
    plan = bench.request_plan()
    for run in range(1, 4):
        sequence = 0

        def emit(record, run=run):
            nonlocal sequence
            record.update({"experiment_id": header["experiment_id"], "run": run})
            record["sequence"] = sequence
            sequence += 1
            records.append(record)

        order = bench.arm_order(run)
        emit(
            {
                "record": "run",
                "arm_order": list(order),
                "catalog_datasets": [{k: catalog[k] for k in bench.hybrid.CATALOG_KEYS}],
                "product_count": 240,
                "embedding_rows": 240,
                "migration_head": "0004",
                "setup_resources": resources(),
                "torch_num_threads": 4,
                "libraries": {},
            }
        )
        for position, arm in enumerate(order):
            common = {
                "arm": arm,
                "arm_position": position,
                "arm_order": list(order),
                "settings": bench.arm_settings(arm),
            }
            emit({"record": "arm_start", **common, **resources()})
            for kind, qi, si in plan:
                emit(sample_record(run, arm, position, kind, qi, si))
            enabled = bench.ARMS[arm]["reranker_enabled"]
            calls = len(plan) if enabled else 0
            emit(
                {
                    "record": "arm_end",
                    **common,
                    **resources(),
                    "reranker_probe": {
                        "load_calls": calls,
                        "count_calls": calls,
                        "score_calls": calls,
                        "max_checked_out_during_reranker_calls": 0,
                        "loaded": enabled,
                    },
                    "embedder_loaded": True,
                    "requests": len(plan),
                    "pool_checked_out_after": 0,
                }
            )
    return records


@pytest.fixture(scope="module")
def dataset():
    header = make_header()
    return header, make_records(header)


@pytest.fixture(scope="module")
def derived(dataset):
    header, records = dataset
    return bench.derive(header, records)


@pytest.fixture(scope="module")
def artifacts(dataset, tmp_path_factory):
    header, records = dataset
    out = tmp_path_factory.mktemp("artifacts")
    paths = bench.artifact_paths(out, header["experiment_id"])
    raw_sha = bench.write_raw(paths, header, records)
    bench.write_summary(paths, header, records, raw_sha, {"note": "synthetic"})
    return paths


def replaced(records, index, mutate):
    """A shallow copy of `records` with one deep-copied, mutated record."""
    out = list(records)
    record = copy.deepcopy(out[index])
    mutate(record)
    out[index] = record
    return out


def first_index(records, **match):
    return next(i for i, r in enumerate(records) if all(r.get(k) == v for k, v in match.items()))


# ---- derivation ----------------------------------------------------------------------------------


def test_raw_record_schema_is_exact(dataset):
    _, records = dataset
    samples = [r for r in records if r["record"] == "sample"]
    assert len(samples) == 33669
    assert set(samples[0]) == bench.SAMPLE_KEYS
    assert set(samples[0]["timings"]) == set(bench.TIMING_FIELDS)
    assert set(samples[0]["results"]) == set(bench.RESULT_KEYS)
    assert set(samples[0]["hashes"]) == {"ranking_sha256", "scores_sha256", "facts_sha256"}
    text = json.dumps(records[:5])
    assert "postgresql" not in text and "password" not in text and ":\\\\" not in text


def test_summary_recomputes_counts_latency_and_nulls(derived):
    assert derived["counts"] == {
        "sample_records": 33669,
        "timed": 30600,
        "warmup": 3060,
        "cold": 9,
        "status_200": 33669,
    }
    for run, data in derived["runs"].items():
        assert data["arm_order"] == list(bench.arm_order(int(run)))
        for arm, a in data["arms"].items():
            assert a["counts"] == {"cold": 1, "warmup": 340, "timed": 3400}
            overall = a["timed_overall"]
            assert overall["request_ms"]["count"] == 3400
            assert overall["model_load_ms"] == {"count": 0, "null_count": 3400, "stats": None}
            assert overall["reranker_load_ms"]["stats"] is None
            if arm == "disabled":
                for metric in ("document_read_ms", "rerank_document_ms", "reranking_ms"):
                    assert overall[metric] == {"count": 0, "null_count": 3400, "stats": None}
                assert a["cold"]["timings"]["reranker_load_ms"] is None
            else:
                assert overall["reranking_ms"]["count"] == 3400
                assert a["cold"]["timings"]["reranker_load_ms"] == 300.0
            assert a["cold"]["timings"]["model_load_ms"] == 500.0
            assert len(a["timed_per_query"]) == 17
            assert a["timed_per_query"]["0"]["request_ms"]["count"] == 200
    expected = sorted(15.0 + (si % 11) * 0.1 for si in range(200)) * 17
    stats = derived["runs"]["1"]["arms"]["disabled"]["timed_overall"]["request_ms"]["stats"]
    values = sorted(expected)
    assert stats["p50"] == round(values[1699], 3)
    assert stats["p95"] == round(values[3229], 3)
    assert stats["p99"] == round(values[3365], 3)
    assert set(derived["pooled_timed_secondary"]) == set(bench.ARMS)
    assert derived["pooled_timed_secondary"]["depth10"]["reranking_ms"]["count"] == 10200


def test_rank_movement_is_recomputed_as_neutral_facts(derived):
    facts = derived["runs"]["1"]["rank_facts"]
    q0 = facts["per_query"]["0"]
    d10 = q0["pairs"]["disabled|depth10"]
    assert d10["overlap"] == 10 and d10["same_position"] == 0 and d10["changed_positions"] == 10
    assert d10["mean_abs_movement_common"] == {"fraction": "5", "float": 5.0}
    d20 = q0["pairs"]["disabled|depth20"]
    assert d20["overlap"] == 8
    assert d20["only_in_second"] == ["P-012", "P-015"]
    assert d20["only_in_first"] == ["P-009", "P-010"]
    assert q0["head_movement"]["depth20"]["results_with_rrf_rank_beyond_top_k"] == 2
    assert "disabled" not in q0["head_movement"]
    assert facts["totals"]["disabled|depth10"]["overlap_sum"] == 170
    assert facts["label"] == bench.RANK_LABEL
    assert derived["determinism_across_runs"]["depth20"]["ranking_identical_all_queries"] is True


def test_rank_movement_function_on_known_lists():
    moved = bench.rank_movement(["a", "b", "c", "d"], ["b", "a", "c", "e"])
    assert moved["overlap"] == 3
    assert moved["same_position"] == 1 and moved["changed_positions"] == 3
    assert moved["mean_abs_movement_common"] == {"fraction": "2/3", "float": 0.666667}
    assert moved["promoted"] == [{"product_id": "b", "from": 2, "to": 1}]
    assert moved["demoted"] == [{"product_id": "a", "from": 1, "to": 2}]
    assert (moved["only_in_first"], moved["only_in_second"]) == (["d"], ["e"])
    same = bench.rank_movement(["a"], ["a"])
    assert same["identical_order"] is True and same["changed_positions"] == 0
    empty = bench.rank_movement([], [])
    assert empty["overlap"] == 0 and empty["mean_abs_movement_common"] is None


def test_deterministic_hashes_are_stable_and_sensitive(dataset):
    _, records = dataset
    record = records[first_index(records, record="sample", arm="depth20")]
    assert bench.record_hashes(record) == bench.record_hashes(copy.deepcopy(record))
    changed = copy.deepcopy(record)
    changed["results"]["rerank_scores"][0] += 1e-12
    assert bench.ranking_sha256(changed["results"]) == bench.ranking_sha256(record["results"])
    assert bench.scores_sha256(changed["results"]) != bench.scores_sha256(record["results"])
    changed["results"]["product_ids"][0] = "P-999"
    assert bench.ranking_sha256(changed["results"]) != bench.ranking_sha256(record["results"])


@pytest.mark.parametrize(
    ("label", "match", "mutate"),
    [
        (
            "disabled score",
            "null without reranking",
            lambda r: r["results"]["rerank_scores"].__setitem__(0, 1.0),
        ),
        (
            "disabled timing",
            "reranking_ms must be null",
            lambda r: r["timings"].__setitem__("reranking_ms", 1.0),
        ),
        ("settings", "settings differ", lambda r: r["settings"].__setitem__("search_rrf_k", 60)),
        (
            "model",
            "model identity",
            lambda r: r["models"].__setitem__("reranker_model_revision", "0" * 40),
        ),
        ("status", "not a 200", lambda r: r.__setitem__("status_code", 503)),
        ("counts", "counts are inconsistent", lambda r: r["counts"].__setitem__("fused_count", 49)),
        (
            "duplicate id",
            "unique",
            lambda r: r["results"]["product_ids"].__setitem__(1, r["results"]["product_ids"][0]),
        ),
        ("query hash", "query hash", lambda r: r.__setitem__("query_sha256", "0" * 64)),
        (
            "model load",
            "model_load_ms must be null",
            lambda r: r["timings"].__setitem__("model_load_ms", 1.0),
        ),
    ],
)
def test_derivation_refuses_altered_records(dataset, label, match, mutate):
    header, records = dataset
    index = first_index(records, record="sample", arm="disabled", kind="timed")
    with pytest.raises(bench.BenchmarkError, match=match):
        bench.derive(header, replaced(records, index, mutate))


def test_an_edited_timed_latency_changes_the_recomputed_summary(dataset, derived):
    header, records = dataset
    index = first_index(records, record="sample", arm="disabled", kind="timed")
    edited = replaced(records, index, lambda r: r["timings"].__setitem__("request_ms", 999.0))
    assert bench.derive(header, edited) != derived


def test_enabled_records_need_finite_ordered_logits(dataset):
    header, records = dataset
    index = first_index(records, record="sample", arm="depth10", kind="timed")

    def nan(r):
        r["results"]["rerank_scores"][0] = None
        r["hashes"] = bench.record_hashes(r)

    with pytest.raises(bench.BenchmarkError, match="leading positions|finite"):
        bench.derive(header, replaced(records, index, nan))

    def unordered(r):
        scores = r["results"]["rerank_scores"]
        scores[0], scores[1] = scores[1], scores[0]
        r["hashes"] = bench.record_hashes(r)

    with pytest.raises(bench.BenchmarkError, match="ordered by score"):
        bench.derive(header, replaced(records, index, unordered))


def test_changed_result_list_within_a_group_is_refused(dataset):
    header, records = dataset
    index = first_index(records, record="sample", arm="depth20", kind="timed", query_index=3)

    def swap(r):
        ids = r["results"]["product_ids"]
        ids[0], ids[1] = ids[1], ids[0]
        r["hashes"] = bench.record_hashes(r)

    with pytest.raises(bench.BenchmarkError, match="changed between requests"):
        bench.derive(header, replaced(records, index, swap))


def test_missing_duplicate_or_reordered_records_are_refused(dataset):
    header, records = dataset
    index = first_index(records, record="sample", kind="timed")
    with pytest.raises(bench.BenchmarkError, match="missing, duplicated or reordered"):
        bench.derive(header, records[:index] + records[index + 1 :])
    with pytest.raises(bench.BenchmarkError, match="missing, duplicated or reordered"):
        bench.derive(header, records[: index + 1] + records[index:])
    swapped = list(records)
    swapped[index], swapped[index + 1] = swapped[index + 1], swapped[index]
    with pytest.raises(bench.BenchmarkError, match="missing, duplicated or reordered"):
        bench.derive(header, swapped)

    def renumber(rs):
        out, seq, run = [], 0, None
        for r in rs:
            if r["run"] != run:
                run, seq = r["run"], 0
            out.append({**r, "sequence": seq})
            seq += 1
        return out

    with pytest.raises(bench.BenchmarkError, match="expected .* records"):
        bench.derive(header, renumber(records[:index] + records[index + 1 :]))
    with pytest.raises(bench.BenchmarkError):
        bench.derive(header, [r for r in records if r["run"] != 3])


def test_disabled_arm_touching_the_reranker_or_a_held_connection_is_refused(dataset):
    header, records = dataset
    end = first_index(records, record="arm_end", arm="disabled")
    with pytest.raises(bench.BenchmarkError, match="touched the reranker"):
        bench.derive(
            header, replaced(records, end, lambda r: r["reranker_probe"].update(load_calls=1))
        )
    enabled_end = first_index(records, record="arm_end", arm="depth10")
    with pytest.raises(bench.BenchmarkError, match="connection was held"):
        bench.derive(
            header,
            replaced(
                records,
                enabled_end,
                lambda r: r["reranker_probe"].update(max_checked_out_during_reranker_calls=1),
            ),
        )
    with pytest.raises(bench.BenchmarkError, match="call counts"):
        bench.derive(
            header,
            replaced(records, enabled_end, lambda r: r["reranker_probe"].update(score_calls=1)),
        )


def test_wrong_arm_order_or_peak_rss_facts_are_refused(dataset):
    header, records = dataset
    with pytest.raises(bench.BenchmarkError, match="arm order"):
        bench.derive(
            header, replaced(records, 0, lambda r: r.update(arm_order=["depth10", "depth20", "x"]))
        )
    start = first_index(records, record="arm_start")
    with pytest.raises(bench.BenchmarkError, match="peak RSS"):
        bench.derive(
            header, replaced(records, start, lambda r: r.update(peak_rss_bytes=r["rss_bytes"] - 1))
        )
    with pytest.raises(bench.BenchmarkError, match="rss_bytes"):
        bench.derive(header, replaced(records, start, lambda r: r.update(rss_bytes=None)))


def test_cross_arm_candidate_facts_must_stay_v2_compatible(dataset):
    header, records = dataset
    index = first_index(records, record="sample", arm="depth10", kind="cold")

    def other_rrf(r):
        r["results"]["rrf_scores"] = [score * 0.999 for score in r["results"]["rrf_scores"]]
        r["hashes"] = bench.record_hashes(r)

    with pytest.raises(bench.BenchmarkError, match="fused candidate facts differ from disabled"):
        bench.derive(header, replaced(records, index, other_rrf))


# ---- artifacts and --verify ----------------------------------------------------------------------


def test_written_artifacts_verify(artifacts):
    assert bench.verify_artifacts(artifacts["summary"], MODELS) == []
    report = artifacts["report"].read_text(encoding="utf-8")
    assert bench.DISCLAIMER in report
    assert "| 1 | disabled | request_ms | 3400 | 0 |" in report


def _rewrite(paths, tmp_path, raw_edit=None, summary_edit=None):
    target = tmp_path / "copy"
    target.mkdir()
    raw = paths["raw"].read_text(encoding="utf-8")
    summary = json.loads(paths["summary"].read_text(encoding="utf-8"))
    if raw_edit:
        raw = raw_edit(raw)
    if summary_edit:
        summary_edit(summary)
    (target / paths["raw"].name).write_bytes(raw.encode("utf-8"))
    out = target / paths["summary"].name
    out.write_text(json.dumps(summary), encoding="utf-8")
    return out


def test_verify_detects_an_edited_latency_even_with_a_matching_raw_hash(artifacts, tmp_path):
    import hashlib

    def edit(raw):
        lines = raw.split("\n")
        index = next(i for i, line in enumerate(lines) if '"kind":"timed"' in line)
        record = json.loads(lines[index])
        record["timings"]["request_ms"] = 12345.0
        lines[index] = bench.dumps(record)
        return "\n".join(lines)

    path = _rewrite(artifacts, tmp_path, raw_edit=edit)
    problems = bench.verify_artifacts(path, MODELS)
    assert "raw samples file hash differs from the summary" in problems
    summary = json.loads(path.read_text(encoding="utf-8"))
    raw_bytes = path.with_name(summary["raw_samples"]["file"]).read_bytes()
    summary["raw_samples"]["sha256"] = hashlib.sha256(raw_bytes).hexdigest()
    path.write_text(json.dumps(summary), encoding="utf-8")
    problems = bench.verify_artifacts(path, MODELS)
    assert problems == ["derived summary (latency, determinism or rank facts) does not recompute"]


def test_verify_detects_missing_and_duplicate_records(artifacts, tmp_path):
    def drop(raw):
        lines = raw.split("\n")
        return "\n".join(lines[:10] + lines[11:])

    problems = bench.verify_artifacts(_rewrite(artifacts, tmp_path, raw_edit=drop), MODELS)
    assert any("incomplete or inconsistent" in p for p in problems)

    def dup(raw):
        lines = raw.split("\n")
        return "\n".join(lines[:10] + [lines[10]] + lines[10:])

    other = tmp_path / "dup"
    other.mkdir()
    problems = bench.verify_artifacts(_rewrite(artifacts, other, raw_edit=dup), MODELS)
    assert any("missing, duplicated or reordered" in p for p in problems)
    missing = tmp_path / "missing.json"
    assert bench.verify_artifacts(missing, MODELS)[0].startswith("artifacts are missing")


@pytest.mark.parametrize(
    ("edit", "message"),
    [
        (lambda h: h["base_settings"].__setitem__("search_rrf_k", 60), "approved base settings"),
        (
            lambda h: h["models"]["reranker"].__setitem__("revision", "0" * 40),
            "model identity",
        ),
        (
            lambda h: h["query_identity"].__setitem__("query_set_sha256", "0" * 64),
            "query identity",
        ),
        (lambda h: h["protocol"].__setitem__("timed_per_query", 100), "predeclared protocol"),
        (lambda h: h.__setitem__("experiment_id", "rerank-m8-latency-ffffffffffff"), "HEAD"),
        (lambda h: h["provenance"].__setitem__("production_commit", "0" * 40), "production"),
    ],
)
def test_verify_detects_wrong_settings_model_query_checksum_or_protocol(edit, message):
    header = copy.deepcopy(make_header())
    assert bench.check_header(header, MODELS) == []
    edit(header)
    problems = bench.check_header(header, MODELS)
    assert any(message in p for p in problems), problems


def test_verify_refuses_a_summary_whose_header_was_altered(artifacts, tmp_path):
    def edit(summary):
        summary["header"]["base_settings"]["search_rerank_depth"] = 10

    problems = bench.verify_artifacts(_rewrite(artifacts, tmp_path, summary_edit=edit), MODELS)
    assert "raw header differs from the summary header" in problems


# ---- run-process collector with a fake client ----------------------------------------------------


class FakeResponse:
    def __init__(self, body, status_code=200):
        self._body = body
        self.status_code = status_code

    def json(self):
        return copy.deepcopy(self._body)


class FakeClient:
    def __init__(self, arm, status_code=200, change_at=None):
        self.arm = arm
        self.calls = []
        self.status_code = status_code
        self.change_at = change_at

    def get(self, path, params):
        self.calls.append((path, params))
        kind = "cold" if len(self.calls) == 1 else "timed"
        enabled = bench.ARMS[self.arm]["reranker_enabled"]
        body = body_for(self.arm, kind, first_applied=enabled and len(self.calls) == 1)
        if self.change_at is not None and len(self.calls) == self.change_at:
            rows = body["results"]
            rows[0]["product_id"], rows[1]["product_id"] = (
                rows[1]["product_id"],
                rows[0]["product_id"],
            )
        return FakeResponse(body, self.status_code)


def parse(body):
    return SimpleNamespace(model_dump_json=lambda: json.dumps(body))


@pytest.mark.parametrize("arm", list(bench.ARMS))
def test_collector_issues_exactly_the_predeclared_get_requests(arm):
    client = FakeClient(arm)
    emitted = []
    queries = bench.QUERIES[:2]
    bench.collect_arm(client, arm, 0, 1, queries, MODELS, emitted.append, parse)
    plan = bench.request_plan(2)
    assert len(client.calls) == len(emitted) == len(plan) == 1 + 2 * 220
    assert {path for path, _ in client.calls} == {"/search/reranked"}
    assert [p for _, p in client.calls][:2] == [
        {"q": "laptop", "top_k": 10},
        {"q": "laptop", "top_k": 10},
    ]
    assert [(r["kind"], r["query_index"], r["sample_index"]) for r in emitted] == plan
    record = emitted[0]
    assert set(record) == bench.SAMPLE_KEYS
    assert record["timings"]["serialization_ms"] >= 0
    assert record["timings"]["request_ms"] >= 0
    assert record["settings"] == bench.arm_settings(arm)


def test_collector_aborts_on_non_200_and_on_a_changed_result_list():
    with pytest.raises(bench.BenchmarkError, match="status 503"):
        bench.collect_arm(
            FakeClient("depth10", 503),
            "depth10",
            0,
            1,
            bench.QUERIES[:1],
            MODELS,
            list().append,
            parse,
        )
    with pytest.raises(bench.BenchmarkError, match="changed between requests"):
        bench.collect_arm(
            FakeClient("depth10", change_at=5),
            "depth10",
            0,
            1,
            bench.QUERIES[:1],
            MODELS,
            list().append,
            parse,
        )
    with pytest.raises(bench.BenchmarkError, match="changed between requests"):
        bench.collect_arm(
            FakeClient("disabled", change_at=5),
            "disabled",
            0,
            1,
            bench.QUERIES[:1],
            MODELS,
            list().append,
            parse,
        )


def test_collector_refuses_a_wrong_reranker_status_for_the_arm():
    class Wrong(FakeClient):
        def get(self, path, params):
            response = super().get(path, params)
            response._body["reranker"]["status"] = "applied"
            return response

    with pytest.raises(bench.BenchmarkError, match="reranker block"):
        bench.collect_arm(
            Wrong("disabled"), "disabled", 0, 1, bench.QUERIES[:1], MODELS, list().append, parse
        )


class InnerReranker:
    def __init__(self):
        self.spec = RERANKER_MODELS[DEFAULT_RERANKER_MODEL_ID]
        self.loaded = False

    def load(self):
        self.loaded = True
        return 1.5

    def count_pair_tokens(self, query, documents):
        return [3] * len(documents)

    def score(self, query, documents):
        return [0.5] * len(documents)


def test_reranker_probe_is_a_pass_through_and_records_connections():
    inner = InnerReranker()
    checked_out = iter([0, 0, 2])
    probe = bench.RerankerProbe(inner, lambda: next(checked_out))
    assert probe.spec is inner.spec and probe.loaded is False
    assert probe.load() == 1.5
    assert probe.count_pair_tokens("q", ["a", "b"]) == [3, 3]
    assert probe.score("q", ["a", "b"]) == [0.5, 0.5]
    assert probe.facts() == {
        "load_calls": 1,
        "count_calls": 1,
        "score_calls": 1,
        "max_checked_out_during_reranker_calls": 2,
        "loaded": True,
    }


def test_peak_rss_is_read_from_the_os_or_refused(monkeypatch):
    info = SimpleNamespace(rss=10, peak_wset=20)
    process = SimpleNamespace(memory_info=lambda: info)
    assert bench.peak_rss_bytes(process) == 20
    assert bench.resources(process) == {"rss_bytes": 10, "peak_rss_bytes": 20}
    monkeypatch.setattr(bench.sys, "platform", "darwin")
    with pytest.raises(bench.BenchmarkError, match="peak RSS"):
        bench.peak_rss_bytes(SimpleNamespace(memory_info=lambda: SimpleNamespace(rss=10)))


# ---- scope: no quality metric, no winner, no production change -----------------------------------

FORBIDDEN = re.compile(
    r"\b(ndcg|mrr|map@|precision|recall|accuracy|csr|win[ _-]?rate|winner|better|worse|"
    r"improv\w*|quality|relevan\w*|best|optimal)\b",
    re.IGNORECASE,
)


def test_no_quality_metric_or_judgement_in_derived_figures_or_report(derived, artifacts):
    text = json.dumps(derived)
    report = artifacts["report"].read_text(encoding="utf-8")
    for allowed in (bench.DISCLAIMER, bench.RANK_LABEL):
        text = text.replace(allowed, "")
        report = report.replace(allowed, "")
    assert not FORBIDDEN.findall(text)
    assert not FORBIDDEN.findall(report)


def test_harness_defines_no_quality_metric_or_selection():
    source = (ROOT / "scripts" / "benchmark_reranked.py").read_text(encoding="utf-8")
    for name in (
        "ndcg",
        "mrr",
        "precision",
        "recall",
        "accuracy",
        "win_rate",
        "consistency(",
        "decide",
        "select_",
        "best_",
    ):
        assert name not in source.lower(), name


def test_harness_uses_only_get_on_the_v3_route_and_never_mutates_the_app():
    source = (ROOT / "scripts" / "benchmark_reranked.py").read_text(encoding="utf-8")
    assert "client.get(ENDPOINT" in source
    for verb in ("client.post", "client.put", "client.delete", "client.patch"):
        assert verb not in source
    assert "include_router" not in source and "dependency_overrides" not in source
    assert "app.state.reranker = probe" in source  # the only app change: a pass-through probe


def test_importing_the_harness_loads_no_model_library():
    import subprocess

    code = (
        "import importlib.util, sys;"
        "spec = importlib.util.spec_from_file_location('b', 'scripts/benchmark_reranked.py');"
        "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m);"
        "print(sorted(x for x in ('torch', 'sentence_transformers', 'transformers') "
        "if x in sys.modules))"
    )
    done = subprocess.run(  # noqa: S603 - fixed argv
        [sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, check=True
    )
    assert done.stdout.strip() == "[]"
