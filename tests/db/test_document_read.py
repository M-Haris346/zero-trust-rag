"""The document read rule, checked for every principal in the dataset.

Every query here is `SELECT ... FROM documents` with no WHERE clause:
the application "forgot" the filter, and RLS still decides.
"""

from __future__ import annotations

from uuid import UUID

import psycopg
import pytest

from tests.dataset import ACME, ALICE, BOB, CAROL, DAVE, EVE, G1, GLOBEX, R1, R2, READABLE, T1
from tests.helpers import as_principal, ids


@pytest.mark.parametrize(("user", "tenant"), list(READABLE))
def test_unfiltered_select_returns_exactly_the_readable_set(
    app_conn: psycopg.Connection, user: UUID, tenant: UUID
) -> None:
    with as_principal(app_conn, user, tenant) as conn:
        assert ids(conn, "SELECT id FROM documents") == READABLE[(user, tenant)]


def test_member_reads_tenant_wide_document(app_conn: psycopg.Connection) -> None:
    with as_principal(app_conn, CAROL, ACME) as conn:
        assert T1 in ids(conn, "SELECT id FROM documents")


def test_member_cannot_read_restricted_document_without_grant(
    app_conn: psycopg.Connection,
) -> None:
    with as_principal(app_conn, BOB, ACME) as conn:
        assert ids(conn, "SELECT id FROM documents WHERE id = %s", (R1,)) == set()


def test_grant_allows_restricted_document(app_conn: psycopg.Connection) -> None:
    with as_principal(app_conn, CAROL, ACME) as conn:
        assert ids(conn, "SELECT id FROM documents WHERE id = %s", (R1,)) == {R1}


def test_creator_reads_own_restricted_document(app_conn: psycopg.Connection) -> None:
    with as_principal(app_conn, BOB, ACME) as conn:
        assert ids(conn, "SELECT id FROM documents WHERE id = %s", (R2,)) == {R2}


def test_non_creator_cannot_read_restricted_document(app_conn: psycopg.Connection) -> None:
    with as_principal(app_conn, CAROL, ACME) as conn:
        assert ids(conn, "SELECT id FROM documents WHERE id = %s", (R2,)) == set()


def test_admin_reads_all_restricted_documents(app_conn: psycopg.Connection) -> None:
    with as_principal(app_conn, ALICE, ACME) as conn:
        assert ids(conn, "SELECT id FROM documents WHERE visibility = 'restricted'") == {R1, R2}


def test_other_tenant_documents_invisible_even_by_id(app_conn: psycopg.Connection) -> None:
    with as_principal(app_conn, DAVE, GLOBEX) as conn:
        assert ids(conn, "SELECT id FROM documents WHERE id = ANY(%s)", ([T1, R1, R2],)) == set()


def test_admin_of_one_tenant_has_no_power_in_another(app_conn: psycopg.Connection) -> None:
    with as_principal(app_conn, ALICE, GLOBEX) as conn:
        assert ids(conn, "SELECT id FROM documents") == set()


def test_forged_tenant_claim_sees_nothing(app_conn: psycopg.Connection) -> None:
    # Bob is real, Globex is real, but Bob is not a Globex member. Even a token
    # that claims Globex (bug or forgery) gets nothing, not even G1.
    with as_principal(app_conn, BOB, GLOBEX) as conn:
        assert ids(conn, "SELECT id FROM documents") == set()


def test_user_without_membership_sees_nothing(app_conn: psycopg.Connection) -> None:
    with as_principal(app_conn, EVE, ACME) as conn:
        assert ids(conn, "SELECT id FROM documents") == set()


def test_correct_tenant_member_sees_own_tenant(app_conn: psycopg.Connection) -> None:
    with as_principal(app_conn, DAVE, GLOBEX) as conn:
        assert ids(conn, "SELECT id FROM documents") == {G1}
