"""The answer graph, with a fake retriever and a scripted FakeLLM (no DB, no network)."""

from __future__ import annotations

import inspect
from uuid import UUID, uuid4

import pytest
from pydantic import BaseModel

from app.rag.graph import Retriever, make_retriever, run_query
from app.rag.llm import FakeLLM, LLMRefusalError, LLMRequest, default_fake_handler, parse_documents
from app.rag.prompts import AnswerOutput, GradeOutput, RewriteOutput
from app.rag.retrieval import RetrievedChunk

DOC = UUID("00000000-0000-4000-8000-00000000aaaa")


def chunk(text: str, index: int = 0) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=uuid4(), document_id=DOC, chunk_index=index, content=text, distance=0.1
    )


class FakeRetriever:
    def __init__(self, *batches: list[RetrievedChunk]) -> None:
        self.batches = list(batches)
        self.queries: list[str] = []

    async def __call__(self, query: str) -> list[RetrievedChunk]:
        self.queries.append(query)
        return self.batches.pop(0) if self.batches else []


def scripted(
    grade: list[str] | None = None,
    answer: AnswerOutput | None = None,
    rewrite: str = "better query",
) -> FakeLLM:
    def handler(request: LLMRequest, output: type[BaseModel]) -> BaseModel:
        if request.purpose == "grade" and grade is not None:
            return GradeOutput(relevant_labels=grade)
        if request.purpose == "rewrite":
            return RewriteOutput(query=rewrite)
        if request.purpose == "answer" and answer is not None:
            return answer
        return default_fake_handler(request, output)

    return FakeLLM(handler=handler)


# --- allowed -----------------------------------------------------------------


async def test_relevant_chunks_are_answered_with_mapped_citations() -> None:
    c1, c2 = chunk("handbook vacation policy", 0), chunk("unrelated cafeteria menu", 1)
    llm = scripted(
        grade=["S1"],
        answer=AnswerOutput(answer="20 days.", cited_labels=["S1"], insufficient=False),
    )
    result = await run_query("vacation policy?", FakeRetriever([c1, c2]), llm)
    assert result.status == "answered"
    assert result.answer == "20 days."
    assert result.citations == [c1]
    assert [r.purpose for r in llm.requests] == ["grade", "answer"]


async def test_answer_prompt_contains_only_relevant_chunks() -> None:
    c1, c2 = chunk("apple orchard"), chunk("banana plantation")
    llm = scripted(grade=["S2"])
    await run_query("q", FakeRetriever([c1, c2]), llm)
    answer_request = llm.requests[-1]
    assert answer_request.purpose == "answer"
    # Labels keep their retrieval numbering; the graded-out S1 is not sent.
    assert parse_documents(answer_request.user) == {"S2": "banana plantation"}


async def test_rewrite_then_answer() -> None:
    first, second = [chunk("nothing useful")], [chunk("the actual answer")]
    calls = {"grade": 0}

    def handler(request: LLMRequest, output: type[BaseModel]) -> BaseModel:
        if request.purpose == "grade":
            calls["grade"] += 1
            return GradeOutput(relevant_labels=[] if calls["grade"] == 1 else ["S1"])
        if request.purpose == "rewrite":
            return RewriteOutput(query="actual answer")
        return default_fake_handler(request, output)

    retriever = FakeRetriever(first, second)
    result = await run_query("original question", retriever, FakeLLM(handler=handler))
    assert result.status == "answered"
    assert result.rewritten is True
    assert retriever.queries == ["original question", "actual answer"]


# --- denied ------------------------------------------------------------------


async def test_nothing_relevant_refuses_after_exactly_one_rewrite() -> None:
    retriever = FakeRetriever([chunk("a")], [chunk("b")], [chunk("c")])
    llm = scripted(grade=[])
    result = await run_query("q", retriever, llm)
    assert result.status == "refused"
    assert result.refusal_reason == "no_relevant_context"
    assert len(retriever.queries) == 2  # original + one rewrite, never a third
    assert [r.purpose for r in llm.requests] == ["grade", "rewrite", "grade"]


async def test_no_chunks_means_no_llm_call() -> None:
    llm = scripted()
    result = await run_query("q", FakeRetriever([]), llm)
    assert result.status == "refused"
    assert result.refusal_reason == "no_relevant_context"
    assert llm.requests == []


@pytest.mark.parametrize(
    "draft",
    [
        AnswerOutput(answer="made up", cited_labels=["S9"], insufficient=False),
        AnswerOutput(answer="uses graded-out", cited_labels=["S2"], insufficient=False),
        AnswerOutput(answer="mixed", cited_labels=["S1", "S9"], insufficient=False),
        AnswerOutput(answer="no sources", cited_labels=[], insufficient=False),
        AnswerOutput(answer="", cited_labels=["S1"], insufficient=False),
        AnswerOutput(answer="", cited_labels=[], insufficient=True),
        AnswerOutput(answer="ignored", cited_labels=["S1"], insufficient=True),
    ],
    ids=[
        "unknown-label",
        "irrelevant-label",
        "one-bad-label",
        "no-citations",
        "empty-answer",
        "insufficient",
        "insufficient-with-citation",
    ],
)
async def test_unsupported_answers_are_refused(draft: AnswerOutput) -> None:
    llm = scripted(grade=["S1"], answer=draft)
    result = await run_query("q", FakeRetriever([chunk("x"), chunk("y")]), llm)
    assert result.status == "refused"
    assert result.refusal_reason == "unsupported_answer"
    assert result.answer is None
    assert result.citations == []


async def test_grader_labels_that_were_never_retrieved_are_dropped() -> None:
    # The grader "finds" S7 and S8, which do not exist; only S1 is real.
    llm = scripted(
        grade=["S7", "S1", "S8", "S1"],
        answer=AnswerOutput(answer="ok", cited_labels=["S1"], insufficient=False),
    )
    c1 = chunk("real")
    result = await run_query("q", FakeRetriever([c1]), llm)
    assert result.status == "answered"
    assert result.relevant == 1
    assert result.citations == [c1]


async def test_model_refusal_becomes_refused_response() -> None:
    def handler(request: LLMRequest, output: type[BaseModel]) -> BaseModel:
        raise LLMRefusalError("declined")

    result = await run_query("q", FakeRetriever([chunk("x")]), FakeLLM(handler=handler))
    assert result.status == "refused"
    assert result.refusal_reason == "model_declined"


def test_retriever_takes_only_a_query_string() -> None:
    # CLAUDE.md §2 rule 1: the graph can only ask for text, never for identity.
    async def stub(query: str) -> list[RetrievedChunk]:
        return []

    retriever: Retriever = stub
    built = make_retriever(pool=None, principal=None, embedder=None, k=3)  # type: ignore[arg-type]
    assert list(inspect.signature(built).parameters) == ["query"]
    assert list(inspect.signature(retriever).parameters) == ["query"]
    assert list(inspect.signature(run_query).parameters) == ["question", "retriever", "llm"]
