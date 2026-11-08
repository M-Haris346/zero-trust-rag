"""The skewed corpus for HNSW + RLS tests (ADR 0004).

2,000 chunks right next to the query vector, all in R1 (Bob cannot read it),
and 20 chunks Bob *can* read in T1, much further away.
"""

from __future__ import annotations

import math
import random

import psycopg

from tests.dataset import ACME, EMBEDDING_DIM, R1, T1, vector_literal

K = 10
NOISE_CHUNKS = 2_000
READABLE_FAR_CHUNKS = 20

# The query is the unit vector e0.
QUERY_EMBEDDING = [1.0] + [0.0] * (EMBEDDING_DIM - 1)
QUERY_VECTOR = vector_literal(QUERY_EMBEDDING)


def _unit(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v]


def _near_query(rng: random.Random) -> list[float]:
    # Cosine distance to the query is about 0.02.
    v = [rng.gauss(0.0, 0.01) for _ in range(EMBEDDING_DIM)]
    v[0] += 1.0
    return _unit(v)


def _far_from_query(rng: random.Random) -> list[float]:
    # Mostly e1 with a little e0. Cosine distance to the query is about 0.7.
    v = [rng.gauss(0.0, 0.01) for _ in range(EMBEDDING_DIM)]
    v[1] += 1.0
    v[0] += 0.3
    return _unit(v)


def load_skewed_corpus(migrator: psycopg.Connection) -> None:
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
