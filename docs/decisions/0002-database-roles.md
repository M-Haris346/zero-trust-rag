# ADR 0002: Three database roles

**Status:** accepted

## Context
RLS is only as strong as the role the API connects with. A superuser, a
`BYPASSRLS` role, or (without `FORCE`) the table owner skips policies entirely.

Two complications:

- We use `FORCE ROW LEVEL SECURITY` so that the table owner is also filtered.
  That means whoever loads seed data or test fixtures needs `BYPASSRLS`.
- The `documents` policy must check `memberships` and `document_grants`.
  Querying those from inside a policy would apply *their* policies too, and a
  grants policy that looks back at `documents` would recurse. The usual fix is
  `SECURITY DEFINER` helper functions. But a definer function owned by the
  table owner is, with `FORCE`, itself subject to the policy it is helping
  evaluate.

## Decision

| Role | Attributes | Used for |
|---|---|---|
| `app_migrator` | `LOGIN NOSUPERUSER BYPASSRLS`, owns the schema | migrations, seed, test fixtures. Never the API. |
| `app_rls_helper` | `NOLOGIN NOSUPERUSER BYPASSRLS` | owns the three SECURITY DEFINER helpers. `SELECT` on `memberships` and `document_grants` only. |
| `app_user` | `LOGIN NOSUPERUSER NOBYPASSRLS`, owns nothing | the API at runtime. Explicit `SELECT/INSERT/DELETE` grants; no `UPDATE`, no `TRUNCATE`. |

- Roles are created by `db/bootstrap/00_bootstrap.sh` as the superuser,
  because creating a `BYPASSRLS` role and the `vector` extension require it.
  Passwords come from environment variables, never from SQL files.
- `002_roles.sql` refuses to migrate if `app_user` is superuser or bypasses RLS.
- The helpers (`is_member`, `is_tenant_admin`, `has_grant`) are `STABLE`,
  `SET search_path = pg_catalog, public`, use schema-qualified table names,
  and only answer questions about the *current* user. `EXECUTE` is revoked
  from `PUBLIC` and granted to `app_user`.
- `app_migrator` is granted membership in `app_rls_helper` with
  `INHERIT FALSE, SET TRUE`: it can hand ownership to the helper role, but
  does not gain the helper's privileges.

## Alternatives considered
- **Two roles; `app_migrator` owns the helpers** (the common Supabase shape).
  Simpler, but every helper then runs with full owner rights. A bug in a helper
  could read any table.
- **No `FORCE`.** Then the owner silently bypasses RLS, which turns any
  accidental owner connection into a full data leak.

## Consequences
- `tests/db/test_hardening.py` checks all of this from the catalog: role
  attributes, ownership, FORCE on every table, helper properties, and that the
  helper role can read exactly two tables.
- One extra role to explain. In exchange, the RLS bypass that the policies need
  is limited to two tables, and that role cannot log in.
