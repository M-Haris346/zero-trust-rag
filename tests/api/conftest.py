from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import psycopg
import pytest

from tests.api.client import build_app, serve


@pytest.fixture
async def client(migrator: psycopg.Connection) -> AsyncIterator[httpx.AsyncClient]:
    """The real app (fake embedder, test DB) with the canonical dataset loaded."""
    async with serve(build_app()) as c:
        yield c
