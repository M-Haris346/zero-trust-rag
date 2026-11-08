# zero-trust-rag

A multi-tenant RAG API where **access control is enforced by PostgreSQL
Row-Level Security**, not by application `WHERE` clauses and not by the LLM
prompt.

The claim this repo is built to prove with automated tests:

> A user can only retrieve chunks from documents they are authorized to read.
> This holds even if the application code forgets a filter, and even if
> retrieved content or the user's question contains a prompt-injection attack.

## Status

### Implemented / Tested
- **Database schema** (`db/migrations/003_schema.sql`): tenants, users,
  memberships, documents, document grants, chunks with `vector(384)` and an
  HNSW index. `tenant_id` on child rows is tied to the parent by composite FKs.
- **RLS policies** (`db/migrations/004_rls.sql`) with `ENABLE` + `FORCE` on
  every table. Read rule: member of the current tenant AND (document is
  tenant-wide OR you created it OR you have a grant OR you are a tenant admin).
  Chunks inherit the document rule.
- **Least-privilege roles**: `app_user` (API, `NOBYPASSRLS`, owns nothing),
  `app_migrator` (migrations/seed), `app_rls_helper` (cannot log in, owns
  policy helpers). See [ADR 0002](docs/decisions/0002-database-roles.md).
- **Transaction-local identity** via `set_config(..., true)` in
  `app/db.py:tenant_transaction`, tested to not leak across pooled connections.
- **JWT auth** (HS256, demo only), pinned algorithm, required claims, 30 s
  clock-skew leeway; dev-only token endpoint. See [ADR 0005](docs/decisions/0005-jwt-and-identity.md).
- **Document API**: upload (chunk + embed, atomic), list, read, delete, grant
  and revoke. Unreadable documents return 404, readable-but-not-allowed 403.
- **Search API** (`POST /search`, query + k only): nearest chunks among those
  the caller may read, with the HNSW + RLS "fewer than k" fix applied. See
  [ADR 0004](docs/decisions/0004-hnsw-with-rls.md).
- **Clean errors**: database errors map to 422 / 403 / 500 with generic
  messages; raw Postgres text never reaches the client.
- **Embedder interface** with a deterministic fake (tests, CI) and optional
  local `fastembed`. See [ADR 0006](docs/decisions/0006-embedder-interface.md).
- **Canary leak test** through the HTTP API (see below).
- **Answer API** (`POST /query`, question + k only): a small LangGraph flow
  (retrieve → grade → answer, at most one query rewrite) on `claude-opus-5-5`.
  Answers cite chunk ids; anything uncited or unverifiable is refused. See
  [ADR 0007](docs/decisions/0007-llm-boundary.md).
- **Prompt-injection containment**, tested deterministically with a scripted
  `FakeLLM`: the model never receives identity, unreadable text never reaches
  it, injected text is escaped and delimited, and a hijacked model cannot cite
  chunks that were not retrieved. A suite-wide check scans every LLM request
  for user/tenant ids, emails and titles.
- [Threat model](docs/threat-model.md) with each threat mapped to its test.
- Tests in `tests/db/`, `tests/api/`, `tests/security/`, `tests/unit/`, with an
  allowed and a denied case for each security property.

### Planned
- Retrieval and answer quality eval set with real embeddings and the real model.
- Architecture doc.
- Production auth: RS256/JWKS verification from an external identity provider.

### Known limitations
- **No server-level request body size limit.** The 200,000-character content
  limit is checked by Pydantic only after the whole body has been read, so a
  very large request still costs memory before it is rejected.
- **No rate limiting** on any route, including `/search` and uploads.
- **Auth is demo-grade.** HS256 with a shared secret (see
  [ADR 0005](docs/decisions/0005-jwt-and-identity.md)). There is no token
  revocation list: a token stays valid until it expires. Removing a membership
  or a grant does take effect on the next request, because RLS re-checks it.
- **Retrieval quality is not evaluated.** Tests and CI use `FakeEmbedder`,
  which has no semantic meaning. The test that runs the real `fastembed` model
  is skipped unless the optional extra is installed.
- **Plain-text upload only.** No PDF or other file formats.
- **Single instance.** Run and tested as one local process; no multi-instance
  deployment setup.
- **Answer wording is not verified.** Citations are checked; the sentences are
  not. A hijacked model could still write a misleading answer about chunks the
  caller may read.
- **Real-model behaviour is only sampled.** `tests/live/` runs a few adversarial
  cases against Claude; it is opt-in (`RUN_LIVE_LLM=1 uv run pytest -m live`),
  costs money, and is not part of CI. The deterministic tests are the evidence.
- **No streaming and no conversation history** for `/query`.
- **The Anthropic request shape is not yet verified against the live API.** The
  combination of server-side `fallbacks`, structured output and `effort` is
  checked against the installed SDK source and by a mocked-HTTP test of the
  exact request body, but no real request has been sent until the live check
  runs.
- **The answer graph is compiled on every request.** Small per-request
  overhead; not optimized yet.

