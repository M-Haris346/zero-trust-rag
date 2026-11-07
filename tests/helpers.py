"""Helpers shared by the DB tests."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from uuid import UUID

import psycopg


@contextmanager
def as_principal(
    conn: psycopg.Connection, user_id: UUID | str, tenant_id: UUID | str
) -> Iterator[psycopg.Connection]:
    """Same mechanism as app.db.tenant_transaction, for sync test connections.

    Put `pytest.raises` *outside* this block so a failing statement rolls the
    transaction back cleanly.
    """
    with conn.transaction():
        conn.execute(
            "SELECT set_config('app.user_id', %s, true), set_config('app.tenant_id', %s, true)",
            (str(user_id), str(tenant_id)),
        )
        yield conn


def ids(conn: psycopg.Connection, query: Any, params: Any = None) -> set[UUID]:
    """Run a query whose first column is a uuid and return the set of values."""
    return {row[0] for row in conn.execute(query, params)}


def count(conn: psycopg.Connection, query: Any, params: Any = None) -> int:
    row = conn.execute(query, params).fetchone()
    assert row is not None
    return int(row[0])
