"""app.rag.retrieval.retrieve against the skewed HNSW corpus (ADR 0004).

test_hnsw_rls.py shows raw SQL starving without iterative scan. These tests
show the application's own retrieve() applies the fix: forced onto the HNSW
index, it still returns k readable chunks, ordered by distance, and its
SET LOCAL does not outlive the transaction.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import psycopg
import pytest

from app.auth import Principal
from app.db import Pool, create_pool, tenant_transaction
from app.rag.retrieval import retrieve
from tests.api.client import make_settings
from tests.corpus import QUERY_EMBEDDING, K
from tests.dataset import ACME, BOB, READABLE

BOB_AT_ACME = Principal(user_id=BOB, tenant_id=ACME)


@pytest.fixture
async def pool(migrator: psycopg.Connection) -> AsyncIterator[Pool]:
    p = create_pool(make_settings(db_pool_min_size=1, db_pool_max_size=1))
    await p.open(wait=True)
    try:
        yield p
    finally:
        await p.close()


@pytest.mark.usefixtures("skewed_corpus")
async def test_retrieve_returns_k_readable_chunks_on_hnsw(pool: Pool) -> None:
    async with tenant_transaction(pool, BOB_AT_ACME) as conn:
        await conn.execute("SET LOCAL enable_seqscan = off")  # force the HNSW index
        await conn.execute("SET LOCAL hnsw.ef_search = 40")
        hits = await retrieve(conn, QUERY_EMBEDDING, K)

    assert len(hits) == K
    assert {h.document_id for h in hits} <= READABLE[(BOB, ACME)]
    distances = [h.distance for h in hits]
    assert distances == sorted(distances)


async def test_iterative_scan_setting_does_not_outlive_transaction(pool: Pool) -> None:
    async with tenant_transaction(pool, BOB_AT_ACME) as conn:
        await retrieve(conn, QUERY_EMBEDDING, 1)
        cur = await conn.execute("SHOW hnsw.iterative_scan")
        assert await cur.fetchone() == ("relaxed_order",)

    async with pool.connection() as conn:  # same physical connection (max_size=1)
        cur = await conn.execute("SHOW hnsw.iterative_scan")
        assert await cur.fetchone() == ("off",)
