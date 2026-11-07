"""No identity in the transaction -> no rows. Never an accidental default."""

from __future__ import annotations

import psycopg
import pytest

from tests.dataset import ACME, ALL_TABLES, BOB
from tests.helpers import as_principal, count


@pytest.mark.parametrize("table", ALL_TABLES)
def test_no_context_sees_zero_rows(app_conn: psycopg.Connection, table: str) -> None:
    # Denied: app_user, no set_config at all.
    assert count(app_conn, f"SELECT count(*) FROM {table}") == 0


@pytest.mark.parametrize("table", ALL_TABLES)
def test_empty_string_context_sees_zero_rows(app_conn: psycopg.Connection, table: str) -> None:
    # Denied: '' is what current_setting() returns after a local setting expires.
    with as_principal(app_conn, "", "") as conn:
        assert count(conn, f"SELECT count(*) FROM {table}") == 0


@pytest.mark.parametrize("table", ["tenants", "memberships", "documents", "chunks"])
def test_user_without_tenant_sees_zero_rows(app_conn: psycopg.Connection, table: str) -> None:
    # Denied: a user id alone is not enough to see any tenant data.
    with as_principal(app_conn, BOB, "") as conn:
        assert count(conn, f"SELECT count(*) FROM {table}") == 0


@pytest.mark.parametrize("table", ["tenants", "users", "memberships", "documents", "chunks"])
def test_tenant_without_user_sees_zero_rows(app_conn: psycopg.Connection, table: str) -> None:
    # Denied: a tenant id alone is not enough either.
    with as_principal(app_conn, "", ACME) as conn:
        assert count(conn, f"SELECT count(*) FROM {table}") == 0


def test_malformed_context_errors_instead_of_matching(app_conn: psycopg.Connection) -> None:
    # Denied loudly: garbage identity is a hard error, not a wildcard.
    with (
        pytest.raises(psycopg.errors.InvalidTextRepresentation),
        as_principal(app_conn, "not-a-uuid", ACME) as conn,
    ):
        conn.execute("SELECT count(*) FROM documents")


def test_valid_context_sees_rows(app_conn: psycopg.Connection) -> None:
    # Allowed: the control case, so the denials above are meaningful.
    with as_principal(app_conn, BOB, ACME) as conn:
        assert count(conn, "SELECT count(*) FROM documents") > 0
