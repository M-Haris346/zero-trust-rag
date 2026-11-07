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

import math
import random

import psycopg
import pytest

from tests.dataset import ACME, BOB, EMBEDDING_DIM, R1, READABLE, T1, vector_literal
from tests.helpers import as_principal

K = 10
NOISE_CHUNKS = 2_000
READABLE_FAR_CHUNKS = 20

SEARCH = "SELECT document_id FROM chunks ORDER BY embedding <=> %s::vector LIMIT %s"


def _unit(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v]


def _near_query(rng: random.Random) -> list[float]:
    # Query is e0. Cosine distance to these is about 0.02.
    v = [rng.gauss(0.0, 0.01) for _ in range(EMBEDDING_DIM)]
    v[0] += 1.0
    return _unit(v)


def _far_from_query(rng: random.Random) -> list[float]:
    # Mostly e1 with a little e0. Cosine distance to the query is about 0.7.
    v = [rng.gauss(0.0, 0.01) for _ in range(EMBEDDING_DIM)]
    v[1] += 1.0
    v[0] += 0.3
    return _unit(v)


QUERY_VECTOR = vector_literal([1.0] + [0.0] * (EMBEDDING_DIM - 1))


@pytest.fixture
def skewed_corpus(migrator: psycopg.Connection) -> None:
    rng = random.Random(1234)
    with (
        migrator.cursor() as cur,
        cur.copy(
            "COPY chunks (document_id, tenant_id, chunk_index, content, embedding) FROM STDIN"
        ) as copy,
    ):
        for i in range(NOISE_CHUNKS):
            copy.write_row((R1, ACME, 1_000 + i, "noise", vector_literal(_near_query(rng))))
        for i in range(READABLE_FAR_CHUNKS):
            copy.write_row((T1, ACME, 1_000 + i, "rare", vector_literal(_far_from_query(rng))))
    migrator.execute("ANALYZE chunks")


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
