"""Caller identity.

A Principal is only ever built from a verified token (JWT verification lands
in the API phase). It is never built from request bodies, query strings or
LLM output.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict


class Principal(BaseModel):
    model_config = ConfigDict(frozen=True)

    user_id: UUID
    tenant_id: UUID
