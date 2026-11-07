"""app.db.tenant_transaction: context is visible inside, and gone afterwards.

The pool has max_size=1, so the "after" checks run on the *same* physical
connection that just served the request. That is the pooled-connection leak
that session-level SET would cause.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import psycopg
import pytest

from app.auth import Principal
from app.config import Settings
from app.db import Pool, create_pool, tenant_transaction
from tests.dataset import ACME, BOB, R2, T1
from tests.env import TEST_APP_DATABASE_URL


@pytest.fixture
async def pool(migrator: psycopg.Connection) -> AsyncIterator[Pool]:
    settings = Settings(
        app_database_url=TEST_APP_DATABASE_URL,  # type: ignore[arg-type]
        db_pool_min_size=1,
        db_pool_max_size=1,
    )
    p = create_pool(settings)
    await p.open(wait=True)
    try:
        yield p
    finally:
        await p.close()


BOB_AT_ACME = Principal(user_id=BOB, tenant_id=ACME)


async def _backend_pid(pool: Pool) -> int:
    async with pool.connection() as conn:
        row = await (await conn.execute("SELECT pg_backend_pid()")).fetchone()
        assert row is not None
        return int(row[0])


async def test_context_visible_inside_transaction(pool: Pool) -> None:
    async with tenant_transaction(pool, BOB_AT_ACME) as conn:
        cur = await conn.execute("SELECT current_setting('app.user_id')")
        assert await cur.fetchone() == (str(BOB),)
        cur = await conn.execute("SELECT id FROM documents")
        assert {r[0] for r in await cur.fetchall()} == {T1, R2}


async def test_context_gone_after_commit_on_same_connection(pool: Pool) -> None:
    pid_before = await _backend_pid(pool)
    async with tenant_transaction(pool, BOB_AT_ACME):
        pass

    async with pool.connection() as conn:
        cur = await conn.execute("SELECT pg_backend_pid(), current_setting('app.user_id', true)")
        assert await cur.fetchone() == (pid_before, "")
        cur = await conn.execute("SELECT count(*) FROM documents")
        assert await cur.fetchone() == (0,)


async def test_context_gone_after_rollback_on_same_connection(pool: Pool) -> None:
    with pytest.raises(RuntimeError):
        async with tenant_transaction(pool, BOB_AT_ACME):
            raise RuntimeError("request failed mid-way")

    async with pool.connection() as conn:
        cur = await conn.execute("SELECT count(*) FROM documents")
        assert await cur.fetchone() == (0,)
