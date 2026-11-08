"""Logs carry identifiers and outcomes, never content, queries or tokens."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx
import pytest
import structlog
from structlog.testing import LogCapture

from app.log import configure_logging
from tests.api.client import bearer
from tests.dataset import ACME, BOB
from tests.security.canary import CANARY


@pytest.fixture
def captured(client: httpx.AsyncClient) -> Iterator[list[dict[str, Any]]]:
    # Production's content-bearing processor (merge_contextvars), but the final
    # step records event dicts instead of rendering JSON. LogCapture adds its own
    # "log_level" key. Requested after `client`, so it overrides the
    # configuration done by create_app().
    capture = LogCapture()
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            capture,
        ],
        cache_logger_on_first_use=False,
    )
    yield capture.entries
    configure_logging()


async def test_logs_contain_no_content_query_or_token(
    client: httpx.AsyncClient, captured: list[dict[str, Any]]
) -> None:
    headers = bearer(BOB, ACME)
    token = headers["Authorization"].removeprefix("Bearer ")

    upload = await client.post(
        "/documents",
        headers=headers,
        json={"title": "log check", "visibility": "tenant", "content": f"secret {CANARY}"},
    )
    assert upload.status_code == 201
    search = await client.post("/search", headers=headers, json={"query": CANARY, "k": 5})
    assert search.status_code == 200
    assert CANARY in search.text  # control: the canary really flowed through the request

    dump = repr(captured)
    assert captured, "control: something was logged"
    assert CANARY not in dump
    assert token not in dump
    assert "secret" not in dump


async def test_logs_contain_request_metadata(
    client: httpx.AsyncClient, captured: list[dict[str, Any]]
) -> None:
    response = await client.post("/search", headers=bearer(BOB, ACME), json={"query": "q", "k": 3})
    assert response.status_code == 200

    request_logs = [e for e in captured if e.get("event") == "request"]
    assert request_logs
    entry = request_logs[-1]
    assert entry["path"] == "/search"
    assert entry["status"] == 200
    assert entry["request_id"] == response.headers["x-request-id"]
    search_logs = [e for e in captured if e.get("event") == "search"]
    assert search_logs == [
        {
            "event": "search",
            "k": 3,
            "results": 3,
            "log_level": "info",
            "request_id": entry["request_id"],
        }
    ]


async def test_query_logs_outcome_but_no_question_answer_or_chunk_text(
    client: httpx.AsyncClient, captured: list[dict[str, Any]]
) -> None:
    headers = bearer(BOB, ACME)
    upload = await client.post(
        "/documents",
        headers=headers,
        json={"title": "log check", "visibility": "tenant", "content": f"payroll {CANARY}"},
    )
    assert upload.status_code == 201
    question = f"payroll {CANARY} details please"
    response = await client.post("/query", headers=headers, json={"question": question, "k": 3})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "answered"  # control: the pipeline really ran

    dump = repr(captured)
    assert CANARY not in dump
    assert "payroll" not in dump
    assert "details please" not in dump
    assert body["answer"] not in dump

    [query_log] = [e for e in captured if e.get("event") == "query"]
    assert query_log["status"] == "answered"
    assert query_log["request_id"] == response.headers["x-request-id"]
    assert {"retrieved", "relevant", "cited", "rewritten", "duration_ms"} <= set(query_log)
    allowed = {
        "event",
        "log_level",
        "request_id",
        "status",
        "refusal_reason",
        "retrieved",
        "relevant",
        "cited",
        "rewritten",
        "duration_ms",
    }
    assert set(query_log) <= allowed, set(query_log) - allowed
