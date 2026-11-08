"""Caller identity: JWT issue/verify and the `get_principal` dependency.

A Principal is only ever built from a verified token. It is never built from
request bodies, query strings, headers other than Authorization, or LLM output.
See ADR 0005 for the token format and its limits.
"""

from __future__ import annotations

import time
from typing import Any
from uuid import UUID

import jwt
from fastapi import HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict

from app.config import Settings

ALGORITHM = "HS256"
REQUIRED_CLAIMS = ["sub", "tid", "exp", "iat", "iss", "aud"]


class Principal(BaseModel):
    model_config = ConfigDict(frozen=True)

    user_id: UUID
    tenant_id: UUID


class TokenError(Exception):
    """The token is missing, malformed, expired, or not ours."""


def issue_token(settings: Settings, principal: Principal, now: int | None = None) -> str:
    issued_at = int(time.time()) if now is None else now
    claims: dict[str, Any] = {
        "sub": str(principal.user_id),
        "tid": str(principal.tenant_id),
        "iat": issued_at,
        "exp": issued_at + settings.jwt_ttl_seconds,
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
    }
    return jwt.encode(claims, settings.jwt_secret.get_secret_value(), algorithm=ALGORITHM)


def verify_token(settings: Settings, token: str) -> Principal:
    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret.get_secret_value(),
            # Pinned: rejects alg=none and any other algorithm.
            algorithms=[ALGORITHM],
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
            leeway=settings.jwt_leeway_seconds,
            options={"require": REQUIRED_CLAIMS},
        )
        return Principal(user_id=UUID(claims["sub"]), tenant_id=UUID(claims["tid"]))
    except (jwt.PyJWTError, ValueError, TypeError) as exc:
        # One error for every failure: callers learn nothing about why.
        raise TokenError("invalid token") from exc


_bearer = HTTPBearer(auto_error=False)


async def get_principal(request: Request) -> Principal:
    """FastAPI dependency: the verified caller, or 401."""
    credentials: HTTPAuthorizationCredentials | None = await _bearer(request)
    if credentials is None:
        raise _unauthorized()
    settings: Settings = request.app.state.settings
    try:
        return verify_token(settings, credentials.credentials)
    except TokenError:
        raise _unauthorized() from None


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=401,
        detail="invalid or missing bearer token",
        headers={"WWW-Authenticate": "Bearer"},
    )
