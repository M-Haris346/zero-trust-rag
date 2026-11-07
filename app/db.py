"""Connection pool and the per-request tenant transaction.

Every query that touches tenant data must run inside `tenant_transaction`.
It is the only place request identity is handed to Postgres, where the RLS
policies in db/migrations/004_rls.sql read it back.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from psycopg import AsyncConnection
from psycopg.rows import TupleRow
from psycopg_pool import AsyncConnectionPool

from app.auth import Principal
from app.config import Settings

Pool = AsyncConnectionPool[AsyncConnection[TupleRow]]


def create_pool(settings: Settings) -> Pool:
    return AsyncConnectionPool(
        conninfo=settings.app_database_url.get_secret_value(),
        min_size=settings.db_pool_min_size,
        max_size=settings.db_pool_max_size,
        # autocommit: nothing runs in an implicit transaction. Transactions
        # exist only where the code opens one with conn.transaction().
        # connect_timeout: fail with a clear error instead of hanging forever
        # when the host is wrong or unreachable.
        kwargs={"autocommit": True, "connect_timeout": 5},
        open=False,
    )


@asynccontextmanager
async def tenant_transaction(
    pool: Pool, principal: Principal
) -> AsyncIterator[AsyncConnection[TupleRow]]:
    """Yield a connection inside a transaction scoped to `principal`.

    set_config(..., true) is transaction-local: Postgres discards the values at
    COMMIT or ROLLBACK. A pooled connection therefore cannot carry one request's
    identity into the next request. Session-level SET would leak.
    """
    async with pool.connection() as conn, conn.transaction():
        await conn.execute(
            "SELECT set_config('app.user_id', %s, true), set_config('app.tenant_id', %s, true)",
            (str(principal.user_id), str(principal.tenant_id)),
        )
        yield conn
