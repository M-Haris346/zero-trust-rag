"""Canary leak test: restricted content never reaches an unauthorized caller.

R1 (restricted, created by Alice, granted to Carol) contains only a unique
canary string. Unauthorized callers search for that exact string with the
maximum k. If RLS let even one R1 chunk through, the canary would be the top
hit, because its distance to the query is 0.

What this proves: ACCESS CONTROL. The canary is absent for denied callers and
present for allowed ones, through the real HTTP API.

What this does NOT prove: retrieval QUALITY. FakeEmbedder has no semantic
meaning; matching an exact string says nothing about whether real questions
find relevant chunks. That is the job of the eval set (later phase).
"""

from __future__ import annotations

from uuid import UUID

import httpx
import pytest

from tests.api.client import bearer
from tests.dataset import ACME, ALICE, BOB, CAROL, DAVE, EVE, GLOBEX, R1
from tests.security.canary import CANARY

MAX_K = 20

pytestmark = pytest.mark.usefixtures("canary_r1")


async def _search(client: httpx.AsyncClient, user: UUID, tenant: UUID) -> httpx.Response:
    response = await client.post(
        "/search", headers=bearer(user, tenant), json={"query": CANARY, "k": MAX_K}
    )
    assert response.status_code == 200
    return response


@pytest.mark.parametrize(("user", "tenant"), [(CAROL, ACME), (ALICE, ACME)], ids=["grant", "admin"])
async def test_authorized_caller_finds_canary(
    client: httpx.AsyncClient, user: UUID, tenant: UUID
) -> None:
    # Control: proves the canary IS findable, so its absence below means something.
    results = (await _search(client, user, tenant)).json()["results"]
    assert results[0]["content"] == CANARY
    assert results[0]["document_id"] == str(R1)


@pytest.mark.parametrize(
    ("user", "tenant", "expects_results"),
    [
        (BOB, ACME, True),  # same tenant, no grant
        (DAVE, GLOBEX, True),  # other tenant
        (BOB, GLOBEX, False),  # forged tenant claim
        (EVE, ACME, False),  # no membership at all
    ],
    ids=["no-grant", "other-tenant", "forged-tenant", "no-membership"],
)
async def test_unauthorized_caller_never_sees_canary(
    client: httpx.AsyncClient, user: UUID, tenant: UUID, expects_results: bool
) -> None:
    response = await _search(client, user, tenant)
    assert CANARY not in response.text
    assert str(R1) not in response.text
    # Control: the search really ran and returned the caller's own chunks.
    assert bool(response.json()["results"]) is expects_results


@pytest.mark.parametrize(("user", "tenant"), [(BOB, ACME), (DAVE, GLOBEX)])
async def test_canary_absent_from_document_routes(
    client: httpx.AsyncClient, user: UUID, tenant: UUID
) -> None:
    headers = bearer(user, tenant)
    for path in ("/documents", f"/documents/{R1}"):
        response = await client.get(path, headers=headers)
        assert CANARY not in response.text
        assert str(R1) not in response.text
