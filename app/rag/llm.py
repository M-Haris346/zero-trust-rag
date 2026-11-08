"""The LLM boundary (ADR 0007).

Everything that crosses this boundary is plain text the app has already
rendered (app/rag/prompts.py): the model never receives identity, never picks
what to retrieve, and everything it returns is validated by code afterwards.

Two implementations:
- AnthropicLLM: Claude via the official SDK, structured output into a Pydantic
  model, server-side refusal fallback.
- FakeLLM: deterministic and offline, records every request; tests can script
  it to behave maliciously (a "fully hijacked" model).
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal, Protocol, TypeVar

import anthropic
import structlog
from pydantic import BaseModel, ValidationError

from app.config import Settings

Purpose = Literal["grade", "rewrite", "answer"]
T = TypeVar("T", bound=BaseModel)


@dataclass(frozen=True)
class LLMRequest:
    purpose: Purpose
    system: str
    user: str


class LLMError(Exception):
    pass


class LLMRefusalError(LLMError):
    """The model (and any fallback) declined the request."""


class LLMUnavailableError(LLMError):
    """Timeout, connection, rate limit, API error, or unusable output."""


class LLM(Protocol):
    async def generate(self, request: LLMRequest, output: type[T]) -> T: ...


# --- Anthropic -------------------------------------------------------------

# Thinking is always on for claude-opus-5-5; effort is the only depth control.
# Grading and rewriting are simple classification/rephrasing; the answer is the
# step where quality matters.
_EFFORT: dict[Purpose, Literal["low", "medium"]] = {
    "grade": "low",
    "rewrite": "low",
    "answer": "medium",
}
# Server-side refusal fallback: if the model's safety classifier declines, the
# API re-runs the request on Anthropic's recommended fallback model.
_FALLBACK_BETA = "server-side-fallback-2026-07-01"
_MAX_TOKENS = 16_000


class AnthropicLLM:
    def __init__(
        self,
        model: str,
        timeout_seconds: float,
        max_retries: int,
        client: anthropic.AsyncAnthropic | None = None,
    ) -> None:
        self._model = model
        self._client = client or anthropic.AsyncAnthropic(
            timeout=timeout_seconds, max_retries=max_retries
        )

    async def generate(self, request: LLMRequest, output: type[T]) -> T:
        log = structlog.get_logger()
        started = time.perf_counter()
        try:
            message = await self._client.beta.messages.parse(
                model=self._model,
                max_tokens=_MAX_TOKENS,
                system=request.system,
                messages=[{"role": "user", "content": request.user}],
                output_format=output,
                output_config={"effort": _EFFORT[request.purpose]},
                betas=[_FALLBACK_BETA],
                fallbacks="default",
            )
        except (anthropic.APITimeoutError, anthropic.APIConnectionError) as exc:
            log.warning("llm_unavailable", purpose=request.purpose, error=type(exc).__name__)
            raise LLMUnavailableError("llm unreachable") from exc
        except anthropic.APIStatusError as exc:
            # Includes 429 and 5xx (already retried by the SDK) and 4xx. Log the
            # status and request id, never the message body.
            log.warning(
                "llm_unavailable",
                purpose=request.purpose,
                status=exc.status_code,
                request_id=exc.request_id,
            )
            raise LLMUnavailableError("llm api error") from exc
        except ValidationError as exc:
            log.warning("llm_unavailable", purpose=request.purpose, error="invalid_output")
            raise LLMUnavailableError("llm output did not match schema") from exc

        # A fallback shows up as a "fallback" content block, or (for turns that
        # stay on the fallback model) as a fallback_message usage iteration.
        fallback_used = any(block.type == "fallback" for block in message.content) or any(
            it.type == "fallback_message" for it in (message.usage.iterations or [])
        )
        log.info(
            "llm_call",
            purpose=request.purpose,
            model=message.model,
            stop_reason=message.stop_reason,
            fallback_used=fallback_used,
            input_tokens=message.usage.input_tokens,
            output_tokens=message.usage.output_tokens,
            cache_read_input_tokens=message.usage.cache_read_input_tokens,
            # Wall clock for this call, including any SDK retries.
            duration_ms=round((time.perf_counter() - started) * 1000, 1),
            request_id=message._request_id,
        )
        # Check stop_reason before reading content.
        if message.stop_reason == "refusal":
            raise LLMRefusalError("model declined")
        if message.stop_reason == "max_tokens":
            raise LLMUnavailableError("llm output truncated")
        parsed = message.parsed_output
        if parsed is None:
            raise LLMUnavailableError("llm returned no structured output")
        return parsed


# --- Fake --------------------------------------------------------------------

Handler = Callable[[LLMRequest, type[BaseModel]], BaseModel]

_DOC_RE = re.compile(r'<document label="(S\d+)">\n(.*?)\n</document>', re.DOTALL)
_QUESTION_RE = re.compile(r"<question>\n(.*?)\n</question>", re.DOTALL)
_WORD_RE = re.compile(r"[a-z0-9]{3,}")


def parse_documents(user_prompt: str) -> dict[str, str]:
    """{label: text} from a rendered prompt. Used by FakeLLM and tests."""
    return dict(_DOC_RE.findall(user_prompt))


def parse_question(user_prompt: str) -> str:
    match = _QUESTION_RE.search(user_prompt)
    return match.group(1) if match else ""


def _words(text: str) -> set[str]:
    return set(_WORD_RE.findall(text.lower()))


def default_fake_handler(request: LLMRequest, output: type[BaseModel]) -> BaseModel:
    """Deterministic stand-in with no semantic ability.

    grade:   a document is relevant if it shares a 3+ letter word with the question
    rewrite: returns the question unchanged
    answer:  cites every document it was given
    """
    question = parse_question(request.user)
    documents = parse_documents(request.user)
    if request.purpose == "grade":
        relevant = [label for label, text in documents.items() if _words(text) & _words(question)]
        return output.model_validate({"relevant_labels": relevant})
    if request.purpose == "rewrite":
        return output.model_validate({"query": question})
    labels = list(documents)
    return output.model_validate(
        {
            "answer": f"Answer based on {', '.join(labels)}." if labels else "",
            "cited_labels": labels,
            "insufficient": not labels,
        }
    )


@dataclass
class FakeLLM:
    handler: Handler = default_fake_handler
    requests: list[LLMRequest] = field(default_factory=list)

    async def generate(self, request: LLMRequest, output: type[T]) -> T:
        self.requests.append(request)
        result = self.handler(request, output)
        if not isinstance(result, output):
            raise TypeError(f"fake handler returned {type(result).__name__}, wanted {output}")
        return result


def build_llm(settings: Settings) -> LLM:
    if settings.llm_provider == "fake":
        return FakeLLM()
    return AnthropicLLM(
        model=settings.llm_model,
        timeout_seconds=settings.llm_timeout_seconds,
        max_retries=settings.llm_max_retries,
    )
