"""FastAPI application factory.

Run with:  uv run uvicorn --factory app.main:create_app
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import Settings, get_settings
from app.db import create_pool
from app.log import configure_logging, request_id_middleware
from app.routes import health


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
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
    app.middleware("http")(request_id_middleware)
    app.include_router(health.router)
    return app
