"""The answer graph (CLAUDE.md §6), kept exactly this small:

    retrieve -> grade_relevance -> answer_with_citations -> validate_citations
                      |                                         |
                      +-> rewrite_query (at most once) -> retrieve
                      +-> refuse  <-----------------------------+

Trust model (ADR 0007):
- Identity never enters the graph. `make_retriever` closes over the principal
  and returns a function of the query string only; graph state holds no ids.
- Retrieval is the existing RLS-scoped `retrieve`, so text the caller may not
  read never reaches the model.
- The model's output is untrusted: grader labels are filtered to labels we
  handed out, and an answer is accepted only if it cites at least one relevant
  label and nothing else. Otherwise the result is a refusal.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from starlette.concurrency import run_in_threadpool

from app.auth import Principal
from app.db import Pool, tenant_transaction
from app.rag.embedder import Embedder
from app.rag.llm import LLM, LLMRefusalError
from app.rag.prompts import (
    AnswerOutput,
    GradeOutput,
    RewriteOutput,
    answer_request,
    grade_request,
    rewrite_request,
)
from app.rag.retrieval import RetrievedChunk, retrieve

Retriever = Callable[[str], Awaitable[list[RetrievedChunk]]]
RefusalReason = Literal["no_relevant_context", "unsupported_answer", "model_declined"]


def make_retriever(pool: Pool, principal: Principal, embedder: Embedder, k: int) -> Retriever:
    """Bind identity and k here, so the graph only ever passes a query string."""

    async def retrieve_chunks(query: str) -> list[RetrievedChunk]:
        # Embed outside the transaction; the transaction is closed again before
        # the graph makes any LLM call, so no pooled connection is held then.
        [embedding] = await run_in_threadpool(embedder.embed, [query])
        async with tenant_transaction(pool, principal) as conn:
            return await retrieve(conn, embedding, k)

    return retrieve_chunks


@dataclass(frozen=True)
class QueryResult:
    status: Literal["answered", "refused"]
    answer: str | None
    refusal_reason: RefusalReason | None
    citations: list[RetrievedChunk] = field(default_factory=list)
    # Counts for logging only (never content).
    retrieved: int = 0
    relevant: int = 0
    rewritten: bool = False


class _State(TypedDict, total=False):
    question: str
    search_query: str
    rewritten: bool
    labeled: dict[str, RetrievedChunk]  # label -> chunk, for this retrieval
    relevant: list[str]  # labels, filtered to `labeled`
    draft: AnswerOutput
    result: QueryResult


def _refused(state: _State, reason: RefusalReason) -> QueryResult:
    return QueryResult(
        status="refused",
        answer=None,
        refusal_reason=reason,
        retrieved=len(state.get("labeled", {})),
        relevant=len(state.get("relevant", [])),
        rewritten=state.get("rewritten", False),
    )


def build_graph(retriever: Retriever, llm: LLM) -> CompiledStateGraph[_State]:

    async def retrieve_node(state: _State) -> _State:
        chunks = await retriever(state["search_query"])
        return {"labeled": {f"S{i}": c for i, c in enumerate(chunks, start=1)}, "relevant": []}

    async def grade_node(state: _State) -> _State:
        labeled = state["labeled"]
        documents = {label: chunk.content for label, chunk in labeled.items()}
        graded = await llm.generate(grade_request(state["question"], documents), GradeOutput)
        # Untrusted output: keep only labels we handed out, in order, once.
        relevant = list(dict.fromkeys(lbl for lbl in graded.relevant_labels if lbl in labeled))
        return {"relevant": relevant}

    async def rewrite_node(state: _State) -> _State:
        rewritten = await llm.generate(rewrite_request(state["question"]), RewriteOutput)
        query = rewritten.query.strip() or state["question"]
        return {"search_query": query, "rewritten": True}

    async def answer_node(state: _State) -> _State:
        documents = {label: state["labeled"][label].content for label in state["relevant"]}
        draft = await llm.generate(answer_request(state["question"], documents), AnswerOutput)
        return {"draft": draft}

    def validate_node(state: _State) -> _State:
        draft = state["draft"]
        relevant = set(state["relevant"])
        cited = list(dict.fromkeys(draft.cited_labels))
        if (
            draft.insufficient
            or not draft.answer.strip()
            or not cited
            or any(label not in relevant for label in cited)
        ):
            return {"result": _refused(state, "unsupported_answer")}
        return {
            "result": QueryResult(
                status="answered",
                answer=draft.answer,
                refusal_reason=None,
                citations=[state["labeled"][label] for label in cited],
                retrieved=len(state["labeled"]),
                relevant=len(relevant),
                rewritten=state.get("rewritten", False),
            )
        }

    def refuse_node(state: _State) -> _State:
        return {"result": _refused(state, "no_relevant_context")}

    def after_retrieve(state: _State) -> str:
        # Nothing retrieved: refuse without calling the model at all.
        return "grade_relevance" if state["labeled"] else "refuse"

    def after_grade(state: _State) -> str:
        if state["relevant"]:
            return "answer_with_citations"
        return "refuse" if state.get("rewritten", False) else "rewrite_query"

    graph = StateGraph(_State)
    graph.add_node("retrieve", retrieve_node)
    graph.add_node("grade_relevance", grade_node)
    graph.add_node("rewrite_query", rewrite_node)
    graph.add_node("answer_with_citations", answer_node)
    graph.add_node("validate_citations", validate_node)
    graph.add_node("refuse", refuse_node)

    graph.add_edge(START, "retrieve")
    graph.add_conditional_edges("retrieve", after_retrieve, ["grade_relevance", "refuse"])
    graph.add_conditional_edges(
        "grade_relevance", after_grade, ["answer_with_citations", "rewrite_query", "refuse"]
    )
    graph.add_edge("rewrite_query", "retrieve")
    graph.add_edge("answer_with_citations", "validate_citations")
    graph.add_edge("validate_citations", END)
    graph.add_edge("refuse", END)
    return graph.compile()


async def run_query(question: str, retriever: Retriever, llm: LLM) -> QueryResult:
    graph = build_graph(retriever, llm)
    initial: _State = {"question": question, "search_query": question, "rewritten": False}
    try:
        final = await graph.ainvoke(initial)
    except LLMRefusalError:
        return QueryResult(status="refused", answer=None, refusal_reason="model_declined")
    result = final["result"]
    if not isinstance(result, QueryResult):  # defensive: graph always sets result
        raise TypeError("graph finished without a result")
    return result
