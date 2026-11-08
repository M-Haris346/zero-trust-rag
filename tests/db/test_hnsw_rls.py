"""HNSW + RLS: the "fewer than k results" problem and its fix (ADR 0004).

Setup: 2,000 chunks right next to the query vector, all in R1 (Bob cannot read
it), and 20 chunks Bob *can* read, much further away.

The HNSW index returns its ef_search nearest candidates; RLS then removes the
ones Bob may not see. Without iterative scanning every candidate is from R1, so
Bob gets fewer than k rows even though 20+ readable chunks exist. With
hnsw.iterative_scan = relaxed_order, pgvector keeps scanning until k rows
survive the filter.
"""

from __future__ import annotations

import psycopg
import pytest

from tests.corpus import QUERY_VECTOR, K
from tests.dataset import ACME, BOB, READABLE
from tests.helpers import as_principal

SEARCH = "SELECT document_id FROM chunks ORDER BY embedding <=> %s::vector LIMIT %s"


def _search(conn: psycopg.Connection, iterative_scan: str) -> list[object]:
    conn.execute("SET LOCAL enable_seqscan = off")  # make sure the HNSW index is used
    conn.execute("SET LOCAL hnsw.ef_search = 40")
    if iterative_scan == "relaxed_order":
        conn.execute("SET LOCAL hnsw.iterative_scan = relaxed_order")
    else:
        conn.execute("SET LOCAL hnsw.iterative_scan = off")

    plan = "\n".join(r[0] for r in conn.execute("EXPLAIN " + SEARCH, (QUERY_VECTOR, K)).fetchall())
    assert "chunks_embedding_hnsw_idx" in plan, plan
    return [r[0] for r in conn.execute(SEARCH, (QUERY_VECTOR, K)).fetchall()]


@pytest.mark.usefixtures("skewed_corpus")
def test_without_iterative_scan_rls_starves_results(app_conn: psycopg.Connection) -> None:
    # Documents the problem: authorized rows exist, but fewer than K come back.
    with as_principal(app_conn, BOB, ACME) as conn:
        rows = _search(conn, iterative_scan="off")
    assert len(rows) < K


@pytest.mark.usefixtures("skewed_corpus")
def test_iterative_scan_returns_k_authorized_results(app_conn: psycopg.Connection) -> None:
    with as_principal(app_conn, BOB, ACME) as conn:
        rows = _search(conn, iterative_scan="relaxed_order")
    assert len(rows) == K
    assert set(rows) <= READABLE[(BOB, ACME)]