## What the canary test proves (and what it does not)
`tests/security/test_canary_leak.py` makes a unique string the *only* content
of a restricted document, then has unauthorized users (no grant, other tenant,
forged tenant claim, no membership) search for that exact string with the
maximum `k`. The string never appears in any response, while authorized users
get it as the top hit.

This proves **access control** end to end through HTTP. It does **not** prove
retrieval quality: the test embedder has no semantic meaning. Quality needs
real embeddings and an eval set, which is planned.

## Quickstart

Requires Docker and [uv](https://docs.astral.sh/uv/).

```bash
cp .env.example .env            # replace every change-me
docker compose up -d db         # first start creates roles + databases
uv sync                         # add --extra embeddings for real local embeddings
uv run python scripts/migrate.py
uv run python scripts/seed.py
uv run pytest -v
uv run python -m scripts.serve  # API on http://127.0.0.1:8000 (docs at /docs)
```

`EMBEDDER=fastembed` (the default) needs `uv sync --extra embeddings`; without
it `serve.py` exits with that instruction. `EMBEDDER=fake` runs without it, but
search results are then not meaningful.

`LLM_PROVIDER=anthropic` (the default) calls Claude; the SDK finds credentials
itself (`ANTHROPIC_API_KEY`, or `ant auth login`). `LLM_PROVIDER=fake` runs
offline with placeholder answers.

Try it (dev only, `APP_ENV=dev`):

```bash
TOKEN=$(curl -s -X POST http://127.0.0.1:8000/auth/dev-token \
  -H 'content-type: application/json' \
  -d '{"user_id":"00000000-0000-4000-8000-000000000002","tenant_id":"00000000-0000-4000-8000-0000000000a1"}' \
  | python -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')   # bob @ Acme

curl -s http://127.0.0.1:8000/documents -H "Authorization: Bearer $TOKEN"
curl -s -X POST http://127.0.0.1:8000/search -H "Authorization: Bearer $TOKEN" \
  -H 'content-type: application/json' -d '{"query":"handbook","k":5}'
```

## API

| Method | Path | Auth | Result |
|---|---|---|---|
| GET | `/healthz`, `/readyz` | none | liveness / database reachable |
| POST | `/auth/dev-token` | none, **dev only** | `{user_id, tenant_id}` → token if that membership exists, else 403 |
| POST | `/documents` | bearer | `{title, visibility, content}` → 201 document |
| GET | `/documents` | bearer | `{"documents": [document, ...]}` (readable only) |
| GET | `/documents/{id}` | bearer | document, or 404 if not readable |
| DELETE | `/documents/{id}` | bearer | 204 (creator/admin), 403 (readable, not allowed), 404 |
| POST / DELETE | `/documents/{id}/grants/{user_id}` | bearer | 204 (idempotent), 403, 404, 422 if not a tenant member |
| POST | `/search` | bearer | `{query, k≤20}` → `{"results": [{chunk_id, document_id, chunk_index, content, distance}]}` |
| POST | `/query` | bearer | `{question, k≤8}` → `{status: answered\|refused, answer, refusal_reason, citations: [{chunk_id, document_id, chunk_index}]}` |

A **document** is
`{id, title, visibility, created_by, created_at, chunk_count}`. It has no
`tenant_id` (that comes from the token) and no content (content is only
returned by `/search`). Request bodies reject unknown fields, so a client
cannot send `tenant_id`, `user_id` or `created_by`.

## See RLS in action as the API role

```sql
-- psql "postgresql://app_user:<password>@127.0.0.1:5432/ztrag"
SELECT count(*) FROM documents;               -- 0: no identity, no rows

BEGIN;
SELECT set_config('app.user_id',   '00000000-0000-4000-8000-000000000002', true),  -- bob
       set_config('app.tenant_id', '00000000-0000-4000-8000-0000000000a1', true);  -- Acme
SELECT title FROM documents;                  -- only what bob may read
COMMIT;

SELECT count(*) FROM documents;               -- 0 again: identity was transaction-local
```

**Why `127.0.0.1`, not `localhost`:** Postgres is published on IPv4 loopback
only. On Windows, `localhost` tries IPv6 `::1` first and that attempt is not
reported as refused, so the connection waits until `connect_timeout` (set to 5 s
in code) before falling back.

**Windows:** psycopg's async mode cannot use the default Proactor event loop.
The tests and `scripts/serve.py` run on a selector event loop instead.

## Design decisions
- [0001 RLS is the enforcement layer](docs/decisions/0001-rls-as-enforcement-layer.md)
- [0002 Three database roles](docs/decisions/0002-database-roles.md)
- [0003 Transaction-local request identity](docs/decisions/0003-transaction-local-context.md)
- [0004 HNSW index scans and RLS](docs/decisions/0004-hnsw-with-rls.md)
- [0005 JWT identity (HS256 for the demo)](docs/decisions/0005-jwt-and-identity.md)
- [0006 Embedder interface](docs/decisions/0006-embedder-interface.md)
- [0007 The LLM boundary](docs/decisions/0007-llm-boundary.md)
