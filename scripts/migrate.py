"""Apply numbered SQL migrations from db/migrations/ in order.

Usage:
    uv run python scripts/migrate.py [--database-url URL]

Connects as app_migrator (MIGRATOR_DATABASE_URL by default). Each migration
runs in its own transaction and is recorded in schema_migrations with a
SHA-256 checksum. Editing an already-applied file is an error: write a new
migration instead.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
from pathlib import Path
from typing import LiteralString, cast

import psycopg
from dotenv import load_dotenv

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "db" / "migrations"
FILENAME_RE = re.compile(r"^(\d{3})_[a-z0-9_]+\.sql$")
# Arbitrary constant; serialises concurrent migrate runs against the same DB.
ADVISORY_LOCK_ID = 7_315_002_001

CREATE_TRACKING_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    text PRIMARY KEY,
    filename   text NOT NULL,
    sha256     text NOT NULL,
    applied_at timestamptz NOT NULL DEFAULT now()
)
"""


class MigrationError(RuntimeError):
    pass


def _discover(migrations_dir: Path) -> list[tuple[str, Path]]:
    found: list[tuple[str, Path]] = []
    for path in sorted(migrations_dir.glob("*.sql")):
        match = FILENAME_RE.match(path.name)
        if match is None:
            raise MigrationError(f"bad migration filename: {path.name} (want NNN_name.sql)")
        found.append((match.group(1), path))
    versions = [v for v, _ in found]
    if len(versions) != len(set(versions)):
        raise MigrationError(f"duplicate migration version in {migrations_dir}")
    return found


def apply_migrations(database_url: str, migrations_dir: Path = MIGRATIONS_DIR) -> list[str]:
    """Apply pending migrations. Returns the filenames applied in this run."""
    applied_now: list[str] = []
    # connect_timeout: fail fast on a wrong/unreachable host instead of hanging.
    with psycopg.connect(database_url, autocommit=True, connect_timeout=5) as conn:
        conn.execute("SELECT pg_advisory_lock(%s)", (ADVISORY_LOCK_ID,))
        try:
            conn.execute(CREATE_TRACKING_TABLE)
            rows = conn.execute("SELECT version, sha256 FROM schema_migrations").fetchall()
            already = {version: checksum for version, checksum in rows}

            for version, path in _discover(migrations_dir):
                body = path.read_text(encoding="utf-8")
                checksum = hashlib.sha256(body.encode("utf-8")).hexdigest()

                if version in already:
                    if already[version] != checksum:
                        raise MigrationError(
                            f"{path.name} was modified after being applied "
                            "(checksum mismatch). Add a new migration instead."
                        )
                    continue

                with conn.transaction():
                    # No parameters -> psycopg uses the simple query protocol,
                    # which allows a multi-statement file. The SQL comes from a
                    # reviewed file in the repo, never from user input.
                    conn.execute(cast(LiteralString, body))
                    conn.execute(
                        "INSERT INTO schema_migrations (version, filename, sha256)"
                        " VALUES (%s, %s, %s)",
                        (version, path.name, checksum),
                    )
                applied_now.append(path.name)
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (ADVISORY_LOCK_ID,))
    return applied_now


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--database-url", default=os.environ.get("MIGRATOR_DATABASE_URL"))
    args = parser.parse_args()
    if not args.database_url:
        parser.error("set MIGRATOR_DATABASE_URL or pass --database-url")

    try:
        applied = apply_migrations(args.database_url)
    except MigrationError as exc:
        print(f"migration failed: {exc}", file=sys.stderr)
        return 1

    if applied:
        for name in applied:
            print(f"applied {name}")
    else:
        print("nothing to apply")
    return 0


if __name__ == "__main__":
    sys.exit(main())
