"""Structural guarantees, checked from the catalog.

These fail if someone later adds a table without RLS, gives app_user too much
power, or writes a policy helper without a pinned search_path.
"""

from __future__ import annotations

import psycopg
import pytest
from psycopg import errors

from tests.dataset import ACME, ALL_TABLES, BOB
from tests.helpers import as_principal, count

# Tracking table for migrate.py. app_user has no privileges on it at all.
NOT_TENANT_DATA = {"schema_migrations"}
DEFINER_HELPERS = {"is_member", "is_tenant_admin", "has_grant"}
INVOKER_HELPERS = {"current_user_id", "current_tenant_id"}


def test_app_user_is_not_privileged(migrator: psycopg.Connection) -> None:
    row = migrator.execute(
        "SELECT rolsuper, rolbypassrls, rolcreaterole, rolcreatedb"
        " FROM pg_roles WHERE rolname = 'app_user'"
    ).fetchone()
    assert row == (False, False, False, False)


def test_rls_helper_cannot_log_in(migrator: psycopg.Connection) -> None:
    row = migrator.execute(
        "SELECT rolcanlogin, rolsuper FROM pg_roles WHERE rolname = 'app_rls_helper'"
    ).fetchone()
    assert row == (False, False)


def test_app_user_owns_nothing(migrator: psycopg.Connection) -> None:
    owned = count(
        migrator,
        """
        SELECT (SELECT count(*) FROM pg_class     WHERE relowner = r.oid)
             + (SELECT count(*) FROM pg_proc      WHERE proowner = r.oid)
             + (SELECT count(*) FROM pg_namespace WHERE nspowner = r.oid)
             + (SELECT count(*) FROM pg_type      WHERE typowner = r.oid)
        FROM pg_roles r WHERE r.rolname = 'app_user'
        """,
    )
    assert owned == 0


def test_every_table_has_rls_enabled_and_forced(migrator: psycopg.Connection) -> None:
    rows = migrator.execute(
        """
        SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')
        """
    ).fetchall()
    tables = {name for name, _, _ in rows} - NOT_TENANT_DATA
    assert tables == set(ALL_TABLES), "new table? add it to ALL_TABLES and give it RLS"
    missing = [
        name for name, enabled, forced in rows if name in tables and not (enabled and forced)
    ]
    assert missing == []


def test_app_user_cannot_read_migration_table(app_conn: psycopg.Connection) -> None:
    with pytest.raises(errors.InsufficientPrivilege):
        app_conn.execute("SELECT * FROM schema_migrations")


def test_row_security_off_does_not_bypass(app_conn: psycopg.Connection) -> None:
    # For a non-BYPASSRLS role, row_security=off makes queries *error*, not leak.
    with pytest.raises(errors.InsufficientPrivilege), as_principal(app_conn, BOB, ACME) as conn:
        conn.execute("SET LOCAL row_security = off")
        conn.execute("SELECT * FROM documents")


def test_definer_helpers_are_locked_down(migrator: psycopg.Connection) -> None:
    rows = migrator.execute(
        """
        SELECT p.proname, p.prosecdef, p.provolatile, p.proconfig, pg_get_userbyid(p.proowner)
        FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = 'public' AND p.proname = ANY(%s)
        """,
        (list(DEFINER_HELPERS),),
    ).fetchall()
    assert {r[0] for r in rows} == DEFINER_HELPERS
    for name, secdef, volatile, config, owner in rows:
        assert secdef, f"{name} must be SECURITY DEFINER"
        assert volatile == "s", f"{name} must be STABLE"
        assert config == ["search_path=pg_catalog, public"], name
        assert owner == "app_rls_helper", name


def test_every_non_extension_function_pins_search_path(migrator: psycopg.Connection) -> None:
    rows = migrator.execute(
        """
        SELECT p.proname, p.proconfig
        FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = 'public'
          AND NOT EXISTS (SELECT 1 FROM pg_depend d
                          WHERE d.classid = 'pg_proc'::regclass
                            AND d.objid = p.oid AND d.deptype = 'e')
        """
    ).fetchall()
    assert {r[0] for r in rows} == DEFINER_HELPERS | INVOKER_HELPERS
    for name, config in rows:
        assert config == ["search_path=pg_catalog, public"], name


def test_rls_helper_can_read_only_two_tables(migrator: psycopg.Connection) -> None:
    rows = migrator.execute(
        """
        SELECT t, has_table_privilege('app_rls_helper', t, 'SELECT'),
                  has_table_privilege('app_rls_helper', t, 'INSERT,UPDATE,DELETE')
        FROM unnest(%s::text[]) AS t
        """,
        (list(ALL_TABLES),),
    ).fetchall()
    readable = {t for t, can_select, _ in rows if can_select}
    writable = {t for t, _, can_write in rows if can_write}
    assert readable == {"memberships", "document_grants"}
    assert writable == set()


def test_helpers_not_executable_by_public(migrator: psycopg.Connection) -> None:
    # Allowed: app_user. Denied: a role that was never granted (PUBLIC).
    for fn in ("is_member(uuid)", "is_tenant_admin(uuid)", "has_grant(uuid)"):
        row = migrator.execute(
            "SELECT has_function_privilege('app_user', %s, 'EXECUTE'),"
            " has_function_privilege('app_migrator', %s, 'EXECUTE')",
            (fn, fn),
        ).fetchone()
        assert row is not None
        app_user_can, migrator_can = row
        assert app_user_can, fn
        assert not migrator_can, f"{fn}: PUBLIC execute was not revoked"
