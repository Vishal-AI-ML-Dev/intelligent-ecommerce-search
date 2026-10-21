"""M14-2 Jev listing-decision provider (`jl-1`), fully mocked: the real TypeSafe SDK runs against an
in-memory `httpx2.MockTransport`, so request building, response parsing, validation and error
mapping are exercised without any network access or real key."""

import json
import logging
import socket
import threading
import time
from collections.abc import Callable

import httpx2
import pytest
from listing_decision_support import LAPTOP, built, state_of
from pydantic import SecretStr

from ecommerce_search.decision import jev_provider as jev
from ecommerce_search.decision.jev_listing_provider import JevListingDecisionProvider
from ecommerce_search.decision.jev_provider import JevConfig
from ecommerce_search.decision.listing_models import (
    CATEGORY_LABELS,
    NUMERIC_VALUE_TYPES,
    SUBCATEGORY_LABELS,
    SupportLabel,
)
from ecommerce_search.decision.listing_provider import ListingDecisionProvider, decide_listing
from ecommerce_search.decision.listing_questions import model_inputs, questions
from ecommerce_search.normalization import rules as nr

pytestmark = pytest.mark.filterwarnings(
    "error::RuntimeWarning",
    "error::ResourceWarning",
    "error::pytest.PytestUnraisableExceptionWarning",
)

_LOOPBACK_HOSTS = {"127.0.0.1", "::1"}


def _refuse(*args, **kwargs):
    raise RuntimeError("network access is not allowed in listing decision unit tests")


@pytest.fixture(autouse=True)
def loopback_only(monkeypatch):
    """No name resolution or outbound connection. The provider's event loop needs a self-pipe,
    which on Windows is a loopback connection, so connect is allowed to 127.0.0.1/::1 only."""
    real_connect = socket.socket.connect

    def guarded_connect(self, address):
        host = address[0] if isinstance(address, tuple) else None
        if host not in _LOOPBACK_HOSTS:
            raise RuntimeError("network access is not allowed in listing decision unit tests")
        return real_connect(self, address)

    monkeypatch.setattr(socket, "create_connection", _refuse)
    monkeypatch.setattr(socket, "getaddrinfo", _refuse)
    monkeypatch.setattr(socket.socket, "connect", guarded_connect)


@pytest.fixture(autouse=True)
def restore_sdk_logger_level():
    sdk_logger = logging.getLogger("typesafe_sdk")
    level = sdk_logger.level
    yield
    sdk_logger.setLevel(level)


@pytest.fixture(autouse=True)
def no_leaked_threads():
    def loop_threads():
        return [t for t in threading.enumerate() if t.name == jev.LOOP_THREAD_NAME and t.is_alive()]

    assert loop_threads() == []
    before = set(threading.enumerate())
    yield
    assert loop_threads() == []
    assert [t.name for t in threading.enumerate() if t not in before] == []


KEY = "ts-test-key-not-real-7f3a9c"
MODEL = "jev-1.13.0"
SUPPORT_LABELS = [s.value for s in SupportLabel]
STATE = state_of()
ASKED = [q.question_id for q in STATE.attribute_questions]
NO_CLAIMS = {
    "seller_id": "seller-2",
    "seller_listing_ref": "ref-2",
    "title": "Nike Pegasus running shoes",
    "price": "₹5,000",
    "Occasion": "Sports-TARGET",
}


def _probabilities(labels, top: dict[str, float]) -> dict[str, float]:
    rest = [label for label in labels if label not in top]
    share = (1.0 - sum(top.values())) / len(rest) if rest else 0.0
    return {label: top.get(label, share) for label in labels}


def _answer(labels, choice: str, top: dict[str, float] | None = None, **overrides) -> dict:
    answer = {
        "type": "choice",
        "choice": choice,
        "confidence": 0.8125,
        "probabilities": _probabilities(labels, top or {choice: 0.85}),
    }
    answer.update(overrides)
    return answer


