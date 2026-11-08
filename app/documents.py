"""SQL for documents, chunks and grants.

Every function takes a connection that is already inside `tenant_transaction`.
None of them filters by tenant or user: RLS does that. Where a row needs the
caller's identity (tenant_id, created_by), it is taken from the transaction
context with current_tenant_id() / current_user_id() in SQL, never passed in
from application code.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import TupleRow

from app.rag.embedder import to_pgvector

Conn = AsyncConnection[TupleRow]


@dataclass(frozen=True)
class DocumentRow:
    id: UUID
    title: str
    visibility: str
    created_by: UUID
    created_at: datetime
    chunk_count: int


class Outcome(Enum):
    OK = "ok"
    NOT_FOUND = "not_found"  # caller cannot read the document (or it does not exist)
    FORBIDDEN = "forbidden"  # caller can read it but may not change it


_SELECT_DOCUMENTS = """
SELECT d.id, d.title, d.visibility, d.created_by, d.created_at,
       (SELECT count(*) FROM chunks c WHERE c.document_id = d.id) AS chunk_count
FROM documents d
"""


def _row(row: tuple[Any, ...]) -> DocumentRow:
    return DocumentRow(
        id=row[0],
        title=row[1],
        visibility=row[2],
        created_by=row[3],
        created_at=row[4],
        chunk_count=int(row[5]),
    )


async def list_documents(conn: Conn) -> list[DocumentRow]:
    # No WHERE clause: RLS returns exactly the documents this caller may read.
    cur = await conn.execute(_SELECT_DOCUMENTS + " ORDER BY d.created_at DESC, d.id")
    return [_row(r) for r in await cur.fetchall()]


async def get_document(conn: Conn, document_id: UUID) -> DocumentRow | None:
    cur = await conn.execute(_SELECT_DOCUMENTS + " WHERE d.id = %s", (document_id,))
    row = await cur.fetchone()
    return _row(row) if row else None


async def create_document(
    conn: Conn,
    title: str,
    visibility: str,
    chunks: list[str],
    embeddings: list[list[float]],
) -> DocumentRow:
    """Insert a document and its chunks. Any failure aborts the whole transaction."""
    cur = await conn.execute(
        "INSERT INTO documents (tenant_id, title, visibility, created_by)"
        " VALUES (current_tenant_id(), %s, %s, current_user_id())"
        " RETURNING id",
        (title, visibility),
    )
    row = await cur.fetchone()
    if row is None:  # cannot happen: INSERT ... RETURNING yields the new row or raises
        raise RuntimeError("INSERT RETURNING returned no row")
    document_id: UUID = row[0]

    async with conn.cursor() as chunk_cur:
        await chunk_cur.executemany(
            "INSERT INTO chunks (document_id, tenant_id, chunk_index, content, embedding)"
            " VALUES (%s, current_tenant_id(), %s, %s, %s::vector)",
            [
                (document_id, i, content, to_pgvector(vector))
                for i, (content, vector) in enumerate(zip(chunks, embeddings, strict=True))
            ],
        )

    created = await get_document(conn, document_id)
    if created is None:  # cannot happen: the read rule lets a creator see their document
        raise RuntimeError("created document is not readable by its creator")
    return created


async def delete_document(conn: Conn, document_id: UUID) -> Outcome:
    if await get_document(conn, document_id) is None:
        return Outcome.NOT_FOUND
    cur = await conn.execute("DELETE FROM documents WHERE id = %s", (document_id,))
    # Readable but not deleted: the delete policy (creator or admin) said no.
    return Outcome.OK if cur.rowcount == 1 else Outcome.FORBIDDEN


async def _can_manage(conn: Conn, document_id: UUID) -> Outcome:
    """Grants may be changed by the document's creator or a tenant admin."""
    cur = await conn.execute(
        "SELECT d.created_by = current_user_id() OR is_tenant_admin(d.tenant_id)"
        " FROM documents d WHERE d.id = %s",
        (document_id,),
    )
    row = await cur.fetchone()
    if row is None:
        return Outcome.NOT_FOUND
    return Outcome.OK if row[0] else Outcome.FORBIDDEN


async def add_grant(conn: Conn, document_id: UUID, user_id: UUID) -> Outcome:
    """Idempotent. Raises ForeignKeyViolation if user_id is not a tenant member."""
    outcome = await _can_manage(conn, document_id)
    if outcome is not Outcome.OK:
        return outcome
    await conn.execute(
        "INSERT INTO document_grants (document_id, tenant_id, user_id)"
        " VALUES (%s, current_tenant_id(), %s) ON CONFLICT DO NOTHING",
        (document_id, user_id),
    )
    return Outcome.OK


async def remove_grant(conn: Conn, document_id: UUID, user_id: UUID) -> Outcome:
    """Idempotent: removing a grant that does not exist is not an error."""
    outcome = await _can_manage(conn, document_id)
    if outcome is not Outcome.OK:
        return outcome
    await conn.execute(
        "DELETE FROM document_grants WHERE document_id = %s AND user_id = %s",
        (document_id, user_id),
    )
    return Outcome.OK
