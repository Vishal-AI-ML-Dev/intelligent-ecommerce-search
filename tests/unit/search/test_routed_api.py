"""Experimental V4 `/search/routed` (rule `jr-1`) with fake sources, embedder, reranker and Jev.

The in-test catalog, fake session, fake embedder and fake reranker are those of the V3 route
tests, so the real V3 reads, record rebuild, fusion and head reranking run. Jev is a fake
provider returning hand-built `JevOutcome`s (fabricated for software tests; never Jev output).
No database, model, network or key is used; nothing here measures relevance.
"""

from contextlib import ExitStack
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from test_reranked_api import (
    FakeSession,
    RecordingEmbedder,
    RecordingReranker,
    dense_hits_for,
    lexical_hits_for,
)

from ecommerce_search.api import routed as routed_api
from ecommerce_search.api.app import create_app
from ecommerce_search.api.dependencies import (
    get_db_session,
    get_embedder,
    get_reranker,
    get_v4_jev_provider,
)
from ecommerce_search.catalog.taxonomy import Category
from ecommerce_search.decision import DeterministicDecisionProvider
from ecommerce_search.decision.jev_provider import JevConfigurationError
from ecommerce_search.decision.provider import (
    CategoryProposal,
    DecisionResult,
    IntentProposal,
    JevOutcome,
)
from ecommerce_search.filtering import FilterSpec
from ecommerce_search.query_understanding import parse_normalized_query
from ecommerce_search.query_understanding.models import Intent
from ecommerce_search.search import filtered as filtered_search
from ecommerce_search.search.dense import DenseResult
from ecommerce_search.search.hybrid import fuse_rrf
from ecommerce_search.search.lexical import LexicalResult
from ecommerce_search.search.routed import (
    fuse_routed,
    pre_call_status,
    routed_filters,
    routing_category,
)

FAKE_KEY = "ts-test-key-not-real-7f3a9c"
V4 = "/search/routed"
V3 = "/search/reranked"
V4_ONLY_RESULT_KEYS = ("routed_lexical_rank", "routed_dense_rank", "in_baseline_sources")
CATEGORIES = ("laptop", "phone", "shoes", "headphones", "none")
INTENTS = (*(i.value for i in Intent), "none")


def outcome(category: str = "phone", *, tie: bool = False, fallback: str | None = None):
    """A fabricated Jev outcome (software-test data, never Jev output)."""
    if fallback is not None:
        return JevOutcome(model="jev-1.13.0", latency_ms=12.0, fallback_reason=fallback)
    if tie:
        probabilities = {label: 0.05 for label in CATEGORIES} | {"laptop": 0.425, "phone": 0.425}
        top = ("laptop", "phone")
    else:
        probabilities = {label: (0.8 if label == category else 0.05) for label in CATEGORIES}
        top = (category,)
    label = None if tie or category == "none" else Category(category)
    return JevOutcome(
        model="jev-1.13.0",
        response_model="jev-1.13.0",
        latency_ms=12.0,
        input_tokens=640,
        output_tokens=0,
        category=CategoryProposal(
            label=label,
            raw_choice=top[0],
            top_labels=top,
            provider_probabilities=probabilities,
            provider_confidence=0.7,
        ),
        semantic_intent=IntentProposal(
            label=None,
            raw_choice="none",
            top_labels=("none",),
            provider_probabilities={i: (0.6 if i == "none" else 0.05) for i in INTENTS},
            provider_confidence=0.5,
        ),
    )