def _text_body(category=None, subcategory=None, **overrides) -> dict:
    body = {
        "model": MODEL,
        "answers": {
            "category": category or _answer(CATEGORY_LABELS, "laptop"),
            "subcategory": subcategory or _answer(SUBCATEGORY_LABELS, "everyday"),
        },
        "usage": {"input_tokens": 300, "output_tokens": 0},
    }
    body.update(overrides)
    return body


def _claims_body(answers: dict | None = None, **overrides) -> dict:
    default = {qid: _answer(SUPPORT_LABELS, "supported") for qid in ASKED}
    body = {
        "model": MODEL,
        "answers": default if answers is None else answers,
        "usage": {"input_tokens": 200, "output_tokens": 1},
    }
    body.update(overrides)
    return body


def _ok(body: dict, request_id: str) -> httpx2.Response:
    return httpx2.Response(200, json=body, headers={"x-typesafe-request-id": request_id})


class Server:
    """Answers the text call and the claims call with separate scripted replies (told apart by
    whether the state carries `claims`) and records every request body."""

    def __init__(
        self,
        text: httpx2.Response | Callable[[], httpx2.Response] | None = None,
        claims: httpx2.Response | Callable[[], httpx2.Response] | None = None,
    ) -> None:
        self.text = text or _ok(_text_body(), "req-text")
        self.claims = claims or _ok(_claims_body(), "req-claims")
        self.requests: list[httpx2.Request] = []

    def bodies(self) -> list[dict]:
        return [json.loads(r.content) for r in self.requests]

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        state = json.loads(request.content)["state"]
        reply = self.claims if "claims" in state else self.text
        return reply() if callable(reply) else reply


def _config(**overrides) -> JevConfig:
    values = {
        "api_key": SecretStr(KEY),
        "model": MODEL,
        "base_url": "https://api.typesafe.ai",
        "attempt_timeout_seconds": 1.25,
        "max_retries": 0,
        "retry_budget_seconds": 3.0,
        "deadline_seconds": 3.0,
        "max_in_flight": 8,
    }
    values.update(overrides)
    return JevConfig(**values)


def _decide(server: Server, state=STATE, **overrides):
    provider = JevListingDecisionProvider(
        _config(**overrides), transport=httpx2.MockTransport(server)
    )
    try:
        return provider.decide(state)
    finally:
        provider.close()


def _assert_fallback(outcome, reason: str) -> None:
    assert outcome.provider == "jev" and outcome.fallback_reason == reason
    assert (outcome.category, outcome.subcategory, outcome.attribute_support) == (None, None, ())
    assert outcome.state_sha256 == STATE.state_sha256() and outcome.model == MODEL


# --- request payloads ---------------------------------------------------------------------------


def test_text_call_sends_only_raw_title_and_description_with_the_two_text_questions() -> None:
    server = Server()
    _decide(server)
    assert len(server.requests) == 2
    request = server.requests[0]
    assert (request.method, str(request.url)) == ("POST", "https://api.typesafe.ai/v1/systemone")
    body = server.bodies()[0]
    assert set(body) == {"state", "model", "questions"} and body["model"] == MODEL
    assert body["state"] == {"title": LAPTOP["title"], "description": LAPTOP["description"]}
    assert body["state"] == model_inputs(STATE)["listing_text"]
    specs = {s.question_id: s for s in questions(STATE)}
    assert list(body["questions"]) == ["category", "subcategory"]
    for qid, question in body["questions"].items():
        assert question["type"] == "choice"
        assert question["instructions"] == specs[qid].instructions
        assert list(question["criteria"].items()) == list(specs[qid].criteria)
    assert list(body["questions"]["category"]["criteria"]) == list(CATEGORY_LABELS)
    assert list(body["questions"]["subcategory"]["criteria"]) == list(SUBCATEGORY_LABELS)


