"""FastAPI dependencies for shared resources created at startup."""

from __future__ import annotations

from fastapi import Request

from app.db import Pool
from app.rag.embedder import Embedder
from app.rag.llm import LLM


def get_pool(request: Request) -> Pool:
    pool: Pool = request.app.state.pool
    return pool


def get_embedder(request: Request) -> Embedder:
    embedder: Embedder = request.app.state.embedder
    return embedder


def get_llm(request: Request) -> LLM:
    llm: LLM = request.app.state.llm
    return llm
