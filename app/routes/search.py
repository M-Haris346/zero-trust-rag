"""Semantic search over the chunks the caller may read. No LLM involved."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.concurrency import run_in_threadpool

from app.auth import Principal, get_principal
from app.db import Pool, tenant_transaction
from app.deps import get_embedder, get_pool
from app.rag.embedder import Embedder
from app.rag.retrieval import retrieve

# Router-level auth as defense in depth; see app/routes/documents.py.
router = APIRouter(tags=["search"], dependencies=[Depends(get_principal)])

MAX_K = 20


class SearchRequest(BaseModel):
    # Only a query and k. Any identity-looking field is rejected with 422.
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=1000)
    k: int = Field(default=5, ge=1, le=MAX_K)

    @field_validator("query")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("query is blank")
        return value


class SearchHit(BaseModel):
    chunk_id: UUID
    document_id: UUID
    chunk_index: int
    content: str
    distance: float


class SearchResponse(BaseModel):
    results: list[SearchHit]


@router.post("/search")
async def search(
    body: SearchRequest,
    principal: Annotated[Principal, Depends(get_principal)],
    pool: Annotated[Pool, Depends(get_pool)],
    embedder: Annotated[Embedder, Depends(get_embedder)],
) -> SearchResponse:
    [query_embedding] = await run_in_threadpool(embedder.embed, [body.query])
    async with tenant_transaction(pool, principal) as conn:
        hits = await retrieve(conn, query_embedding, body.k)
    # Counts only. The query text is never logged.
    structlog.get_logger().info("search", k=body.k, results=len(hits))
    return SearchResponse(results=[SearchHit.model_validate(h, from_attributes=True) for h in hits])
