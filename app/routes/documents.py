"""Document upload, listing, reading, deletion, and grants.

Status codes are chosen so that nobody learns a document exists unless they can
already read it: unreadable -> 404, readable but not allowed -> 403.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Response
from psycopg import errors
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.concurrency import run_in_threadpool

from app import documents
from app.auth import Principal, get_principal
from app.db import Pool, tenant_transaction
from app.deps import get_embedder, get_pool
from app.documents import DocumentRow, Outcome
from app.rag.chunking import chunk_text
from app.rag.embedder import Embedder

# Router-level auth is defense in depth: a route added here later without a
# Principal parameter still requires a valid token. Endpoints keep their own
# Principal parameter; FastAPI caches a dependency per request, so
# get_principal runs once (tests/api/test_auth.py checks this).
router = APIRouter(prefix="/documents", tags=["documents"], dependencies=[Depends(get_principal)])

PrincipalDep = Annotated[Principal, Depends(get_principal)]
PoolDep = Annotated[Pool, Depends(get_pool)]
EmbedderDep = Annotated[Embedder, Depends(get_embedder)]


class DocumentCreate(BaseModel):
    # extra="forbid": a client cannot send tenant_id or created_by. Those come
    # from the token, via the transaction context.
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=500)
    visibility: Literal["tenant", "restricted"]
    content: str = Field(min_length=1, max_length=200_000)

    @field_validator("content")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("content is blank")
        return value


class DocumentOut(BaseModel):
    id: UUID
    title: str
    visibility: Literal["tenant", "restricted"]
    created_by: UUID
    created_at: datetime
    chunk_count: int


class DocumentList(BaseModel):
    documents: list[DocumentOut]


def _out(row: DocumentRow) -> DocumentOut:
    return DocumentOut.model_validate(row, from_attributes=True)


def _raise_for(outcome: Outcome) -> None:
    if outcome is Outcome.NOT_FOUND:
        raise HTTPException(status_code=404, detail="document not found")
    if outcome is Outcome.FORBIDDEN:
        raise HTTPException(status_code=403, detail="not allowed for this document")


@router.post("", status_code=201)
async def create_document(
    body: DocumentCreate, principal: PrincipalDep, pool: PoolDep, embedder: EmbedderDep
) -> DocumentOut:
    chunks = chunk_text(body.content)
    # Embed BEFORE opening the transaction: slow CPU work must not hold a
    # pooled connection. The transaction then only does fast inserts, and any
    # insert failure rolls back the document row too.
    embeddings = await run_in_threadpool(embedder.embed, chunks)
    async with tenant_transaction(pool, principal) as conn:
        row = await documents.create_document(conn, body.title, body.visibility, chunks, embeddings)
    structlog.get_logger().info("document_created", document_id=str(row.id), chunks=len(chunks))
    return _out(row)


@router.get("")
async def list_documents(principal: PrincipalDep, pool: PoolDep) -> DocumentList:
    async with tenant_transaction(pool, principal) as conn:
        rows = await documents.list_documents(conn)
    return DocumentList(documents=[_out(r) for r in rows])


@router.get("/{document_id}")
async def get_document(document_id: UUID, principal: PrincipalDep, pool: PoolDep) -> DocumentOut:
    async with tenant_transaction(pool, principal) as conn:
        row = await documents.get_document(conn, document_id)
    if row is None:
        raise HTTPException(status_code=404, detail="document not found")
    return _out(row)


@router.delete("/{document_id}", status_code=204)
async def delete_document(document_id: UUID, principal: PrincipalDep, pool: PoolDep) -> Response:
    async with tenant_transaction(pool, principal) as conn:
        outcome = await documents.delete_document(conn, document_id)
    _raise_for(outcome)
    return Response(status_code=204)


@router.post("/{document_id}/grants/{user_id}", status_code=204)
async def add_grant(
    document_id: UUID, user_id: UUID, principal: PrincipalDep, pool: PoolDep
) -> Response:
    try:
        async with tenant_transaction(pool, principal) as conn:
            outcome = await documents.add_grant(conn, document_id, user_id)
    except errors.ForeignKeyViolation:
        # The composite FK to memberships: grantee is not in this tenant (or
        # does not exist; we deliberately do not say which).
        raise HTTPException(status_code=422, detail="user is not a member of this tenant") from None
    _raise_for(outcome)
    return Response(status_code=204)


@router.delete("/{document_id}/grants/{user_id}", status_code=204)
async def remove_grant(
    document_id: UUID, user_id: UUID, principal: PrincipalDep, pool: PoolDep
) -> Response:
    async with tenant_transaction(pool, principal) as conn:
        outcome = await documents.remove_grant(conn, document_id, user_id)
    _raise_for(outcome)
    return Response(status_code=204)
