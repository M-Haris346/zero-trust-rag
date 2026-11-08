"""POST /query through the real app (fake embedder, FakeLLM, test DB)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID

import httpx
import psycopg
import pytest
from pydantic import BaseModel

from app.rag.llm import FakeLLM, LLMRequest, LLMUnavailableError
from tests.api.client import bearer, build_app, serve
from tests.dataset import ACME, BOB, CAROL, GLOBEX, R1, READABLE

RESPONSE_FIELDS = {"status", "answer", "refusal_reason", "citations"}
CITATION_FIELDS = {"chunk_id", "document_id", "chunk_index"}
# Every seeded chunk reads "chunk <i> of <doc>", so FakeLLM grades all of them
# relevant for a question containing "chunk".
QUESTION = "What does each chunk say?"


@pytest.fixture
def llm() -> FakeLLM:
    return FakeLLM()


@pytest.fixture
async def client(migrator: psycopg.Connection, llm: FakeLLM) -> AsyncIterator[httpx.AsyncClient]:
    async with serve(build_app(llm=llm)) as c:
        yield c


async def _ask(
    client: httpx.AsyncClient, user: UUID, tenant: UUID, question: str = QUESTION, k: int = 6
) -> dict[str, object]:
    response = await client.post(
        "/query", headers=bearer(user, tenant), json={"question": question, "k": k}
    )
    assert response.status_code == 200, response.text
    body: dict[str, object] = response.json()
    return body


def _cited_documents(body: dict[str, object]) -> set[UUID]:
    citations = body["citations"]
    assert isinstance(citations, list)
    return {UUID(c["document_id"]) for c in citations}


# --- allowed -----------------------------------------------------------------


async def test_answer_cites_only_readable_documents(client: httpx.AsyncClient) -> None:
    body = await _ask(client, BOB, ACME)
    assert set(body) == RESPONSE_FIELDS
    assert body["status"] == "answered"
    assert body["refusal_reason"] is None
    assert isinstance(body["answer"], str) and body["answer"]
    citations = body["citations"]
    assert isinstance(citations, list) and citations
    assert all(set(c) == CITATION_FIELDS for c in citations)
    assert _cited_documents(body) <= READABLE[(BOB, ACME)]
    assert R1 not in _cited_documents(body)


async def test_grantee_can_get_answers_from_granted_document(client: httpx.AsyncClient) -> None:
    assert R1 in _cited_documents(await _ask(client, CAROL, ACME))


@pytest.mark.parametrize("k", [1, 8])
async def test_k_bounds_allowed(client: httpx.AsyncClient, k: int) -> None:
    body = await _ask(client, BOB, ACME, k=k)
    citations = body["citations"]
    assert isinstance(citations, list) and len(citations) <= k


# --- denied ------------------------------------------------------------------


async def test_forged_tenant_is_refused_without_calling_the_llm(
    client: httpx.AsyncClient, llm: FakeLLM
) -> None:
    body = await _ask(client, BOB, GLOBEX)
    assert body == {
        "status": "refused",
        "answer": None,
        "refusal_reason": "no_relevant_context",
        "citations": [],
    }
    assert llm.requests == []


async def test_removed_membership_is_refused_without_calling_the_llm(
    client: httpx.AsyncClient, llm: FakeLLM, migrator: psycopg.Connection
) -> None:
    assert (await _ask(client, CAROL, ACME))["status"] == "answered"  # control
    llm.requests.clear()
    migrator.execute("DELETE FROM memberships WHERE tenant_id = %s AND user_id = %s", (ACME, CAROL))

    body = await _ask(client, CAROL, ACME)  # same still-valid token
    assert body["status"] == "refused"
    assert body["refusal_reason"] == "no_relevant_context"
    assert llm.requests == []


@pytest.mark.parametrize(
    "payload",
    [
        {"question": "q", "k": 0},
        {"question": "q", "k": 9},
        {"question": "", "k": 3},
        {"question": "   ", "k": 3},
        {"question": "x" * 1001, "k": 3},
        {"question": "q", "k": 3, "tenant_id": str(GLOBEX)},
        {"question": "q", "k": 3, "user_id": str(BOB)},
        {"k": 3},
    ],
    ids=["k-0", "k-9", "empty", "blank", "too-long", "tenant_id", "user_id", "missing"],
)
async def test_invalid_query_rejected(
    client: httpx.AsyncClient, llm: FakeLLM, payload: dict[str, object]
) -> None:
    response = await client.post("/query", headers=bearer(BOB, ACME), json=payload)
    assert response.status_code == 422
    assert llm.requests == []


async def test_llm_outage_is_clean_503(migrator: psycopg.Connection) -> None:
    def down(request: LLMRequest, output: type[BaseModel]) -> BaseModel:
        raise LLMUnavailableError("upstream said: org_123 overloaded at 10.0.0.7")

    async with serve(build_app(llm=FakeLLM(handler=down))) as client:
        response = await client.post(
            "/query", headers=bearer(BOB, ACME), json={"question": QUESTION}
        )
    assert response.status_code == 503
    assert response.json() == {"detail": "answer service unavailable"}
    assert "org_123" not in response.text and "10.0.0.7" not in response.text