class FakeJev:
    def __init__(self, result=None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.calls: list[str] = []

    def understand(self, query, deterministic_result):
        self.calls.append(query)
        if self.error is not None:
            raise self.error
        if isinstance(self.result, JevOutcome):
            return DecisionResult(
                provider="jev",
                provider_version="jq-1",
                understanding=deterministic_result,
                jev=self.result,
            )
        return self.result


@pytest.fixture
def events():
    return []


@pytest.fixture
def client_for(make_settings, monkeypatch, events):
    stack = ExitStack()

    def _make(jev: FakeJev | None = None, *, enter: bool = True, **settings):
        sessions: list = []
        embedder = RecordingEmbedder(events, sessions)
        reranker = RecordingReranker(events, sessions)

        def fake_lexical(session, query, filters, limit):
            assert session.in_transaction() and type(filters) is FilterSpec
            events.append(("lexical", filters))
            return LexicalResult(tsquery="'x'", hits=lexical_hits_for(filters, limit), lexical_ms=1)

        def fake_dense(session, vector, spec, filters, limit):
            assert session.in_transaction() and type(filters) is FilterSpec
            events.append(("dense", filters))
            return DenseResult(hits=dense_hits_for(filters, limit), vector_ms=2)

        monkeypatch.setattr(filtered_search, "filtered_lexical_search", fake_lexical)
        monkeypatch.setattr(filtered_search, "filtered_dense_search", fake_dense)

        def session_dependency():
            session = FakeSession(events)
            sessions.append(session)
            try:
                yield session
            finally:
                session.close()

        app = create_app(make_settings(**settings))
        app.dependency_overrides[get_db_session] = session_dependency
        app.dependency_overrides[get_embedder] = lambda: embedder
        app.dependency_overrides[get_reranker] = lambda: reranker
        if jev is not None:
            app.dependency_overrides[get_v4_jev_provider] = lambda: lambda: jev
        client = TestClient(app)
        return stack.enter_context(client) if enter else client

    with stack:
        yield _make


def v3_view(v4: dict) -> dict:
    """A V4 body reduced to what must equal the V3 body for the same request."""
    body = {k: v for k, v in v4.items() if k not in ("latency_ms", "search_version", "jev_routing")}
    body["results"] = [
        {k: v for k, v in result.items() if k not in V4_ONLY_RESULT_KEYS}
        for result in v4["results"]
    ]
    return body


def v3_body(v3: dict) -> dict:
    return {k: v for k, v in v3.items() if k not in ("latency_ms", "search_version")}


def source_reads(events: list) -> list:
    return [e for e in events if isinstance(e, tuple) and e[0] in ("lexical", "dense")]


# ---- pure rule ----------------------------------------------------------------------------------


def test_jev_is_asked_only_without_a_deterministic_category_or_conflict():
    gaming = parse_normalized_query("gaming")
    assert pre_call_status(gaming, enabled=False, searchable=True) == "disabled"
    assert pre_call_status(gaming, enabled=True, searchable=False) == "skipped_no_searchable_text"
    assert pre_call_status(gaming, enabled=True, searchable=True) is None
    laptop = parse_normalized_query("gaming laptop")
    assert pre_call_status(laptop, enabled=True, searchable=True) == (
        "skipped_deterministic_category"
    )
    conflict = parse_normalized_query("laptop phone")
    assert conflict.category is None and conflict.conflicts
    assert pre_call_status(conflict, enabled=True, searchable=True) == "skipped_category_conflict"


def test_only_a_unique_non_none_category_routes():
    assert routing_category(outcome("phone")) == ("routed", Category.PHONE)
    assert routing_category(outcome("none")) == ("jev_no_category", None)
    assert routing_category(outcome(tie=True)) == ("jev_no_category", None)
    assert routing_category(outcome(fallback="timeout")) == ("jev_fallback", None)


def test_branch_filters_keep_every_deterministic_constraint():
    base = FilterSpec(brand="HP", max_price=Decimal("40000.00"), ram_gb=8)
    branch = routed_filters(base, Category.LAPTOP)
    assert branch == FilterSpec(
        brand="HP",
        max_price=Decimal("40000.00"),
        ram_gb=8,
        category=Category.LAPTOP,
    )
    with pytest.raises(ValueError):
        routed_filters(FilterSpec(category=Category.PHONE), Category.LAPTOP)


def test_an_empty_branch_fuses_exactly_like_v3():
    lexical = lexical_hits_for(FilterSpec(), 50)
    dense = dense_hits_for(FilterSpec(), 50)
    v3 = fuse_rrf(lexical, dense, 60, 50)
    v4 = fuse_routed(lexical, dense, [], [], 60, 50)
    assert [(c.product_id, c.rrf_score, c.lexical_rank, c.dense_rank) for c in v4] == [
        (c.product_id, c.rrf_score, c.lexical_rank, c.dense_rank) for c in v3
    ]


def test_branch_hits_add_rrf_terms_and_can_add_new_candidates():
    lexical = lexical_hits_for(FilterSpec(category=Category.LAPTOP), 50)
    dense = []
    branch = lexical_hits_for(FilterSpec(category=Category.PHONE), 50)
    fused = fuse_routed(lexical, dense, branch, [], 60, 50)
    phone = next(c for c in fused if c.product_id == "P1")
    assert (phone.lexical_rank, phone.routed_lexical_rank, phone.in_baseline_sources) == (
        None,
        1,
        False,
    )
    assert {c.product_id for c in fused} == {"L1", "L2", "L3", "L4", "P1", "P2"}


# ---- route: every non-routed outcome returns V3 ---------------------------------------------------


@pytest.mark.parametrize(
    ("jev", "status", "called"),
    [
        (None, "disabled", 0),
        (FakeJev(outcome(fallback="timeout")), "jev_fallback", 1),
        (FakeJev(outcome(fallback="auth_error")), "jev_fallback", 1),
        (FakeJev(outcome("none")), "jev_no_category", 1),
        (FakeJev(outcome(tie=True)), "jev_no_category", 1),
    ],
)
def test_v4_returns_the_v3_baseline_unless_routed(client_for, jev, status, called):
    client = client_for(jev)
    v4 = client.get(V4, params={"q": "gaming"})
    v3 = client.get(V3, params={"q": "gaming"})
    assert v4.status_code == v3.status_code == 200
    assert v3_view(v4.json()) == v3_body(v3.json())
    routing = v4.json()["jev_routing"]
    assert (routing["status"], routing["routing_used"], routing["routed_filters"]) == (
        status,
        False,
        [],
    )
    assert routing["experimental"] is True and routing["gate"] == "none_validated"
    assert routing["jev_called"] is (called == 1)
    assert len(jev.calls if jev else []) == called
    assert v4.json()["search_version"] == "v4_jev_routed_experimental"


def test_a_deterministic_category_never_calls_jev(client_for):
    jev = FakeJev(outcome("phone"))
    client = client_for(jev)
    v4 = client.post(V4, json={"query": "gaming laptop"})
    v3 = client.post(V3, json={"query": "gaming laptop"})
    assert v3_view(v4.json()) == v3_body(v3.json())
    assert v4.json()["jev_routing"]["status"] == "skipped_deterministic_category"
    assert jev.calls == []


def test_routing_adds_a_branch_without_filtering_the_combined_result(client_for, events):
    jev = FakeJev(outcome("phone"))
    client = client_for(jev)
    response = client.get(V4, params={"q": "gaming", "top_k": 8})
    assert response.status_code == 200
    body = response.json()
    routing = body["jev_routing"]
    assert routing["status"] == "routed" and routing["routing_used"] is True
    assert routing["routed_category"] == "phone"
    assert routing["routed_filters"] == [{"field": "category", "operator": "eq", "value": "phone"}]
    assert routing["raw_top_category_probability"] == 0.8
    assert (routing["model"], routing["jev_latency_ms"], routing["fallback_reason"]) == (
        "jev-1.13.0",
        12.0,
        None,
    )
    assert body["applied_filters"] == []  # Jev's category is never an applied (hard) filter
    categories = {result["category"] for result in body["results"]}
    assert "phone" in categories and categories - {"phone"}  # not a mandatory filter
    for result in body["results"]:
        in_branch = result["routed_lexical_rank"] is not None or result["routed_dense_rank"]
        assert bool(in_branch) == (result["category"] == "phone")
    assert jev.calls == ["gaming"]
    reads = source_reads(events)
    assert [e[1] for e in reads] == [
        FilterSpec(),
        FilterSpec(),
        FilterSpec(category=Category.PHONE),
        FilterSpec(category=Category.PHONE),
    ]
    assert body["reranker"]["status"] == "applied"
    assert body["latency_ms"]["jev_decision_ms"] is not None
    assert FAKE_KEY not in response.text


def test_the_branch_keeps_deterministic_numeric_constraints(client_for, events):
    client = client_for(FakeJev(outcome("laptop")))
    body = client.get(V4, params={"q": "gaming under 40k"}).json()
    assert body["applied_filters"] == [
        {"field": "max_price", "operator": "lte", "value": "40000.00"}
    ]
    assert body["jev_routing"]["routed_filters"] == [
        {"field": "category", "operator": "eq", "value": "laptop"},
        {"field": "max_price", "operator": "lte", "value": "40000.00"},
    ]
    assert all(float(result["price"]) <= 40000 for result in body["results"])


def test_a_malformed_provider_result_is_the_fixed_503(client_for, caplog):
    jev = FakeJev(DecisionResult(understanding=parse_normalized_query("gaming")))
    response = client_for(jev).get(V4, params={"q": "gaming"})
    assert (response.status_code, response.json()) == (503, {"detail": "search unavailable"})
    assert "jev_provider_error" in caplog.text


def test_an_unexpected_provider_exception_is_not_swallowed(client_for):
    client = client_for(FakeJev(error=ZeroDivisionError()))
    with pytest.raises(ZeroDivisionError):
        client.get(V4, params={"q": "gaming"})


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (JevConfigurationError(), "configuration_invalid"),
        (ImportError("typesafe_sdk"), "sdk_unavailable"),
        (routed_api.JevProviderClosed(), "provider_closed"),
    ],
)
def test_a_provider_that_cannot_be_obtained_returns_v3(
    client_for, make_settings, monkeypatch, error, reason, caplog
):
    def failing(config):
        raise error

    monkeypatch.setattr(routed_api, "JevDecisionProvider", failing)
    client = client_for(None, search_v4_jev_enabled=True, typesafe_api_key=FAKE_KEY)
    v4 = client.get(V4, params={"q": "gaming"})
    v3 = client.get(V3, params={"q": "gaming"})
    assert v4.status_code == 200
    assert v3_view(v4.json()) == v3_body(v3.json())
    routing = v4.json()["jev_routing"]
    assert (routing["status"], routing["fallback_reason"], routing["jev_called"]) == (
        "jev_fallback",
        reason,
        False,
    )
    assert FAKE_KEY not in caplog.text and "typesafe_sdk" not in caplog.text


