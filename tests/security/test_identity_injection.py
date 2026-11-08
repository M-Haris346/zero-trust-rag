"""Identity comes from the verified token only, never from the request."""

from __future__ import annotations

import inspect
from uuid import UUID

import httpx
import psycopg
import pytest

from app.rag.retrieval import retrieve
from tests.api.client import bearer
from tests.dataset import ACME, ALICE, BOB, GLOBEX, READABLE

DOC = {"title": "t", "visibility": "tenant", "content": "hello"}


async def test_upload_lands_in_token_tenant_as_token_user(
    client: httpx.AsyncClient, migrator: psycopg.Connection
) -> None:
    response = await client.post("/documents", headers=bearer(BOB, ACME), json=DOC)
    assert response.status_code == 201
    row = migrator.execute(
        "SELECT tenant_id, created_by FROM documents WHERE id = %s", (response.json()["id"],)
    ).fetchone()
    assert row == (ACME, BOB)


@pytest.mark.parametrize(
    "extra",
    [{"tenant_id": str(GLOBEX)}, {"created_by": str(ALICE)}, {"user_id": str(ALICE)}],
    ids=["tenant_id", "created_by", "user_id"],
)
async def test_upload_with_identity_fields_rejected(
    client: httpx.AsyncClient, migrator: psycopg.Connection, extra: dict[str, str]
) -> None:
    response = await client.post("/documents", headers=bearer(BOB, ACME), json={**DOC, **extra})
    assert response.status_code == 422
    assert migrator.execute("SELECT count(*) FROM documents WHERE title = 't'").fetchone() == (0,)


async def test_search_with_query_and_k_allowed(client: httpx.AsyncClient) -> None:
    response = await client.post("/search", headers=bearer(BOB, ACME), json={"query": "q", "k": 5})
    assert response.status_code == 200


@pytest.mark.parametrize("field", ["tenant_id", "user_id"])
async def test_search_with_identity_fields_rejected(client: httpx.AsyncClient, field: str) -> None:
    response = await client.post(
        "/search",
        headers=bearer(BOB, ACME),
        json={"query": "q", "k": 5, field: str(GLOBEX)},
    )
    assert response.status_code == 422


async def test_identity_in_query_string_or_headers_is_ignored(client: httpx.AsyncClient) -> None:
    response = await client.get(
        "/documents",
        params={"tenant_id": str(GLOBEX), "user_id": str(ALICE)},
        headers={**bearer(BOB, ACME), "X-Tenant-Id": str(GLOBEX), "X-User-Id": str(ALICE)},
    )
    assert response.status_code == 200
    assert {UUID(d["id"]) for d in response.json()["documents"]} == READABLE[(BOB, ACME)]


def test_retrieve_has_no_identity_parameters() -> None:
    # CLAUDE.md §2 rule 1: retrieval takes a query, never a tenant or user.
    assert list(inspect.signature(retrieve).parameters) == ["conn", "query_embedding", "k"]