def test_claims_call_sends_only_the_asked_claims_with_one_support_question_each() -> None:
    server = Server()
    _decide(server)
    body = server.bodies()[1]
    state = body["state"]
    assert state == model_inputs(STATE)["listing_claims"]
    assert set(state) == {"title", "description", "claims"}
    assert list(state["claims"]) == ASKED == [f"attribute_support[{o}]" for o in (0, 3, 6, 10, 12)]
    assert state["claims"]["attribute_support[0]"] == {"field": "brand", "value": "HP"}
    assert all(set(claim) == {"field", "value"} for claim in state["claims"].values())
    assert list(body["questions"]) == ASKED
    for qid, question in body["questions"].items():
        assert question["type"] == "choice"
        assert list(question["criteria"]) == SUPPORT_LABELS
        assert repr(qid) in question["instructions"]
        assert LAPTOP["description"] not in question["instructions"]  # seller text is data only


def test_omitted_and_numeric_candidates_are_never_sent() -> None:
    server = Server()
    _decide(server)
    claims = server.bodies()[1]["state"]["claims"]
    omitted = {f"attribute_support[{o.ordinal}]" for o in STATE.omitted_candidates}
    assert omitted and not omitted & set(claims)
    assert not omitted & set(server.bodies()[1]["questions"])
    fields = {claim["field"] for claim in claims.values()}
    assert not any(nr.FIELD_VALUE_TYPES[f] in NUMERIC_VALUE_TYPES for f in fields)
    assert not {"ram_gb", "storage_gb", "price", "camera"} & fields


def test_hidden_target_metadata_and_identity_never_reach_any_request() -> None:
    server = Server()
    _decide(server)
    sent = b"".join(r.content for r in server.requests).decode("utf-8")
    for hidden in ("TARGET", "Occasion", "product_category_tree", "seller-1", "ref-1", "40,000"):
        assert hidden not in sent
    for body in server.bodies():
        state = body["state"]
        assert not {"category", "subcategory", "brand", "seller_id", "price"} & set(state)


def test_a_listing_with_no_asked_claim_makes_only_the_text_call() -> None:
    state = state_of(NO_CLAIMS)
    assert state.attribute_questions == ()
    server = Server(text=_ok(_text_body(category=_answer(CATEGORY_LABELS, "shoes")), "req-text"))
    outcome = _decide(server, state=state)
    assert len(server.requests) == 1 and "claims" not in server.bodies()[0]["state"]
    assert "TARGET" not in server.requests[0].content.decode("utf-8")
    assert outcome.fallback_reason is None and outcome.attribute_support == ()
    assert outcome.category.label == "shoes"
    assert (outcome.input_tokens, outcome.output_tokens, outcome.request_id) == (300, 0, "req-text")


# --- the validated outcome ----------------------------------------------------------------------


def test_valid_responses_give_bounded_proposals_with_no_gate_confidence() -> None:
    provider = JevListingDecisionProvider(_config(), transport=httpx2.MockTransport(Server()))
    try:
        assert isinstance(provider, ListingDecisionProvider)
        state, outcome = decide_listing(*built(), provider)
    finally:
        provider.close()
    assert state == STATE and outcome.state_sha256 == STATE.state_sha256()
    assert outcome.provider == "jev" and outcome.fallback_reason is None
    assert (outcome.model, outcome.response_model) == (MODEL, MODEL)
    assert outcome.request_id == "req-text,req-claims"
    assert (outcome.input_tokens, outcome.output_tokens) == (500, 1)
    assert outcome.latency_ms >= 0
    assert (outcome.category.label, outcome.subcategory.label) == ("laptop", "everyday")
    assert [p.ordinal for p in outcome.attribute_support] == [0, 3, 6, 10, 12]
    proposals = [outcome.category, outcome.subcategory, *outcome.attribute_support]
    assert all(p.gate_confidence is None for p in proposals)
    assert all(p.provider_confidence == 0.8125 for p in proposals)
    assert {p.label for p in outcome.attribute_support} == {"supported"}
    assert outcome.category.provider_probabilities == _probabilities(
        CATEGORY_LABELS, {"laptop": 0.85}
    )


