# ADR 0005: JWT identity (HS256 for the demo)

**Status:** accepted

## Context
RLS needs two facts per request: who the caller is and which tenant they are
acting in (ADR 0003). Those facts must come from something the client cannot
forge, and never from the request body, query string, other headers, or LLM
output (CLAUDE.md §2 rule 1).

## Decision
- Every protected route depends on `app/auth.py:get_principal`, which reads a
  `Authorization: Bearer <jwt>` header and verifies it with PyJWT.
- Claims, all required: `sub` (user UUID), `tid` (tenant UUID), `iat`, `exp`,
  `iss`, `aud`. A token is for **one** tenant; a user in two tenants gets two
  tokens.
- `algorithms=["HS256"]` is pinned. `alg=none`, HS512 and anything else are
  rejected, which rules out the classic algorithm-confusion attacks.
- **Clock-skew leeway is 30 seconds** (`JWT_LEEWAY_SECONDS`). A token expired
  10 s ago is accepted, one expired 60 s ago is rejected; both are tested.
- Every failure returns the same `401 invalid or missing bearer token` with
  `WWW-Authenticate: Bearer`, so a caller learns nothing about why.
- `JWT_SECRET` has no default and must be at least 32 characters.
- `POST /auth/dev-token` exists **only** when `APP_ENV=dev` (in any other
  environment the route is not registered: 404). It takes
  `{user_id, tenant_id}` and signs a token only if that membership exists,
  checked by running `SELECT count(*) FROM memberships` inside that
  principal's own RLS transaction.

## HS256 is for the demo only
HS256 uses one shared secret for signing and verifying, so anything that can
verify tokens can also mint them. That is acceptable here because this API is
its own (dev-only) issuer. In production the API should only *verify*:
**RS256/ES256 with keys fetched from the identity provider's JWKS endpoint**
(Supabase, Auth0, Cognito, ...). `verify_token` is the single place to change;
routes only see a `Principal`.

## No token revocation, by design
There is no deny-list: a stolen token is valid until `exp` (1 hour by default).
What *is* immediate is losing access to data. A token carries identity, not
permissions. Every request re-checks membership through RLS (`is_member()` in
the policies), so removing a membership or a grant takes effect on the very
next request, even with a still-valid token.
`tests/api/test_documents.py::test_membership_removed_takes_effect_on_next_request`
proves this.

## Consequences
- Short token lifetimes keep the stolen-token window small.
- Moving to JWKS changes `verify_token` and settings only.
- A token naming a tenant the user does not belong to (bug or forgery) is
  accepted by the verifier but sees nothing: RLS returns zero rows and writes
  are refused (403). Tested at both the DB and the API layer.
