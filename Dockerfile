# M26A: the built Search UI (M25A, ADR-008) from the pinned lockfile; same Node line as CI.
FROM node:24.22.1-bookworm-slim AS frontend

WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/index.html frontend/tsconfig.json frontend/vite.config.ts ./
COPY frontend/src ./src
RUN npm run build


FROM python:3.12-slim-bookworm AS builder

COPY --from=ghcr.io/astral-sh/uv:0.11.12 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable


FROM python:3.12-slim-bookworm

ARG EMBEDDING_SNAPSHOT=models--sentence-transformers--all-MiniLM-L6-v2/snapshots/1110a243fdf4706b3f48f1d95db1a4f5529b4d41
ARG RERANKER_SNAPSHOT=models--cross-encoder--ms-marco-MiniLM-L6-v2/snapshots/233902d25c440f23af6f7d6e94d2946bac0bee0a

RUN useradd --create-home --uid 1000 app
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY alembic.ini ./
COPY migrations ./migrations
COPY --from=frontend /frontend/dist ./frontend/dist
# M26A: only the two pinned registry snapshots (Apache-2.0 model cards) and their manifests, owned
# by root and read-only for the app user. `model-verify` re-checks them offline inside the image.
COPY models/manifests ./models/manifests
COPY models/hub/${EMBEDDING_SNAPSHOT} ./models/hub/${EMBEDDING_SNAPSHOT}
COPY models/hub/${RERANKER_SNAPSHOT} ./models/hub/${RERANKER_SNAPSHOT}

# The loaders also force offline mode before importing the model libraries; setting it here keeps
# any other import from reaching the hub. A deployment may override the two directories.
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 \
    EMBEDDING_MODELS_DIR=/app/models FRONTEND_DIST_DIR=/app/frontend/dist \
    HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1
USER app
EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/health', timeout=4)"]

CMD ["uvicorn", "ecommerce_search.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
