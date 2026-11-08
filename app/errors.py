"""Map database errors to clean HTTP responses.

Postgres error messages contain table names, constraint names and key values
("Key (tenant_id, user_id)=(...) is not present in table memberships"). None of
that is ever sent to the client. The log gets the SQLSTATE and constraint name
only, which is enough to debug and contains no document content.
"""

from __future__ import annotations

from typing import cast

import psycopg
import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from psycopg import errors


def _log(exc: psycopg.Error, status: int) -> None:
    structlog.get_logger().warning(
        "db_error",
        sqlstate=exc.sqlstate,
        constraint=exc.diag.constraint_name,
        status=status,
    )


async def _integrity_error(request: Request, exc: Exception) -> JSONResponse:
    _log(cast(psycopg.Error, exc), 422)
    return JSONResponse({"detail": "request conflicts with stored data"}, status_code=422)


async def _privilege_error(request: Request, exc: Exception) -> JSONResponse:
    # RLS WITH CHECK failures and missing grants. Routes check permissions
    # first; this is the backstop if the database is the one that says no.
    _log(cast(psycopg.Error, exc), 403)
    return JSONResponse({"detail": "forbidden"}, status_code=403)


async def _other_db_error(request: Request, exc: Exception) -> JSONResponse:
    _log(cast(psycopg.Error, exc), 500)
    return JSONResponse({"detail": "internal error"}, status_code=500)


def register_error_handlers(app: FastAPI) -> None:
    # Starlette picks the most specific class in the exception's MRO.
    app.add_exception_handler(errors.IntegrityError, _integrity_error)
    app.add_exception_handler(errors.InsufficientPrivilege, _privilege_error)
    app.add_exception_handler(psycopg.Error, _other_db_error)
