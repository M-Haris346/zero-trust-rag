"""/auth/dev-token: exists only in dev, and only issues tokens for real members."""

from __future__ import annotations

import psycopg
import pytest

from tests.api.client import build_app, make_settings, serve
from tests.dataset import ACME, BOB, EVE, GLOBEX, R2, T1


async def test_dev_issues_token_that_works(migrator: psycopg.Connection) -> None:
    async with serve(build_app(make_settings(app_env="dev"))) as client:
        response = await client.post(
            "/auth/dev-token", json={"user_id": str(BOB), "tenant_id": str(ACME)}
        )
        assert response.status_code == 200
        body = response.json()
        assert body["token_type"] == "bearer"
        assert body["expires_in"] == 3600

        docs = await client.get(
            "/documents", headers={"Authorization": f"Bearer {body['access_token']}"}
        )
        assert docs.status_code == 200
        assert {d["id"] for d in docs.json()["documents"]} == {str(T1), str(R2)}


@pytest.mark.parametrize(
    ("user", "tenant"),
    [(BOB, GLOBEX), (EVE, ACME)],
    ids=["member-of-other-tenant", "no-memberships"],
)
async def test_dev_refuses_non_members(
    migrator: psycopg.Connection, user: object, tenant: object
) -> None:
    async with serve(build_app(make_settings(app_env="dev"))) as client:
        response = await client.post(
            "/auth/dev-token", json={"user_id": str(user), "tenant_id": str(tenant)}
        )
    assert response.status_code == 403


async def test_dev_rejects_extra_fields(migrator: psycopg.Connection) -> None:
    async with serve(build_app(make_settings(app_env="dev"))) as client:
        response = await client.post(
            "/auth/dev-token",
            json={"user_id": str(BOB), "tenant_id": str(ACME), "role": "admin"},
        )
    assert response.status_code == 422


@pytest.mark.parametrize("env", ["prod", "test"])
async def test_route_does_not_exist_outside_dev(migrator: psycopg.Connection, env: str) -> None:
    async with serve(build_app(make_settings(app_env=env))) as client:
        response = await client.post(
            "/auth/dev-token", json={"user_id": str(BOB), "tenant_id": str(ACME)}
        )
    assert response.status_code == 404
