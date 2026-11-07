"""Write rules: members create as themselves; only creator/admin delete or add chunks."""

from __future__ import annotations

import random
from uuid import UUID

import psycopg
import pytest
from psycopg import errors

from tests.dataset import (
    ACME,
    ALICE,
    BOB,
    CAROL,
    EVE,
    G1,
    GLOBEX,
    R1,
    R2,
    T1,
    random_unit_vector,
    vector_literal,
)
from tests.helpers import as_principal, count, ids

# "new row violates row-level security policy" and "permission denied" are both 42501.
RLS_DENIED = errors.InsufficientPrivilege

INSERT_DOC = (
    "INSERT INTO documents (tenant_id, title, visibility, created_by)"
    " VALUES (%s, %s, 'tenant', %s) RETURNING id"
)
INSERT_CHUNK = (
    "INSERT INTO chunks (document_id, tenant_id, chunk_index, content, embedding)"
    " VALUES (%s, %s, %s, 'new chunk', %s::vector)"
)


def _vec() -> str:
    return vector_literal(random_unit_vector(random.Random(7)))


# --- document insert ---------------------------------------------------------


def test_member_inserts_document_as_self(app_conn: psycopg.Connection) -> None:
    with as_principal(app_conn, BOB, ACME) as conn:
        row = conn.execute(INSERT_DOC, (ACME, "bob's new doc", BOB)).fetchone()
        assert row is not None
        # RETURNING works because the creator can read what they just wrote.
        assert isinstance(row[0], UUID)


def test_insert_into_other_tenant_denied(app_conn: psycopg.Connection) -> None:
    with pytest.raises(RLS_DENIED), as_principal(app_conn, BOB, ACME) as conn:
        conn.execute(INSERT_DOC, (GLOBEX, "smuggled", BOB))


def test_insert_impersonating_another_creator_denied(app_conn: psycopg.Connection) -> None:
    with pytest.raises(RLS_DENIED), as_principal(app_conn, BOB, ACME) as conn:
        conn.execute(INSERT_DOC, (ACME, "framed", ALICE))


def test_non_member_cannot_insert(app_conn: psycopg.Connection) -> None:
    with pytest.raises(RLS_DENIED), as_principal(app_conn, EVE, ACME) as conn:
        conn.execute(INSERT_DOC, (ACME, "outsider", EVE))


# --- chunk insert ------------------------------------------------------------


def test_creator_adds_chunks_to_own_document(app_conn: psycopg.Connection) -> None:
    with as_principal(app_conn, BOB, ACME) as conn:
        conn.execute(INSERT_CHUNK, (T1, ACME, 99, _vec()))
        assert count(conn, "SELECT count(*) FROM chunks WHERE chunk_index = 99") == 1


def test_admin_adds_chunks_to_any_tenant_document(app_conn: psycopg.Connection) -> None:
    with as_principal(app_conn, ALICE, ACME) as conn:
        conn.execute(INSERT_CHUNK, (R2, ACME, 99, _vec()))


def test_cannot_add_chunks_to_unreadable_document(app_conn: psycopg.Connection) -> None:
    with pytest.raises(RLS_DENIED), as_principal(app_conn, BOB, ACME) as conn:
        conn.execute(INSERT_CHUNK, (R1, ACME, 99, _vec()))


def test_read_grant_does_not_allow_writing_chunks(app_conn: psycopg.Connection) -> None:
    # Carol can READ R1 through her grant, but must not be able to add content.
    with pytest.raises(RLS_DENIED), as_principal(app_conn, CAROL, ACME) as conn:
        conn.execute(INSERT_CHUNK, (R1, ACME, 99, _vec()))


def test_composite_fk_rejects_chunk_with_wrong_tenant(migrator: psycopg.Connection) -> None:
    # Even app_migrator (BYPASSRLS) cannot attach a Globex-tagged chunk to an
    # Acme document: the schema, not RLS, forbids it.
    with pytest.raises(errors.ForeignKeyViolation):
        migrator.execute(INSERT_CHUNK, (T1, GLOBEX, 99, _vec()))


# --- delete ------------------------------------------------------------------


def test_creator_deletes_own_document_and_chunks_cascade(
    app_conn: psycopg.Connection, migrator: psycopg.Connection
) -> None:
    with as_principal(app_conn, BOB, ACME) as conn:
        assert conn.execute("DELETE FROM documents WHERE id = %s", (T1,)).rowcount == 1
    assert count(migrator, "SELECT count(*) FROM chunks WHERE document_id = %s", (T1,)) == 0


def test_admin_deletes_member_document(app_conn: psycopg.Connection) -> None:
    with as_principal(app_conn, ALICE, ACME) as conn:
        assert conn.execute("DELETE FROM documents WHERE id = %s", (R2,)).rowcount == 1


def test_reader_cannot_delete(app_conn: psycopg.Connection, migrator: psycopg.Connection) -> None:
    # Carol can read T1 but did not create it: DELETE silently matches 0 rows.
    with as_principal(app_conn, CAROL, ACME) as conn:
        assert conn.execute("DELETE FROM documents WHERE id = %s", (T1,)).rowcount == 0
    assert ids(migrator, "SELECT id FROM documents WHERE id = %s", (T1,)) == {T1}


def test_unfiltered_delete_only_touches_deletable_rows(
    app_conn: psycopg.Connection, migrator: psycopg.Connection
) -> None:
    # A buggy "DELETE FROM documents" with no WHERE still only hits Bob's docs.
    with as_principal(app_conn, BOB, ACME) as conn:
        conn.execute("DELETE FROM documents")
    assert ids(migrator, "SELECT id FROM documents") == {R1, G1}


# --- privileges that do not exist at all -------------------------------------


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE documents SET title = 'x'",
        "UPDATE chunks SET content = 'x'",
        "DELETE FROM chunks",
        "TRUNCATE chunks",
        "INSERT INTO memberships (tenant_id, user_id, role)"
        " VALUES ('00000000-0000-4000-8000-0000000000a1',"
        " '00000000-0000-4000-8000-000000000005', 'admin')",
    ],
)
def test_statement_not_granted_to_app_user(app_conn: psycopg.Connection, statement: str) -> None:
    with pytest.raises(RLS_DENIED), as_principal(app_conn, ALICE, ACME) as conn:
        conn.execute(statement)  # type: ignore[arg-type]
