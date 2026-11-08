"""Document routes: RLS decides what the API returns, and writes are atomic."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

import httpx
import psycopg
import pytest

from app.rag.chunking import chunk_text
from tests.api.client import bearer, build_app, serve
from tests.dataset import (
    ACME,
    ALICE,
    BOB,
    CAROL,
    CHUNKS_PER_DOC,
    DAVE,
    EVE,
    G1,
    GLOBEX,
    R1,
    R2,
    READABLE,
    T1,
)
from tests.helpers import count

DOCUMENT_FIELDS = {"id", "title", "visibility", "created_by", "created_at", "chunk_count"}


async def _ids(client: httpx.AsyncClient, user: UUID, tenant: UUID) -> set[UUID]:
    response = await client.get("/documents", headers=bearer(user, tenant))
    assert response.status_code == 200
    return {UUID(d["id"]) for d in response.json()["documents"]}


# --- reading -----------------------------------------------------------------


@pytest.mark.parametrize(("user", "tenant"), list(READABLE))
async def test_list_returns_exactly_the_readable_set(
    client: httpx.AsyncClient, user: UUID, tenant: UUID
) -> None:
    assert await _ids(client, user, tenant) == READABLE[(user, tenant)]


async def test_get_readable_document_has_documented_shape(client: httpx.AsyncClient) -> None:
    response = await client.get(f"/documents/{R1}", headers=bearer(CAROL, ACME))
    assert response.status_code == 200
    body = response.json()
    assert set(body) == DOCUMENT_FIELDS  # no tenant_id, no content
    assert body["id"] == str(R1)
    assert body["visibility"] == "restricted"
    assert body["created_by"] == str(ALICE)
    assert body["chunk_count"] == CHUNKS_PER_DOC


@pytest.mark.parametrize(
    ("user", "tenant", "doc"),
    [(BOB, ACME, R1), (CAROL, ACME, R2), (DAVE, GLOBEX, T1), (EVE, ACME, T1)],
    ids=["no-grant", "not-creator", "other-tenant", "no-membership"],
)
async def test_get_unreadable_document_is_404(
    client: httpx.AsyncClient, user: UUID, tenant: UUID, doc: UUID
) -> None:
    response = await client.get(f"/documents/{doc}", headers=bearer(user, tenant))
    assert response.status_code == 404


async def test_forged_tenant_claim_sees_nothing(client: httpx.AsyncClient) -> None:
    # Bob is real, Globex is real, Bob is not a Globex member.
    headers = bearer(BOB, GLOBEX)
    assert await _ids(client, BOB, GLOBEX) == set()
    for doc in (T1, R2, G1):
        assert (await client.get(f"/documents/{doc}", headers=headers)).status_code == 404
    create = await client.post(
        "/documents",
        headers=headers,
        json={"title": "x", "visibility": "tenant", "content": "y"},
    )
    assert create.status_code == 403
    assert create.json() == {"detail": "forbidden"}


async def test_membership_removed_takes_effect_on_next_request(
    client: httpx.AsyncClient, migrator: psycopg.Connection
) -> None:
    headers = bearer(CAROL, ACME)  # issued while Carol is still a member

    # Allowed: before removal.
    assert await _ids(client, CAROL, ACME) == {T1, R1}

    migrator.execute("DELETE FROM memberships WHERE tenant_id = %s AND user_id = %s", (ACME, CAROL))

    # Denied: same, still-valid token. No revocation list needed, RLS sees it.
    assert await _ids(client, CAROL, ACME) == set()
    for doc in (T1, R1):
        assert (await client.get(f"/documents/{doc}", headers=headers)).status_code == 404
    search = await client.post("/search", headers=headers, json={"query": "chunk", "k": 20})
    assert search.status_code == 200
    assert search.json() == {"results": []}


# --- creating ----------------------------------------------------------------


async def test_member_uploads_document(
    client: httpx.AsyncClient, migrator: psycopg.Connection
) -> None:
    content = "word " * 400  # 2,000 chars -> several chunks
    response = await client.post(
        "/documents",
        headers=bearer(BOB, ACME),
        json={"title": "Bob's notes", "visibility": "restricted", "content": content},
    )
    assert response.status_code == 201
    body = response.json()
    assert set(body) == DOCUMENT_FIELDS
    assert body["created_by"] == str(BOB)
    assert body["chunk_count"] == len(chunk_text(content)) > 1

    # Tenant and creator came from the token, via the transaction context.
    row = migrator.execute(
        "SELECT tenant_id, created_by FROM documents WHERE id = %s", (body["id"],)
    ).fetchone()
    assert row == (ACME, BOB)
    assert (
        count(
            migrator,
            "SELECT count(*) FROM chunks WHERE document_id = %s AND tenant_id = %s",
            (body["id"], ACME),
        )
        == body["chunk_count"]
    )

    # The creator can read their own restricted document straight away.
    readable = await client.get(f"/documents/{body['id']}", headers=bearer(BOB, ACME))
    assert readable.status_code == 200


@pytest.mark.parametrize(
    "payload",
    [
        {"title": "", "visibility": "tenant", "content": "x"},
        {"title": "x" * 501, "visibility": "tenant", "content": "x"},
        {"title": "t", "visibility": "public", "content": "x"},
        {"title": "t", "visibility": "tenant", "content": ""},
        {"title": "t", "visibility": "tenant", "content": "   \n\t "},
        {"title": "t", "visibility": "tenant", "content": "x" * 200_001},
        {"title": "t", "visibility": "tenant"},
    ],
    ids=["empty-title", "long-title", "bad-visibility", "empty", "blank", "too-long", "missing"],
)
async def test_invalid_upload_rejected(client: httpx.AsyncClient, payload: dict[str, str]) -> None:
    response = await client.post("/documents", headers=bearer(BOB, ACME), json=payload)
    assert response.status_code == 422


class WrongDimensionEmbedder:
    """Simulates an embedder bug: returns 3-dim vectors for a vector(384) column."""

    dim = 3

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [[0.1, 0.2, 0.3] for _ in texts]


async def test_failed_chunk_insert_rolls_back_document(migrator: psycopg.Connection) -> None:
    # The document INSERT succeeds, then the chunk INSERT fails on dimension.
    # One transaction: the document row must be gone too.
    async with serve(build_app(embedder=WrongDimensionEmbedder())) as client:
        response = await client.post(
            "/documents",
            headers=bearer(BOB, ACME),
            json={"title": "atomic-check", "visibility": "tenant", "content": "some text"},
        )
    assert response.status_code == 500
    assert response.json() == {"detail": "internal error"}
    assert "dimension" not in response.text  # raw Postgres message not leaked
    assert count(migrator, "SELECT count(*) FROM documents WHERE title = 'atomic-check'") == 0


# --- deleting ----------------------------------------------------------------


async def test_creator_deletes_document_and_chunks(
    client: httpx.AsyncClient, migrator: psycopg.Connection
) -> None:
    response = await client.delete(f"/documents/{T1}", headers=bearer(BOB, ACME))
    assert response.status_code == 204
    assert count(migrator, "SELECT count(*) FROM documents WHERE id = %s", (T1,)) == 0
    assert count(migrator, "SELECT count(*) FROM chunks WHERE document_id = %s", (T1,)) == 0


async def test_admin_deletes_member_document(client: httpx.AsyncClient) -> None:
    response = await client.delete(f"/documents/{R2}", headers=bearer(ALICE, ACME))
    assert response.status_code == 204


async def test_reader_who_is_not_creator_gets_403(
    client: httpx.AsyncClient, migrator: psycopg.Connection
) -> None:
    response = await client.delete(f"/documents/{T1}", headers=bearer(CAROL, ACME))
    assert response.status_code == 403
    assert count(migrator, "SELECT count(*) FROM documents WHERE id = %s", (T1,)) == 1


async def test_unreadable_document_delete_is_404(
    client: httpx.AsyncClient, migrator: psycopg.Connection
) -> None:
    response = await client.delete(f"/documents/{T1}", headers=bearer(DAVE, GLOBEX))
    assert response.status_code == 404
    assert count(migrator, "SELECT count(*) FROM documents WHERE id = %s", (T1,)) == 1
