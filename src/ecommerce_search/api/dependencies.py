from collections.abc import Callable, Iterator

from fastapi import Request
from sqlalchemy.orm import Session

from ecommerce_search.assistant.eligibility import LazyEligibility
from ecommerce_search.config import Settings
from ecommerce_search.decision import DecisionProvider
from ecommerce_search.embeddings.provider import Embedder
from ecommerce_search.generation import GenerationProvider
from ecommerce_search.reranking.provider import RerankerProvider


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings


def get_db_session(request: Request) -> Iterator[Session]:
    session = request.app.state.session_factory()
    try:
        yield session
    finally:
        session.close()


def get_embedder(request: Request) -> Embedder | None:
    """The application's lazily loaded embedding provider (None when no models dir is known)."""
    return request.app.state.embedder


def get_decision_provider(request: Request) -> DecisionProvider:
    """The application's single decision provider (used by /search/hybrid, /search/filtered and
    /search/reranked)."""
    return request.app.state.decision_provider


def get_v4_jev_provider(request: Request) -> Callable[[], DecisionProvider] | None:
    """Experimental V4 only: a getter of the lazily built Jev provider, or None while
    `SEARCH_V4_JEV_ENABLED` is off. Getting it never calls Jev."""
    holder = request.app.state.v4_jev_provider
    return None if holder is None else holder.get


def get_reranker(request: Request) -> RerankerProvider | None:
    """The application's single, lazily loaded reranker (used only by /search/reranked; None
    when no models dir is known). Not loaded until a request needs to score candidates."""
    return request.app.state.reranker


def get_eligibility(request: Request) -> LazyEligibility | None:
    """The country-eligibility lookup (Milestone 16); None while generation is off."""
    return request.app.state.eligibility


def get_generation_provider(request: Request) -> GenerationProvider | None:
    """The assistant's optional generation provider (Milestone 16), or None while generation is
    off (`ASSISTANT_GENERATION_PROVIDER=none`, the default)."""
    return request.app.state.generation_provider
