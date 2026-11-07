"""document_grants: who can grant, who can see grants, and schema guarantees."""

from __future__ import annotations

import psycopg
import pytest
from psycopg import errors

from tests.dataset import ACME, ALICE, BOB, CAROL, DAVE, R1, R2
from tests.helpers import as_principal, count, ids

GRANT = "INSERT INTO document_grants (document_id, tenant_id, user_id) VALUES (%s, %s, %s)"


def _carol_can_read_r2(app_conn: psycopg.Connection) -> bool:
    with as_principal(app_conn, CAROL, ACME) as conn:
        return ids(conn, "SELECT id FROM documents WHERE id = %s", (R2,)) == {R2}


def test_admin_grants_access(app_conn: psycopg.Connection) -> None:
    assert not _carol_can_read_r2(app_conn)
    with as_principal(app_conn, ALICE, ACME) as conn:
        conn.execute(GRANT, (R2, ACME, CAROL))
    assert _carol_can_read_r2(app_conn)


def test_creator_grants_and_revokes_access(app_conn: psycopg.Connection) -> None:
    with as_principal(app_conn, BOB, ACME) as conn:
        conn.execute(GRANT, (R2, ACME, CAROL))
    assert _carol_can_read_r2(app_conn)

    with as_principal(app_conn, BOB, ACME) as conn:
        deleted = conn.execute(
            "DELETE FROM document_grants WHERE document_id = %s AND user_id = %s", (R2, CAROL)
        ).rowcount
        assert deleted == 1
    assert not _carol_can_read_r2(app_conn)


def test_grantee_cannot_regrant(app_conn: psycopg.Connection) -> None:
    # Carol can read R1 via her grant but does not manage it.
    with pytest.raises(errors.InsufficientPrivilege), as_principal(app_conn, CAROL, ACME) as conn:
        conn.execute(GRANT, (R1, ACME, BOB))


def test_cannot_grant_to_member_of_another_tenant(app_conn: psycopg.Connection) -> None:
    # Policy allows alice (admin); the composite FK to memberships stops it.
    with pytest.raises(errors.ForeignKeyViolation), as_principal(app_conn, ALICE, ACME) as conn:
        conn.execute(GRANT, (R2, ACME, DAVE))


def test_grantee_sees_own_grant(app_conn: psycopg.Connection) -> None:
    with as_principal(app_conn, CAROL, ACME) as conn:
        assert count(conn, "SELECT count(*) FROM document_grants") == 1


def test_unrelated_member_sees_no_grants(app_conn: psycopg.Connection) -> None:
    # Bob neither holds nor manages the R1 grant.
    with as_principal(app_conn, BOB, ACME) as conn:
        assert count(conn, "SELECT count(*) FROM document_grants") == 0


def test_admin_sees_all_grants_in_tenant(app_conn: psycopg.Connection) -> None:
    with as_principal(app_conn, ALICE, ACME) as conn:
        assert count(conn, "SELECT count(*) FROM document_grants") == 1


def test_removing_membership_removes_grants_and_access(
    app_conn: psycopg.Connection, migrator: psycopg.Connection
) -> None:
    migrator.execute("DELETE FROM memberships WHERE tenant_id = %s AND user_id = %s", (ACME, CAROL))
    assert count(migrator, "SELECT count(*) FROM document_grants WHERE user_id = %s", (CAROL,)) == 0
    with as_principal(app_conn, CAROL, ACME) as conn:
        assert ids(conn, "SELECT id FROM documents") == set()
