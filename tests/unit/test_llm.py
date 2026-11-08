"""AnthropicLLM against the real SDK with a mocked HTTP transport (no network).

This checks what we actually send (model, beta header, fallbacks, effort,
structured-output schema) and how each kind of response is handled.
"""

from __future__ import annotations

import json
from typing import Any

import anthropic
import httpx2
import pytest
from pydantic import BaseModel

from app.rag.llm import (
    AnthropicLLM,
    FakeLLM,
    LLMRefusalError,
    LLMRequest,
    LLMUnavailableError,
)
from app.rag.prompts import AnswerOutput, GradeOutput, grade_request


def _message(text: str, stop_reason: str = "end_turn", **extra: Any) -> dict[str, Any]:
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": "claude-opus-5-5",
        "content": [{"type": "text", "text": text}] if text else [],
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 12, "output_tokens": 7},
        **extra,
    }


class Recorder:
    def __init__(self, status: int, body: dict[str, Any]) -> None:
        self.status, self.body = status, body
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        return httpx2.Response(self.status, json=self.body, headers={"request-id": "req_test"})


def _llm(handler: Any) -> AnthropicLLM:
    client = anthropic.AsyncAnthropic(
        api_key="sk-test-not-a-real-key",
        max_retries=0,
        http_client=anthropic.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(handler)),
    )
    return AnthropicLLM(model="claude-opus-5-5", timeout_seconds=5, max_retries=0, client=client)


GRADE = grade_request("what is in the handbook?", {"S1": "handbook text"})


async def test_request_shape() -> None:
    recorder = Recorder(200, _message('{"relevant_labels": ["S1"]}'))
    await _llm(recorder).generate(GRADE, GradeOutput)

    [sent] = recorder.requests
    body = json.loads(sent.content)
    assert sent.url.path == "/v1/messages"
    assert body["model"] == "claude-opus-5-5"
    assert body["system"] == GRADE.system
    assert body["messages"] == [{"role": "user", "content": GRADE.user}]
    assert body["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in sent.headers["anthropic-beta"]
    assert body["output_config"]["effort"] == "low"
    assert body["output_config"]["format"]["type"] == "json_schema"
    # Removed sampling parameters must not be sent.
    assert not {"temperature", "top_p", "top_k"} & set(body)


async def test_answer_uses_medium_effort() -> None:
    recorder = Recorder(
        200, _message('{"answer": "a", "cited_labels": ["S1"], "insufficient": false}')
    )
    request = LLMRequest(purpose="answer", system="s", user="u")
    result = await _llm(recorder).generate(request, AnswerOutput)
    assert result.cited_labels == ["S1"]
    assert json.loads(recorder.requests[0].content)["output_config"]["effort"] == "medium"


async def test_parses_structured_output() -> None:
    result = await _llm(Recorder(200, _message('{"relevant_labels": ["S1", "S3"]}'))).generate(
        GRADE, GradeOutput
    )
    assert result == GradeOutput(relevant_labels=["S1", "S3"])


async def test_refusal_raises_refusal_error() -> None:
    body = _message("", stop_reason="refusal", stop_details={"type": "refusal", "category": None})
    with pytest.raises(LLMRefusalError):
        await _llm(Recorder(200, body)).generate(GRADE, GradeOutput)


async def test_truncated_output_is_unavailable() -> None:
    with pytest.raises(LLMUnavailableError):
        await _llm(Recorder(200, _message('{"relevant_', stop_reason="max_tokens"))).generate(
            GRADE, GradeOutput
        )


@pytest.mark.parametrize("status", [400, 401, 429, 500, 529])
async def test_api_errors_are_unavailable_without_leaking_body(status: int) -> None:
    secret = "internal detail: org_123 quota exceeded"
    body = {"type": "error", "error": {"type": "api_error", "message": secret}}
    with pytest.raises(LLMUnavailableError) as info:
        await _llm(Recorder(status, body)).generate(GRADE, GradeOutput)
    assert secret not in str(info.value)


async def test_connection_error_is_unavailable() -> None:
    def broken(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("no route", request=request)

    with pytest.raises(LLMUnavailableError):
        await _llm(broken).generate(GRADE, GradeOutput)


async def test_fake_llm_records_and_type_checks() -> None:
    class Wrong(BaseModel):
        x: int

    fake = FakeLLM(handler=lambda request, output: Wrong(x=1))
    with pytest.raises(TypeError):
        await fake.generate(GRADE, GradeOutput)
    assert fake.requests == [GRADE]


async def test_call_log_has_measurements_and_no_content() -> None:
    from structlog.testing import capture_logs

    body = _message('{"relevant_labels": ["S1"]}')
    with capture_logs() as logs:
        await _llm(Recorder(200, body)).generate(GRADE, GradeOutput)
    [entry] = [e for e in logs if e["event"] == "llm_call"]
    assert entry["purpose"] == "grade"
    assert entry["stop_reason"] == "end_turn"
    assert entry["fallback_used"] is False
    assert entry["input_tokens"] == 12 and entry["output_tokens"] == 7
    assert entry["request_id"] == "req_test"
    assert isinstance(entry["duration_ms"], float) and entry["duration_ms"] >= 0
    # Prompt text and model output never go to the log.
    assert "handbook" not in repr(logs) and "relevant_labels" not in repr(logs)
