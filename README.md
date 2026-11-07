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
- **HNSW + RLS "fewer than k" problem**, reproduced and fixed with
  `hnsw.iterative_scan = relaxed_order`. See [ADR 0004](docs/decisions/0004-hnsw-with-rls.md).
- Migration runner with checksums, demo seed, `/healthz` and `/readyz`.
- Tests in `tests/db/` (allowed + denied case for each property) and `tests/api/`.

### Planned
- JWT verification and a dev-only token endpoint.
- Document ingestion (chunking, local `fastembed` embeddings) and query routes.
- LangGraph answer flow with citations and refusal on insufficient context.
- Prompt-injection and canary-leak tests; retrieval eval set.
- Architecture and threat-model docs.

## Quickstart

Requires Docker and [uv](https://docs.astral.sh/uv/).

```bash
cp .env.example .env            # replace every change-me
docker compose up -d db         # first start creates roles + databases
uv sync
uv run python scripts/migrate.py
uv run python scripts/seed.py
uv run pytest -v
```

See RLS in action as the API role:

```sql
-- psql "postgresql://app_user:<password>@localhost:5432/ztrag"
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

**Windows:** psycopg's async mode needs the selector event loop. The tests set
it; when running uvicorn on Windows this is handled in the API phase.

## Design decisions
- [0001 RLS is the enforcement layer](docs/decisions/0001-rls-as-enforcement-layer.md)
- [0002 Three database roles](docs/decisions/0002-database-roles.md)
- [0003 Transaction-local request identity](docs/decisions/0003-transaction-local-context.md)
- [0004 HNSW index scans and RLS](docs/decisions/0004-hnsw-with-rls.md)