def test_a_closed_holder_reports_provider_closed():
    holder = routed_api.LazyJevProvider(object())
    holder.close()
    with pytest.raises(routed_api.JevProviderClosed):
        holder.get()


# ---- opt-in, lazy construction and settings ------------------------------------------------------


def test_the_provider_is_built_lazily_by_the_first_query_that_needs_it(
    client_for, make_settings, monkeypatch
):
    built: list = []

    class RecordingProvider(FakeJev):
        def __init__(self, config) -> None:
            super().__init__(outcome("phone"))
            built.append(config)
            self.closed = False

        def close(self) -> None:
            self.closed = True

    monkeypatch.setattr(routed_api, "JevDecisionProvider", RecordingProvider)
    client = client_for(None, enter=False, search_v4_jev_enabled=True, typesafe_api_key=FAKE_KEY)
    with client:
        assert built == []  # start-up builds nothing and calls nothing
        client.get(V3, params={"q": "gaming"})
        client.get(V4, params={"q": "gaming laptop"})  # deterministic category: no Jev
        assert built == []
        assert client.get(V4, params={"q": "gaming"}).json()["jev_routing"]["status"] == "routed"
        client.get(V4, params={"q": "gaming"})
        assert len(built) == 1
        config = built[0]
        assert (config.model, config.max_retries, config.max_in_flight) == ("jev-1.13.0", 0, 1)
        holder = client.app.state.v4_jev_provider
    assert holder._closed is True


