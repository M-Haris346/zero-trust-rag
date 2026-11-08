"""POST /query: an answer with citations, or an explicit refusal (ADR 0007)."""

from __future__ import annotations

import time
from typing import Annotated, Literal
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.auth import Principal, get_principal
from app.db import Pool
from app.deps import get_embedder, get_llm, get_pool
from app.rag.embedder import Embedder
from app.rag.graph import make_retriever, run_query
from app.rag.llm import LLM, LLMUnavailableError

# Router-level auth as defense in depth; see app/routes/documents.py.
router = APIRouter(tags=["query"], dependencies=[Depends(get_principal)])

MAX_K = 8


class QueryRequest(BaseModel):
    # Only a question and k. Identity-looking fields are rejected with 422.
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=1000)
    k: int = Field(default=6, ge=1, le=MAX_K)

    @field_validator("question")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("question is blank")
        return value


class Citation(BaseModel):
    chunk_id: UUID
    document_id: UUID
    chunk_index: int


class QueryResponse(BaseModel):
    status: Literal["answered", "refused"]
    answer: str | None
    refusal_reason: Literal["no_relevant_context", "unsupported_answer", "model_declined"] | None
    citations: list[Citation]


@router.post("/query")
async def query(
    body: QueryRequest,
    principal: Annotated[Principal, Depends(get_principal)],
    pool: Annotated[Pool, Depends(get_pool)],
    embedder: Annotated[Embedder, Depends(get_embedder)],
    llm: Annotated[LLM, Depends(get_llm)],
) -> QueryResponse:
    started = time.perf_counter()
    retriever = make_retriever(pool, principal, embedder, body.k)
    try:
        result = await run_query(body.question, retriever, llm)
    except LLMUnavailableError:
        raise HTTPException(status_code=503, detail="answer service unavailable") from None

    # Counts and outcome only: never the question, answer or chunk text.
    structlog.get_logger().info(
        "query",
        status=result.status,
        refusal_reason=result.refusal_reason,
        retrieved=result.retrieved,
        relevant=result.relevant,
        cited=len(result.citations),
        rewritten=result.rewritten,
        duration_ms=round((time.perf_counter() - started) * 1000, 1),
    )
    return QueryResponse(
        status=result.status,
        answer=result.answer,
        refusal_reason=result.refusal_reason,
        citations=[
            Citation(chunk_id=c.chunk_id, document_id=c.document_id, chunk_index=c.chunk_index)
            for c in result.citations
        ],
    )
