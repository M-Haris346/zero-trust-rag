"""Database errors become clean responses; raw Postgres text never leaks."""

from __future__ import annotations

import psycopg
import pytest
from psycopg import errors

from tests.api.client import build_app, serve

SECRET_DETAIL = 'Key (tenant_id)=(1234) violates "secret_constraint_name" on table chunks'


@pytest.mark.parametrize(
    ("exc", "status", "detail"),
    [
        (errors.CheckViolation(SECRET_DETAIL), 422, "request conflicts with stored data"),
        (errors.ForeignKeyViolation(SECRET_DETAIL), 422, "request conflicts with stored data"),
        (errors.UniqueViolation(SECRET_DETAIL), 422, "request conflicts with stored data"),
        (errors.InsufficientPrivilege(SECRET_DETAIL), 403, "forbidden"),
        (errors.DataException(SECRET_DETAIL), 500, "internal error"),
        (psycopg.OperationalError(SECRET_DETAIL), 500, "internal error"),
    ],
    ids=["check", "fk", "unique", "privilege", "data", "operational"],
)
async def test_db_error_is_mapped_without_leaking_text(
    migrator: psycopg.Connection, exc: Exception, status: int, detail: str
) -> None:
    app = build_app()

    @app.get("/_raise")
    async def _raise() -> None:
        raise exc

    async with serve(app) as client:
        response = await client.get("/_raise")
    assert response.status_code == status
    assert response.json() == {"detail": detail}
    for fragment in ("Key (", "violates", "secret_constraint_name", "chunks", "1234"):
        assert fragment not in response.text
