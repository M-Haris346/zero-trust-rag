"""/search: results come only from readable documents, and inputs are bounded."""

from __future__ import annotations

from uuid import UUID

import httpx
import pytest

from tests.api.client import bearer
from tests.dataset import ACME, BOB, CHUNKS_PER_DOC, DAVE, GLOBEX, READABLE

HIT_FIELDS = {"chunk_id", "document_id", "chunk_index", "content", "distance"}


async def _search(
    client: httpx.AsyncClient, user: UUID, tenant: UUID, query: str = "chunk", k: int = 20
) -> list[dict[str, object]]:
    response = await client.post(
        "/search", headers=bearer(user, tenant), json={"query": query, "k": k}
    )
    assert response.status_code == 200
    results: list[dict[str, object]] = response.json()["results"]
    return results


@pytest.mark.parametrize(("user", "tenant"), list(READABLE))
async def test_results_only_from_readable_documents(
    client: httpx.AsyncClient, user: UUID, tenant: UUID
) -> None:
    hits = await _search(client, user, tenant)
    readable = READABLE[(user, tenant)]
    assert {UUID(str(h["document_id"])) for h in hits} <= readable
    # k=20 exceeds what anyone can read here, so every readable chunk comes back.
    assert len(hits) == CHUNKS_PER_DOC * len(readable)


async def test_hit_shape_and_ordering(client: httpx.AsyncClient) -> None:
    hits = await _search(client, BOB, ACME)
    assert hits, "control: Bob has readable chunks"
    assert all(set(h) == HIT_FIELDS for h in hits)
    distances = [float(str(h["distance"])) for h in hits]
    assert distances == sorted(distances)


async def test_k_limits_result_count(client: httpx.AsyncClient) -> None:
    assert len(await _search(client, DAVE, GLOBEX, k=1)) == 1


@pytest.mark.parametrize("k", [1, 20])
async def test_k_bounds_allowed(client: httpx.AsyncClient, k: int) -> None:
    response = await client.post("/search", headers=bearer(BOB, ACME), json={"query": "q", "k": k})
    assert response.status_code == 200


@pytest.mark.parametrize(
    "payload",
    [
        {"query": "q", "k": 0},
        {"query": "q", "k": 21},
        {"query": "", "k": 5},
        {"query": "   ", "k": 5},
        {"query": "x" * 1001, "k": 5},
        {"k": 5},
    ],
    ids=["k-0", "k-21", "empty", "blank", "too-long", "missing"],
)
async def test_invalid_search_rejected(
    client: httpx.AsyncClient, payload: dict[str, object]
) -> None:
    response = await client.post("/search", headers=bearer(BOB, ACME), json=payload)
    assert response.status_code == 422
