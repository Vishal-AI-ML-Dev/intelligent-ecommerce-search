import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from ecommerce_search import __version__
from ecommerce_search.api.dense import router as dense_router
from ecommerce_search.api.filtered import router as filtered_router
from ecommerce_search.api.health import router as health_router
from ecommerce_search.api.hybrid import router as hybrid_router
from ecommerce_search.api.observability import (
    RequestIdMiddleware,
    configure_logging,
    install_access_log_redaction,
)
from ecommerce_search.api.reranked import router as reranked_router
from ecommerce_search.api.routed import LazyJevProvider
from ecommerce_search.api.routed import router as routed_router
from ecommerce_search.api.search import router as search_router
from ecommerce_search.config import Settings, get_settings
from ecommerce_search.db.engine import create_db_engine, create_session_factory
from ecommerce_search.decision import DeterministicDecisionProvider
from ecommerce_search.embeddings.sentence_transformers_provider import (
    SentenceTransformerEmbedder,
)
from ecommerce_search.reranking.cross_encoder_provider import CrossEncoderReranker

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging(settings.log_level)
        engine = create_db_engine(settings)
        app.state.settings = settings
        app.state.engine = engine
        app.state.session_factory = create_session_factory(engine)
        # Created unloaded: no model file is read and torch is not imported until the first
        # dense request (see SentenceTransformerEmbedder).
        models_dir = settings.resolved_models_dir()
        app.state.embedder = (
            None
            if models_dir is None
            else SentenceTransformerEmbedder(
                settings.embedding_spec(), models_dir, batch_size=settings.embedding_batch_size
            )
        )
        logger.info(
            "starting ecommerce-search %s env=%s db=%s:%s",
            __version__,
            settings.app_env,
            settings.postgres_host,
            settings.postgres_port,
        )
        try:
            yield
        finally:
            if app.state.v4_jev_provider is not None:
                app.state.v4_jev_provider.close()
            engine.dispose()
            logger.info("engine disposed")

    app = FastAPI(title="Intelligent E-commerce Search", version=__version__, lifespan=lifespan)
    # Milestone 24A: request IDs, one completion log per request, query-free access logs.
    app.add_middleware(RequestIdMiddleware)
    install_access_log_redaction()
    # One stateless, local provider per app: constructing it performs no I/O.
    app.state.decision_provider = DeterministicDecisionProvider()
    # One reranker per app, created unloaded: construction reads no file and imports no model
    # library; the snapshot is verified and loaded by the first request that scores candidates.
    reranker_models_dir = settings.resolved_models_dir()
    app.state.reranker = (
        None
        if reranker_models_dir is None
        else CrossEncoderReranker(
            settings.reranker_spec(), reranker_models_dir, batch_size=settings.reranker_batch_size
        )
    )
    # Experimental V4 only, and only with SEARCH_V4_JEV_ENABLED=true: the Jev provider is built by
    # the first request that needs it (no thread, SDK import or call at start-up).
    v4_config = settings.v4_jev_config()
    app.state.v4_jev_provider = None if v4_config is None else LazyJevProvider(v4_config)
    app.include_router(health_router)
    app.include_router(search_router)
    app.include_router(dense_router)
    app.include_router(hybrid_router)
    app.include_router(filtered_router)
    app.include_router(reranked_router)
    app.include_router(routed_router)
    if settings.frontend_dist_dir is not None:
        mount_frontend(app, settings.frontend_dist_dir)
    return app


# Served with the page only; the built bundle has no inline script or style and calls the API
# same-origin.
FRONTEND_PAGE_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; "
    "img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'self'; "
    "form-action 'self'; frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}


def mount_frontend(app: FastAPI, dist_dir: Path) -> None:
    """Serve the built Search UI (M25A) same-origin: `GET /` and the hashed `/assets/*` files.

    Registered after every API router and without a catch-all fallback, so no API path, `/docs`
    or `/openapi.json` is shadowed and unknown paths still return the API's 404.
    """
    index = dist_dir / "index.html"

    @app.get("/", include_in_schema=False)
    def frontend_index() -> FileResponse:
        return FileResponse(index, media_type="text/html", headers=FRONTEND_PAGE_HEADERS)

    app.mount("/assets", StaticFiles(directory=dist_dir / "assets"), name="frontend-assets")
