from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import psycopg
import pytest

from app.rag.embedder import FakeEmbedder
from tests.api.client import build_app, serve
from tests.dataset import ACME, R1, vector_literal
from tests.security.canary import CANARY


@pytest.fixture
async def client(migrator: psycopg.Connection) -> AsyncIterator[httpx.AsyncClient]:
    async with serve(build_app()) as c:
        yield c


@pytest.fixture
def canary_r1(migrator: psycopg.Connection) -> None:
    """Make the canary string the ONLY content of restricted document R1.

    Its embedding is exactly what /search computes for the query CANARY, so for
    anyone allowed to read R1 it is the nearest possible chunk (distance 0).
    """
    [embedding] = FakeEmbedder().embed([CANARY])
    with migrator.transaction():
        migrator.execute("DELETE FROM chunks WHERE document_id = %s", (R1,))
        migrator.execute(
            "INSERT INTO chunks (document_id, tenant_id, chunk_index, content, embedding)"
            " VALUES (%s, %s, 0, %s, %s::vector)",
            (R1, ACME, CANARY, vector_literal(embedding)),
        )