def test_v4_jev_settings_need_an_explicit_opt_in_a_key_and_the_pinned_model(make_settings):
    assert make_settings().search_v4_jev_enabled is False
    assert make_settings(typesafe_api_key=FAKE_KEY).v4_jev_config() is None
    with pytest.raises(ValueError):
        make_settings(search_v4_jev_enabled=True)
    with pytest.raises(ValueError):
        make_settings(search_v4_jev_enabled=True, typesafe_api_key=FAKE_KEY, jev_model="jev-1.14.0")
    with pytest.raises(ValueError):
        make_settings(search_v4_jev_enabled=1, typesafe_api_key=FAKE_KEY)
    settings = make_settings(
        search_v4_jev_enabled=True,
        typesafe_api_key=FAKE_KEY,
        jev_max_retries=2,
        jev_retry_budget_seconds=3.0,
        jev_attempt_timeout_seconds=1.0,
        jev_max_in_flight=8,
    )
    config = settings.v4_jev_config()
    assert (config.max_retries, config.max_in_flight, config.model) == (0, 1, "jev-1.13.0")
    assert settings.jev_config() is None  # the M11 switch is independent


def test_the_v3_route_is_unchanged_by_v4(client_for):
    body = client_for(FakeJev(outcome("phone"))).get(V3, params={"q": "gaming"}).json()
    assert body["search_version"] == "v3_reranked" and "jev_routing" not in body


def test_the_deterministic_provider_still_derives_v4_filters(client_for):
    # V4's filters come from the app's deterministic provider, never from Jev.
    client = client_for(FakeJev(outcome("phone")))
    body = client.get(V4, params={"q": "gaming"}).json()
    assert body["query_understanding"]["provider"] == "deterministic"
    assert isinstance(client.app.state.decision_provider, DeterministicDecisionProvider)
