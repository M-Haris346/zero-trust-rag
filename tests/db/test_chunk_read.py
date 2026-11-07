"""Chunks inherit the document read rule (chunks_select defers to documents)."""

from __future__ import annotations

from uuid import UUID

import psycopg
import pytest

from tests.dataset import ACME, BOB, CHUNKS_PER_DOC, GLOBEX, R1, READABLE
from tests.helpers import as_principal, count, ids


@pytest.mark.parametrize(("user", "tenant"), list(READABLE))
def test_unfiltered_chunk_select_returns_only_readable_documents(
    app_conn: psycopg.Connection, user: UUID, tenant: UUID
) -> None:
    # The core claim: the "retrieval" query has no filter, RLS still applies.
    with as_principal(app_conn, user, tenant) as conn:
        assert ids(conn, "SELECT document_id FROM chunks") == READABLE[(user, tenant)]
        assert count(conn, "SELECT count(*) FROM chunks") == CHUNKS_PER_DOC * len(
            READABLE[(user, tenant)]
        )


def test_explicit_filter_for_unreadable_document_returns_nothing(
    app_conn: psycopg.Connection,
) -> None:
    # Denied: asking for R1's chunks by id does not get around the policy.
    with as_principal(app_conn, BOB, ACME) as conn:
        assert count(conn, "SELECT count(*) FROM chunks WHERE document_id = %s", (R1,)) == 0


def test_nearest_neighbour_query_returns_only_readable_chunks(
    app_conn: psycopg.Connection,
) -> None:
    # Shape of the real retrieval query: ORDER BY distance, LIMIT k, no WHERE.
    with as_principal(app_conn, BOB, ACME) as conn:
        q = "[" + ",".join(["0.05"] * 384) + "]"
        rows = conn.execute(
            "SELECT document_id FROM chunks ORDER BY embedding <=> %s::vector LIMIT 100", (q,)
        ).fetchall()
        assert rows, "control: bob should get some chunks"
        assert {r[0] for r in rows} <= READABLE[(BOB, ACME)]


def test_forged_tenant_claim_sees_no_chunks(app_conn: psycopg.Connection) -> None:
    with as_principal(app_conn, BOB, GLOBEX) as conn:
        assert count(conn, "SELECT count(*) FROM chunks") == 0
