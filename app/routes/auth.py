"""Dev-only token endpoint. Not registered at all unless APP_ENV=dev."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from app.auth import Principal, issue_token
from app.config import Settings
from app.db import Pool, tenant_transaction
from app.deps import get_pool

router = APIRouter(prefix="/auth", tags=["auth (dev only)"])


class DevTokenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: UUID
    tenant_id: UUID


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"  # noqa: S105  # OAuth2 token type name, not a secret
    expires_in: int


@router.post("/dev-token")
async def dev_token(
    body: DevTokenRequest, request: Request, pool: Annotated[Pool, Depends(get_pool)]
) -> TokenResponse:
    principal = Principal(user_id=body.user_id, tenant_id=body.tenant_id)
    # Membership is checked by RLS itself: inside this principal's transaction,
    # memberships_select shows the row only if it really exists.
    async with tenant_transaction(pool, principal) as conn:
        cur = await conn.execute("SELECT count(*) FROM memberships")
        row = await cur.fetchone()
    if row is None or row[0] == 0:
        raise HTTPException(status_code=403, detail="not a member of this tenant")

    settings: Settings = request.app.state.settings
    return TokenResponse(
        access_token=issue_token(settings, principal), expires_in=settings.jwt_ttl_seconds
    )
