"""Shared fixtures.

Two connections per test, on purpose:
  * `migrator` (app_migrator, BYPASSRLS) loads fixtures. Tests use it only to
    set up state or to prove a schema constraint, never to check access.
  * `app_conn` (app_user, NOBYPASSRLS) is what every access assertion uses,
    so the tests see exactly what the API would see.
"""

from __future__ import annotations

import asyncio
import re
import sys
from collections.abc import Iterator
from typing import Any

import psycopg
import pytest

from app.rag.llm import FakeLLM, LLMRequest
from scripts.migrate import apply_migrations
from tests import dataset
from tests.corpus import load_skewed_corpus
from tests.env import TEST_APP_DATABASE_URL, TEST_MIGRATOR_DATABASE_URL

_QUESTION_BLOCK = re.compile(r"<question>.*?</question>", re.DOTALL)

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


@pytest.fixture(autouse=True)
def llm_identity_sweep(
    monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> Iterator[list[LLMRequest]]:
    """Every test, suite-wide: no LLM request may carry identity (ADR 0007).

    Wraps FakeLLM.generate for all instances (the app's own, and scripted ones)
    and, after the test, scans every request for user/tenant ids, emails and
    document titles. The <question> block is scanned too, so the application
    cannot smuggle identity in there. A test whose *user* deliberately types
    an id into the question opts out for that block only, with
    @pytest.mark.identity_in_question; system and documents are still scanned.
    """
    question_typed_by_user = request.node.get_closest_marker("identity_in_question") is not None
    seen: list[LLMRequest] = []
    original = FakeLLM.generate

    async def recording_generate(self: FakeLLM, request: LLMRequest, output: Any) -> Any:
        seen.append(request)
        return await original(self, request, output)

    monkeypatch.setattr(FakeLLM, "generate", recording_generate)
    yield seen
    for llm_request in seen:
        user = llm_request.user
        if question_typed_by_user:
            user = _QUESTION_BLOCK.sub("", user)
        leaked = [s for s in dataset.IDENTITY_STRINGS if s in llm_request.system + user]
        assert leaked == [], f"{llm_request.purpose} request carried identity: {leaked}"