def test_each_support_label_is_kept_and_not_stated_is_a_real_answer() -> None:
    choices = ["supported", "contradicted", "not_stated", "not_stated", "supported"]
    answers = {qid: _answer(SUPPORT_LABELS, c) for qid, c in zip(ASKED, choices, strict=True)}
    outcome = _decide(Server(claims=_ok(_claims_body(answers), "req-claims")))
    assert [p.label for p in outcome.attribute_support] == choices
    assert [p.raw_choice for p in outcome.attribute_support] == choices


def test_output_order_follows_the_state_whatever_order_the_response_uses() -> None:
    reversed_answers = {
        qid: _answer(SUPPORT_LABELS, "not_stated" if i % 2 else "supported")
        for i, qid in reversed(list(enumerate(ASKED)))
    }
    text = _text_body()
    reordered_text = {**text, "answers": dict(reversed(list(text["answers"].items())))}
    first = _decide(Server(claims=_ok(_claims_body(reversed_answers), "c")))
    second = _decide(
        Server(
            text=_ok(reordered_text, "req-text"),
            claims=_ok(_claims_body(dict(reversed(list(reversed_answers.items())))), "c"),
        )
    )
    assert [p.ordinal for p in first.attribute_support] == [0, 3, 6, 10, 12]
    assert first.model_dump(exclude={"latency_ms"}) == second.model_dump(exclude={"latency_ms"})


# --- ties and `none` never force a label ---------------------------------------------------------


def test_explicit_none_answers_propose_no_label() -> None:
    body = _text_body(
        category=_answer(CATEGORY_LABELS, "none"), subcategory=_answer(SUBCATEGORY_LABELS, "none")
    )
    outcome = _decide(Server(text=_ok(body, "t")))
    assert outcome.fallback_reason is None
    assert (outcome.category.label, outcome.category.raw_choice) == (None, "none")
    assert (outcome.subcategory.label, outcome.subcategory.top_labels) == (None, ("none",))


@pytest.mark.parametrize(
    ("gap", "expected_top", "expected_label"),
    [
        (0.0, ("laptop", "phone"), None),
        (5e-10, ("laptop", "phone"), None),
        (2e-9, ("laptop",), "laptop"),
    ],
    ids=["equal", "within-m11-tie-tolerance", "beyond-m11-tie-tolerance"],
)
def test_category_ties_use_the_m11_tie_tolerance(gap, expected_top, expected_label) -> None:
    assert jev.TOP_TIE_TOLERANCE == 1e-9
    probabilities = {"laptop": 0.45, "phone": 0.45 - gap, "shoes": 0.05, "headphones": 0.05}
    probabilities["none"] = 1.0 - sum(probabilities.values())
    category = _answer(CATEGORY_LABELS, "laptop", probabilities=probabilities)
    outcome = _decide(Server(text=_ok(_text_body(category=category), "t")))
    assert outcome.fallback_reason is None
    assert (outcome.category.top_labels, outcome.category.label) == (expected_top, expected_label)


def test_subcategory_and_support_ties_propose_no_label() -> None:
    subcategory = _answer(SUBCATEGORY_LABELS, "ultrabook", {"business": 0.4, "ultrabook": 0.4})
    tie = _answer(SUPPORT_LABELS, "not_stated", {"supported": 0.45, "not_stated": 0.45})
    answers = {qid: _answer(SUPPORT_LABELS, "supported") for qid in ASKED} | {ASKED[2]: tie}
    outcome = _decide(
        Server(
            text=_ok(_text_body(subcategory=subcategory), "t"),
            claims=_ok(_claims_body(answers), "c"),
        )
    )
    assert outcome.fallback_reason is None
    assert outcome.subcategory.top_labels == ("business", "ultrabook")
    assert outcome.subcategory.label is None
    support = outcome.attribute_support[2]
    assert (support.ordinal, support.label) == (6, None)
    assert support.top_labels == ("supported", "not_stated")


