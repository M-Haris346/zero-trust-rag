"""Liveness and readiness probes."""

from __future__ import annotations

import psycopg
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from psycopg_pool import PoolTimeout

from app.db import Pool

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    """The process is up. Does not touch the database."""
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(request: Request) -> JSONResponse:
    """The database is reachable. Runs no tenant query, so needs no identity."""
    pool: Pool = request.app.state.pool
    try:
        async with pool.connection(timeout=2.0) as conn:
            await conn.execute("SELECT 1")
    except (psycopg.Error, PoolTimeout):
        return JSONResponse({"status": "unavailable"}, status_code=503)
    return JSONResponse({"status": "ready"})
