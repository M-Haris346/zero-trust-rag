"""Shared fixtures.

Two connections per test, on purpose:
  * `migrator` (app_migrator, BYPASSRLS) loads fixtures. Tests use it only to
    set up state or to prove a schema constraint, never to check access.
  * `app_conn` (app_user, NOBYPASSRLS) is what every access assertion uses,
    so the tests see exactly what the API would see.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Iterator

import psycopg
import pytest

from scripts.migrate import apply_migrations
from tests import dataset
from tests.corpus import load_skewed_corpus
from tests.env import TEST_APP_DATABASE_URL, TEST_MIGRATOR_DATABASE_URL

# psycopg's async mode cannot run on Windows' default Proactor event loop.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


@pytest.fixture(scope="session")
def migrated_db() -> None:
    apply_migrations(TEST_MIGRATOR_DATABASE_URL)


@pytest.fixture
def migrator(migrated_db: None) -> Iterator[psycopg.Connection]:
    """app_migrator connection; the canonical dataset is freshly loaded."""
    with psycopg.connect(TEST_MIGRATOR_DATABASE_URL, autocommit=True, connect_timeout=5) as conn:
        dataset.load(conn)
        yield conn


@pytest.fixture
def app_conn(migrator: psycopg.Connection) -> Iterator[psycopg.Connection]:
    """app_user connection with no request context set."""
    with psycopg.connect(TEST_APP_DATABASE_URL, autocommit=True, connect_timeout=5) as conn:
        yield conn


@pytest.fixture
def skewed_corpus(migrator: psycopg.Connection) -> None:
    """Adds the HNSW + RLS skewed corpus (tests/corpus.py) to the dataset."""
    load_skewed_corpus(migrator)
