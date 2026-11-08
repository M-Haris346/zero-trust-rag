"""FastAPI application factory.

Run with:  uv run python -m scripts.serve
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import Settings, get_settings
from app.db import create_pool
from app.errors import register_error_handlers
from app.log import configure_logging, request_id_middleware
from app.rag.embedder import Embedder, build_embedder
from app.rag.llm import LLM, build_llm
from app.routes import auth, documents, health, query, search


def create_app(
    settings: Settings | None = None,
    embedder: Embedder | None = None,
    llm: LLM | None = None,
) -> FastAPI:
    """Build the app. Raises EmbedderUnavailableError if fastembed is selected but missing."""
    settings = settings or get_settings()
    embedder = embedder or build_embedder(settings)
    llm = llm or build_llm(settings)
    configure_logging()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        pool = create_pool(settings)
        await pool.open(wait=True, timeout=10.0)
        app.state.pool = pool
        try:
            yield
        finally:
            await pool.close()

    app = FastAPI(title="zero-trust-rag", lifespan=lifespan)
    app.state.settings = settings
    app.state.embedder = embedder
    app.state.llm = llm
    app.middleware("http")(request_id_middleware)
    register_error_handlers(app)

    app.include_router(health.router)
    app.include_router(documents.router)
    app.include_router(search.router)
    app.include_router(query.router)
    if settings.app_env == "dev":
        # Not merely disabled outside dev: the route does not exist (404).
        app.include_router(auth.router)
    return app
