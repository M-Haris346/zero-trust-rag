"""Grant routes: who may grant, idempotency, and clean errors."""

from __future__ import annotations

from uuid import UUID

import httpx
import pytest

from tests.api.client import bearer
from tests.dataset import ACME, ALICE, BOB, CAROL, DAVE, GLOBEX, R1, R2

# Fragments of raw Postgres error text that must never reach a client.
RAW_DB_TEXT = ("violates", "foreign key", "constraint", "Key (", "DETAIL", "document_grants")


def _grant(doc: UUID, user: UUID) -> str:
    return f"/documents/{doc}/grants/{user}"


async def _carol_reads_r2(client: httpx.AsyncClient) -> bool:
    response = await client.get(f"/documents/{R2}", headers=bearer(CAROL, ACME))
    return response.status_code == 200


async def test_creator_grants_then_revokes(client: httpx.AsyncClient) -> None:
    assert not await _carol_reads_r2(client)

    assert (await client.post(_grant(R2, CAROL), headers=bearer(BOB, ACME))).status_code == 204
    assert await _carol_reads_r2(client)

    assert (await client.delete(_grant(R2, CAROL), headers=bearer(BOB, ACME))).status_code == 204
    assert not await _carol_reads_r2(client)


async def test_admin_grants(client: httpx.AsyncClient) -> None:
    assert (await client.post(_grant(R2, CAROL), headers=bearer(ALICE, ACME))).status_code == 204
    assert await _carol_reads_r2(client)


async def test_duplicate_grant_is_204(client: httpx.AsyncClient) -> None:
    for _ in range(2):
        response = await client.post(_grant(R2, CAROL), headers=bearer(BOB, ACME))
        assert response.status_code == 204


async def test_revoking_missing_grant_is_204(client: httpx.AsyncClient) -> None:
    response = await client.delete(_grant(R2, CAROL), headers=bearer(BOB, ACME))
    assert response.status_code == 204


@pytest.mark.parametrize("method", ["POST", "DELETE"])
async def test_readable_but_not_manager_is_403(client: httpx.AsyncClient, method: str) -> None:
    # Carol reads R1 through her grant, but neither created it nor is admin.
    response = await client.request(method, _grant(R1, BOB), headers=bearer(CAROL, ACME))
    assert response.status_code == 403


@pytest.mark.parametrize("method", ["POST", "DELETE"])
@pytest.mark.parametrize(
    ("user", "tenant"), [(BOB, ACME), (DAVE, GLOBEX)], ids=["no-grant", "other-tenant"]
)
async def test_unreadable_document_is_404(
    client: httpx.AsyncClient, method: str, user: UUID, tenant: UUID
) -> None:
    response = await client.request(method, _grant(R1, CAROL), headers=bearer(user, tenant))
    assert response.status_code == 404


@pytest.mark.parametrize(
    "grantee",
    [DAVE, UUID("00000000-0000-4000-8000-0000000fffff")],
    ids=["member-of-other-tenant", "no-such-user"],
)
async def test_grant_to_non_member_is_clean_422(client: httpx.AsyncClient, grantee: UUID) -> None:
    response = await client.post(_grant(R2, grantee), headers=bearer(BOB, ACME))
    assert response.status_code == 422
    assert response.json() == {"detail": "user is not a member of this tenant"}
    for fragment in RAW_DB_TEXT:
        assert fragment not in response.text