# --- invalid answers are rejected as a whole ----------------------------------------------------


def _support(**changes) -> dict:
    return _claims_body({qid: _answer(SUPPORT_LABELS, "supported") for qid in ASKED} | changes)


_INVALID_TEXT = {
    "category-out-of-set": _text_body(
        category=_answer(CATEGORY_LABELS, "tablet", {"laptop": 0.85})
    ),
    "category-extra-label": _text_body(
        category=_answer([*CATEGORY_LABELS, "tablet"], "laptop", {"laptop": 0.85})
    ),
    "subcategory-out-of-set": _text_body(
        subcategory=_answer(SUBCATEGORY_LABELS, "gaming", {"everyday": 0.85})
    ),
    "subcategory-missing-label": _text_body(
        subcategory=_answer(
            [lbl for lbl in SUBCATEGORY_LABELS if lbl != "none"], "everyday", {"everyday": 0.85}
        )
    ),
    "choice-not-top": _text_body(category=_answer(CATEGORY_LABELS, "phone", {"laptop": 0.85})),
    "missing-subcategory": {
        **_text_body(),
        "answers": {"category": _answer(CATEGORY_LABELS, "laptop")},
    },
    "claim-answered-in-text-call": {
        **_text_body(),
        "answers": {**_text_body()["answers"], ASKED[0]: _answer(SUPPORT_LABELS, "supported")},
    },
    "negative-tokens": _text_body(usage={"input_tokens": -1, "output_tokens": 0}),
    "bool-tokens": _text_body(usage={"input_tokens": True, "output_tokens": 0}),
}


@pytest.mark.parametrize("body", list(_INVALID_TEXT.values()), ids=list(_INVALID_TEXT))
def test_invalid_text_answers_fall_back_without_any_proposal(body) -> None:
    server = Server(text=_ok(body, "req-text"))
    outcome = _decide(server)
    _assert_fallback(outcome, "invalid_response")
    assert outcome.request_id == "req-text"
    assert len(server.requests) == 1  # the claims call is never made after an invalid text answer


_UNKNOWN = "attribute_support[99]"
_OMITTED = f"attribute_support[{STATE.omitted_candidates[0].ordinal}]"
_INVALID_CLAIMS = {
    "support-label-out-of-set": _support(
        **{ASKED[0]: _answer(SUPPORT_LABELS, "true", {"supported": 0.85})}
    ),
    "support-extra-label": _support(
        **{ASKED[0]: _answer([*SUPPORT_LABELS, "unknown"], "supported", {"supported": 0.85})}
    ),
    "support-missing-label": _support(
        **{ASKED[0]: _answer(["supported", "contradicted"], "supported", {"supported": 0.85})}
    ),
    "omitted-claim-answered": _support(**{_OMITTED: _answer(SUPPORT_LABELS, "supported")}),
    "unknown-ordinal-answered": _support(**{_UNKNOWN: _answer(SUPPORT_LABELS, "supported")}),
    "asked-claim-missing": _claims_body(
        {qid: _answer(SUPPORT_LABELS, "supported") for qid in ASKED[1:]}
    ),
    "unasked-replaces-asked": _claims_body(
        {qid: _answer(SUPPORT_LABELS, "supported") for qid in [*ASKED[1:], _OMITTED]}
    ),
    "category-answered-in-claims-call": _support(category=_answer(CATEGORY_LABELS, "laptop")),
    "noul-answer": _support(**{ASKED[0]: {"type": "noul", "noul": 0.9}}),
}


