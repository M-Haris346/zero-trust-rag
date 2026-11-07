# ADR 0003: Request identity is transaction-local

**Status:** accepted

## Context
Policies need to know who is asking. The API uses a connection pool, so one
physical connection serves many requests from many users, one after another.

`SET app.user_id = '...'` is *session*-level: the value stays on the
connection after the request finishes. If the next request on that connection
forgets to set it, or fails before setting it, it runs as the previous user.

## Decision
`app/db.py:tenant_transaction()` is the only way to run tenant queries:

```python
async with pool.connection() as conn, conn.transaction():
    await conn.execute(
        "SELECT set_config('app.user_id', %s, true), set_config('app.tenant_id', %s, true)",
        (str(principal.user_id), str(principal.tenant_id)),
    )
    yield conn
```

- The third argument `true` means "local to this transaction". Postgres
  discards the values at `COMMIT` or `ROLLBACK`, including when the request
  raises.
- Values are passed as bind parameters, never formatted into SQL.
- The pool uses `autocommit=True`, so no statement runs in an implicit
  transaction: transactions exist only where code opens one.
- Policies read the values with `current_setting(name, true)` (missing is not
  an error) wrapped in `nullif(..., '')`. After a local setting expires,
  Postgres returns `''` rather than `NULL`; `nullif` turns that into `NULL`,
  and `NULL` makes every policy comparison false.

## Consequences
- An unset identity means zero rows, not an error and not a default
  (`tests/db/test_fail_closed.py`).
- `tests/db/test_tenant_transaction.py` uses a pool of size 1 and checks that
  the *same* backend (same `pg_backend_pid()`) sees no identity and no rows
  after both commit and rollback.
- Anything that can run SQL as `app_user` can call `set_config` and claim any
  identity. RLS protects against missing filters and confused application
  code, not against SQL injection. Parameterised queries remain mandatory.
