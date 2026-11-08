"""Nearest-neighbour chunk retrieval.

`retrieve` takes a connection that is already inside `tenant_transaction`, a
query embedding, and k. It has no tenant or user parameter on purpose: identity
is already on the connection, and RLS decides which chunks exist for this
caller (CLAUDE.md §2 rule 1). The query below has no access filter at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import TupleRow

from app.rag.embedder import to_pgvector

# ADR 0004. relaxed_order: keep scanning the HNSW index until k rows survive the
# RLS filter. It may return rows slightly out of order, so the outer query
# re-sorts by distance. Both statements are transaction-local.
_ENABLE_ITERATIVE_SCAN = "SET LOCAL hnsw.iterative_scan = relaxed_order"

_SEARCH = """
WITH candidates AS MATERIALIZED (
    SELECT c.id, c.document_id, c.chunk_index, c.content,
           c.embedding <=> %(q)s::vector AS distance
    FROM chunks c
    ORDER BY c.embedding <=> %(q)s::vector
    LIMIT %(k)s
)
SELECT id, document_id, chunk_index, content, distance
FROM candidates
ORDER BY distance, id
"""


@dataclass(frozen=True)
class RetrievedChunk:
    chunk_id: UUID
    document_id: UUID
    chunk_index: int
    content: str
    distance: float


async def retrieve(
    conn: AsyncConnection[TupleRow], query_embedding: list[float], k: int
) -> list[RetrievedChunk]:
    await conn.execute(_ENABLE_ITERATIVE_SCAN)
    cur = await conn.execute(_SEARCH, {"q": to_pgvector(query_embedding), "k": k})
    return [
        RetrievedChunk(
            chunk_id=row[0],
            document_id=row[1],
            chunk_index=row[2],
            content=row[3],
            distance=float(row[4]),
        )
        for row in await cur.fetchall()
    ]
