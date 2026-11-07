# ADR 0001: PostgreSQL RLS is the access-control layer

**Status:** accepted

## Context
A multi-tenant RAG system has three places where access control could live:

1. **The LLM prompt** ("only answer using documents the user may see").
   The model cannot enforce anything. Retrieved text or the user's question can
   contain prompt injection. This is not access control.
2. **Application `WHERE` clauses** (`WHERE tenant_id = :tid AND ...`).
   This works until one query forgets the clause. Every new query, script or
   endpoint is a new chance to get it wrong, and nothing catches the mistake.
3. **The database itself**, with Row-Level Security.

## Decision
Access control is enforced by PostgreSQL RLS policies on every tenant-data
table (`db/migrations/004_rls.sql`). The application tells Postgres *who* is
asking. Postgres decides *what* they can see.

- Identity comes only from the verified JWT. It is passed to Postgres as
  transaction-local settings (`app.user_id`, `app.tenant_id`); see ADR 0003.
- The API connects as `app_user` (`NOBYPASSRLS`), so it cannot skip policies;
  see ADR 0002.
- Retrieval code may still filter for relevance or performance, but nothing
  depends on that filter for security.

## Consequences
- A query with no `WHERE` clause returns only authorized rows. The tests
  in `tests/db/test_document_read.py` and `test_chunk_read.py` run exactly that.
- Policies are SQL, so they are reviewable in one file and testable directly
  against the database without going through HTTP.
- Policy functions run per candidate row, which costs something on large scans.
  Context readers are wrapped as `(SELECT current_tenant_id())` so they are
  evaluated once per query.
- Approximate vector search interacts badly with post-filtering; see ADR 0004.
- The same model maps to Supabase (`auth.uid()` there plays the role of
  `app.user_id` here) if the project ever moves to it.