@pytest.mark.parametrize("body", list(_INVALID_CLAIMS.values()), ids=list(_INVALID_CLAIMS))
def test_invalid_claim_answers_fall_back_without_any_proposal(body) -> None:
    assert STATE.omitted_candidates[0].ordinal not in STATE.question_ordinals()
    server = Server(claims=_ok(body, "req-claims"))
    outcome = _decide(server)
    _assert_fallback(outcome, "invalid_response")  # the valid text answers are discarded too
    assert outcome.request_id == "req-text,req-claims"
    assert len(server.requests) == 2


@pytest.mark.parametrize("which", ["text", "claims"])
def test_a_different_answering_model_is_a_model_mismatch(which) -> None:
    body = _text_body(model="jev-1.14.0") if which == "text" else _claims_body(model="jev-1.14.0")
    outcome = _decide(Server(**{which: _ok(body, "r")}))
    _assert_fallback(outcome, "model_mismatch")


# --- provider failures surface as fallback outcomes ----------------------------------------------


@pytest.mark.parametrize(
    ("status", "reason"),
    [(500, "server_error"), (429, "rate_limited"), (401, "auth_error"), (400, "request_rejected")],
)
def test_a_failed_text_call_falls_back_and_skips_the_claims_call(status, reason) -> None:
    server = Server(text=httpx2.Response(status, json={"error": "x"}))
    outcome = _decide(server)
    _assert_fallback(outcome, reason)
    assert len(server.requests) == 1


def test_a_failed_claims_call_discards_the_text_answers() -> None:
    server = Server(claims=httpx2.Response(503, json={"error": "x"}))
    outcome = _decide(server)
    _assert_fallback(outcome, "server_error")
    assert outcome.request_id is not None and outcome.request_id.startswith("req-text")
    assert len(server.requests) == 2


def test_transport_errors_fall_back() -> None:
    def broken() -> httpx2.Response:
        raise httpx2.ConnectError("refused")

    _assert_fallback(_decide(Server(text=broken)), "transport_error")


@pytest.mark.parametrize("content", [b"{not json", b"", b"[]"], ids=["malformed", "empty", "array"])
def test_unparseable_bodies_fall_back_as_invalid_responses(content) -> None:
    _assert_fallback(
        _decide(Server(text=httpx2.Response(200, content=content))), "invalid_response"
    )


def test_a_closed_provider_sends_nothing_and_falls_back() -> None:
    server = Server()
    provider = JevListingDecisionProvider(_config(), transport=httpx2.MockTransport(server))
    provider.close()
    provider.close()  # idempotent
    _assert_fallback(provider.decide(STATE), "provider_closed")
    assert server.requests == []


def test_outcomes_never_put_the_key_or_listing_text_in_logs_or_reprs(caplog) -> None:
    caplog.set_level(logging.DEBUG)
    outcomes = [
        _decide(Server()),
        _decide(Server(text=httpx2.Response(401, json={"error": KEY}))),
    ]
    text = caplog.text + "".join(repr(o) for o in outcomes)
    assert "jev_listing_decision" in caplog.text
    for secret in (KEY, LAPTOP["title"], "Ignore all previous instructions", "Ryzen"):
        assert secret not in text


def test_the_deadline_bounds_both_calls_together() -> None:
    # Each call alone fits the deadline; the two together do not, so the claims call is cut off.
    def slow(response: httpx2.Response) -> Callable[[], httpx2.Response]:
        def reply() -> httpx2.Response:
            time.sleep(0.35)
            return response

        return reply

    server = Server(text=slow(_ok(_text_body(), "req-text")), claims=slow(_ok(_claims_body(), "c")))
    started = time.perf_counter()
    outcome = _decide(server, deadline_seconds=0.5)
    assert time.perf_counter() - started < 2.0
    _assert_fallback(outcome, "deadline_exceeded")
    assert outcome.request_id == "req-text" and len(server.requests) == 2
