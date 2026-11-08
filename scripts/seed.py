"""Seed demo tenants, users, memberships, documents and grants.

Usage:
    uv run python scripts/seed.py [--database-url URL]

Runs as app_migrator (BYPASSRLS): seeding is an admin task, like a migration.
Fixed UUIDs make the demo reproducible; ON CONFLICT DO NOTHING makes it safe to
run twice. Chunks are not seeded yet: they need the embedder (next phase).
"""

from __future__ import annotations

import argparse
import os
import sys
from uuid import UUID

import psycopg
from dotenv import load_dotenv

ACME = UUID("00000000-0000-4000-8000-0000000000a1")
GLOBEX = UUID("00000000-0000-4000-8000-0000000000b1")

ALICE = UUID("00000000-0000-4000-8000-000000000001")  # Acme admin
BOB = UUID("00000000-0000-4000-8000-000000000002")  # Acme member
CAROL = UUID("00000000-0000-4000-8000-000000000003")  # Acme member, granted salary bands
DAVE = UUID("00000000-0000-4000-8000-000000000004")  # Globex member

HANDBOOK = UUID("00000000-0000-4000-8000-00000000d001")  # Acme, tenant-wide, by Bob
SALARY_BANDS = UUID("00000000-0000-4000-8000-00000000d002")  # Acme, restricted, by Alice
GLOBEX_ONBOARDING = UUID("00000000-0000-4000-8000-00000000d003")  # Globex, tenant-wide

TENANTS = [(ACME, "Acme Corp"), (GLOBEX, "Globex Inc")]
USERS = [
    (ALICE, "alice@acme.test", "Alice (Acme admin)"),
    (BOB, "bob@acme.test", "Bob (Acme member)"),
    (CAROL, "carol@acme.test", "Carol (Acme member)"),
    (DAVE, "dave@globex.test", "Dave (Globex member)"),
]
MEMBERSHIPS = [
    (ACME, ALICE, "admin"),
    (ACME, BOB, "member"),
    (ACME, CAROL, "member"),
    (GLOBEX, DAVE, "member"),
]
DOCUMENTS = [
    # id, tenant, title, visibility, created_by
    (HANDBOOK, ACME, "Acme public handbook", "tenant", BOB),
    (SALARY_BANDS, ACME, "Acme salary bands (restricted)", "restricted", ALICE),
    (GLOBEX_ONBOARDING, GLOBEX, "Globex onboarding", "tenant", DAVE),
]
GRANTS = [
    # document, tenant, user
    (SALARY_BANDS, ACME, CAROL),
]


SEED_STEPS: list[tuple[str, str, list[tuple[object, ...]]]] = [
    # (table, INSERT statement, rows)
    ("tenants", "INSERT INTO tenants (id, name) VALUES (%s, %s)", TENANTS),
    ("users", "INSERT INTO users (id, email, display_name) VALUES (%s, %s, %s)", USERS),
    (
        "memberships",
        "INSERT INTO memberships (tenant_id, user_id, role) VALUES (%s, %s, %s)",
        MEMBERSHIPS,
    ),
    (
        "documents",
        "INSERT INTO documents (id, tenant_id, title, visibility, created_by)"
        " VALUES (%s, %s, %s, %s, %s)",
        DOCUMENTS,
    ),
    (
        "document_grants",
        "INSERT INTO document_grants (document_id, tenant_id, user_id) VALUES (%s, %s, %s)",
        GRANTS,
    ),
]


def seed(database_url: str) -> dict[str, tuple[int, int]]:
    """Insert the demo rows. Returns {table: (rows inserted, rows in seed)}.

    Rows that already exist are skipped by ON CONFLICT DO NOTHING, so on a
    second run "inserted" is 0. Counted one row at a time (rowcount is 1 or 0)
    so the number reported is exactly what this run wrote.
    """
    result: dict[str, tuple[int, int]] = {}
    with psycopg.connect(database_url, connect_timeout=5) as conn, conn.cursor() as cur:
        for table, insert, rows in SEED_STEPS:
            inserted = 0
            for row in rows:
                cur.execute(insert + " ON CONFLICT DO NOTHING", row)
                inserted += cur.rowcount
            result[table] = (inserted, len(rows))
    # Leaving the `with psycopg.connect(...)` block commits the transaction.
    return result


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--database-url", default=os.environ.get("MIGRATOR_DATABASE_URL"))
    args = parser.parse_args()
    if not args.database_url:
        parser.error("set MIGRATOR_DATABASE_URL or pass --database-url")
    for table, (inserted, total) in seed(args.database_url).items():
        skipped = total - inserted
        note = f" ({skipped} already present)" if skipped else ""
        print(f"{table}: inserted {inserted} of {total}{note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
