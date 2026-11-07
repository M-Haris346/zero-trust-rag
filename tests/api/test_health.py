"""Health endpoints, through the real app factory and lifespan."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import psycopg
import pytest

from app.config import Settings
from app.main import create_app
from tests.env import TEST_APP_DATABASE_URL


@pytest.fixture
async def client(migrator: psycopg.Connection) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(Settings(app_database_url=TEST_APP_DATABASE_URL))  # type: ignore[arg-type]
    # httpx's ASGITransport does not run lifespan events, so run them here.
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c,
    ):
        yield c


async def test_healthz(client: httpx.AsyncClient) -> None:
    response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert "x-request-id" in response.headers


async def test_readyz(client: httpx.AsyncClient) -> None:
    response = await client.get("/readyz")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}
