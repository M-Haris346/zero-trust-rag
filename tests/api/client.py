"""Building and driving the real app in tests.

Settings are constructed explicitly (no .env file) so API tests are hermetic:
they always use the fake embedder, a known JWT secret and the test database.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

import httpx
from fastapi import FastAPI

from app.auth import Principal, issue_token
from app.config import Settings
from app.main import create_app
from app.rag.embedder import Embedder
from tests.env import TEST_APP_DATABASE_URL

# Test-only value; 64 chars so HS512 negative tests do not trip key-length warnings.
JWT_SECRET = "test-only-jwt-secret-" + "x" * 43


def make_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "app_env": "test",
        "app_database_url": TEST_APP_DATABASE_URL,
        "jwt_secret": JWT_SECRET,
        "embedder": "fake",
        "db_pool_max_size": 4,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def build_app(settings: Settings | None = None, embedder: Embedder | None = None) -> FastAPI:
    return create_app(settings or make_settings(), embedder)


@asynccontextmanager
async def serve(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    # httpx's ASGITransport does not run lifespan events, so run them here.
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c,
    ):
        yield c


def bearer(user_id: UUID, tenant_id: UUID, settings: Settings | None = None) -> dict[str, str]:
    principal = Principal(user_id=user_id, tenant_id=tenant_id)
    return {"Authorization": f"Bearer {issue_token(settings or make_settings(), principal)}"}
