"""JWT verification: every protected route needs a valid, correctly-signed token."""

from __future__ import annotations

import re
import time
from typing import Any

import httpx
import jwt
import pytest
from fastapi import FastAPI
from fastapi.routing import iter_route_contexts

from app import auth as auth_module
from tests.api.client import JWT_SECRET, build_app, make_settings, serve
from tests.dataset import ACME, BOB, T1

# Routes that are intentionally public. Anything else must require a token.
PUBLIC_PATHS = {"/healthz", "/readyz", "/auth/dev-token"}

SETTINGS = make_settings()


def _claims(**overrides: Any) -> dict[str, Any]:
    now = int(time.time())
    claims: dict[str, Any] = {
        "sub": str(BOB),
        "tid": str(ACME),
        "iat": now,
        "exp": now + 300,
        "iss": SETTINGS.jwt_issuer,
        "aud": SETTINGS.jwt_audience,
    }
    claims.update(overrides)
    return {k: v for k, v in claims.items() if v is not None}


def _header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _token(claims: dict[str, Any], key: str = JWT_SECRET, algorithm: str = "HS256") -> str:
    return jwt.encode(claims, key, algorithm=algorithm)


async def test_every_non_public_route_rejects_missing_token(migrator: object) -> None:
    # Walks every route the app exposes (via its OpenAPI schema, which is
    # public API across FastAPI versions), so a new route without auth fails.
    app = build_app(make_settings(app_env="dev"))  # dev: include the dev-only route too
    routes = [
        (method.upper(), path)
        for path, operations in app.openapi()["paths"].items()
        if path not in PUBLIC_PATHS
        for method in operations
    ]
    assert len(routes) >= 7, f"control: expected the protected routes, got {routes}"
    async with serve(app) as client:
        for method, template in routes:
            path = re.sub(r"\{[^}]+\}", str(T1), template)
            response = await client.request(method, path)
            assert response.status_code == 401, (method, template)
            assert response.headers["www-authenticate"] == "Bearer"


# FastAPI's own documentation routes: registered with include_in_schema=False
# by FastAPI itself, and public by design.
FASTAPI_DOCS_PATHS = {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}


def _registered_routes(app: FastAPI) -> set[tuple[str, str]]:
    """Every (METHOD, path) the app actually routes, including hidden ones.

    FastAPI 0.162 keeps included routers lazy (`_IncludedRouter` entries in
    app.routes), so app.routes alone misses them. fastapi.routing.
    iter_route_contexts is the public iterator FastAPI's OpenAPI generator uses
    to expand those; unlike the schema, it does not skip include_in_schema=False.
    """
    found: set[tuple[str, str]] = set()
    for context in iter_route_contexts(app.routes):
        if context.path_format in FASTAPI_DOCS_PATHS:
            continue
        assert context.path_format is not None and context.methods, context
        found.update((method, context.path_format) for method in context.methods)
    return found


def _schema_routes(app: FastAPI) -> set[tuple[str, str]]:
    return {
        (method.upper(), path)
        for path, operations in app.openapi()["paths"].items()
        for method in operations
    }


def test_every_registered_route_is_in_openapi_schema() -> None:
    # The route walk above only sees the schema. A route hidden with
    # include_in_schema=False would escape it, so forbid hidden routes.
    app = build_app(make_settings(app_env="dev"))
    registered = _registered_routes(app)
    assert ("POST", "/search") in registered, "control: enumeration must see included routers"
    hidden = registered - _schema_routes(app)
    assert hidden == set(), f"routes missing from the OpenAPI schema: {sorted(hidden)}"


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [("GET", "/documents", None), ("POST", "/search", {"query": "q", "k": 1})],
)
async def test_principal_is_resolved_once_per_request(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    path: str,
    body: dict[str, Any] | None,
) -> None:
    # Router-level and endpoint-level Depends(get_principal) are the same
    # dependency; FastAPI's per-request cache must run it only once.
    calls: list[str] = []

    def counting_verify(settings: Any, token: str) -> Any:
        calls.append(token)
        return real_verify(settings, token)

    real_verify = auth_module.verify_token
    monkeypatch.setattr(auth_module, "verify_token", counting_verify)

    response = await client.request(method, path, headers=_header(_token(_claims())), json=body)
    assert response.status_code == 200
    assert len(calls) == 1


async def test_valid_token_is_accepted(client: httpx.AsyncClient) -> None:
    response = await client.get("/documents", headers=_header(_token(_claims())))
    assert response.status_code == 200


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer"},
        {"Authorization": "Bearer not-a-jwt"},
        {"Authorization": "Basic Ym9iOnBhc3N3b3Jk"},
    ],
)
async def test_missing_or_malformed_header_rejected(
    client: httpx.AsyncClient, headers: dict[str, str]
) -> None:
    response = await client.get("/documents", headers=headers)
    assert response.status_code == 401


async def test_wrong_secret_rejected(client: httpx.AsyncClient) -> None:
    token = _token(_claims(), key="some-other-secret-that-is-also-long-enough-1234567890")
    assert (await client.get("/documents", headers=_header(token))).status_code == 401


async def test_alg_none_rejected(client: httpx.AsyncClient) -> None:
    token = jwt.encode(_claims(), None, algorithm="none")  # type: ignore[arg-type]
    assert (await client.get("/documents", headers=_header(token))).status_code == 401


async def test_other_algorithm_with_same_secret_rejected(client: httpx.AsyncClient) -> None:
    token = _token(_claims(), algorithm="HS512")
    assert (await client.get("/documents", headers=_header(token))).status_code == 401


async def test_expired_within_leeway_accepted(client: httpx.AsyncClient) -> None:
    # Clock skew: expired 10 s ago, leeway is 30 s.
    now = int(time.time())
    token = _token(_claims(iat=now - 300, exp=now - 10))
    assert (await client.get("/documents", headers=_header(token))).status_code == 200


async def test_expired_beyond_leeway_rejected(client: httpx.AsyncClient) -> None:
    now = int(time.time())
    token = _token(_claims(iat=now - 300, exp=now - 60))
    assert (await client.get("/documents", headers=_header(token))).status_code == 401


@pytest.mark.parametrize(
    "overrides",
    [
        {"iss": "someone-else"},
        {"aud": "another-api"},
        {"tid": None},
        {"sub": None},
        {"exp": None},
        {"iat": None},
        {"sub": "not-a-uuid"},
        {"tid": "not-a-uuid"},
    ],
    ids=["wrong-iss", "wrong-aud", "no-tid", "no-sub", "no-exp", "no-iat", "bad-sub", "bad-tid"],
)
async def test_bad_claims_rejected(client: httpx.AsyncClient, overrides: dict[str, Any]) -> None:
    token = _token(_claims(**overrides))
    assert (await client.get("/documents", headers=_header(token))).status_code == 401
