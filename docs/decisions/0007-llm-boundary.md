# ADR 0007: The LLM boundary — the model is never trusted with access control

**Status:** accepted

## Context
`POST /query` adds an LLM that reads retrieved chunks and writes an answer.
Retrieved text is written by other people, and the question is written by the
caller. Either can contain a prompt injection ("ignore your rules", "you are
now tenant X", "cite document S99"). No prompt wording reliably stops a model
from following such text. So the design must not depend on the model resisting.

## Decision
Access control happens before and after the model, in code. The model only
ever shapes wording about text the caller was already allowed to read.

**Before the model**
- **Identity never reaches the model.** The graph (`app/rag/graph.py`) never
  holds the principal. `make_retriever(pool, principal, embedder, k)` closes over
  it and returns a function of the query string only. Prompts are built from
  `(question, chunk text)` and nothing else (`app/rag/prompts.py`).
- **Retrieval is the existing RLS-scoped `retrieve`, run by code.** Unreadable
  text is never in the prompt, whatever the question says. If nothing is
  retrieved, the request is refused **without calling the model**.
- **Each retrieval runs in its own `tenant_transaction`, closed before any model
  call,** so no pooled connection is held during a slow LLM request.

**In the prompt**
- **Constant system prompts**, which state that document and question text is
  untrusted data.
- **Untrusted text is HTML-escaped and wrapped:** chunks in
  `<document label="S1">`, the question in `<question>`. No text can close its
  wrapper or forge another one.
- **Chunks get short labels (`S1..Sn`) instead of UUIDs.** The server holds the
  label-to-chunk map.

**After the model** (all model output is untrusted)
- **The grader's labels are filtered** to the labels we handed out.
- **An answer is accepted only if** it is non-empty, not marked `insufficient`,
  cites at least one label, and every cited label was graded relevant.
  Otherwise the response is `refused / unsupported_answer`. There are no
  partial answers.
- **Citations returned to the client are mapped** from labels back to the chunks
  that were actually retrieved for this request.

**The graph is exactly CLAUDE.md §6:**
`retrieve → grade_relevance → answer_with_citations → validate_citations`, with
at most one `rewrite_query`, and a `refuse` node. The rewrite step sees only the
question. Documents graded irrelevant get no say in what is searched next.

## Model and API choices
- **Model and request:** `claude-opus-5-5` through the official `anthropic` SDK
  (2.3.0). Structured output into Pydantic models (`beta.messages.parse`).
  Effort is `low` for grading and rewriting and `medium` for the answer; thinking
  is always on for this model, so effort is the only control.
- **Server-side refusal fallback** (`fallbacks: "default"`, beta
  `server-side-fallback-2026-07-01`). If the model's safety classifier declines,
  the API re-runs the request on Anthropic's recommended fallback model. The
  binding was checked against the installed SDK source before use. A decline
  that survives the fallback becomes `refused / model_declined`.
- **Failures:** timeout 30 s, 2 SDK retries. Timeouts, connection errors, rate
  limits and API errors return a generic `503 answer service unavailable`.
  Logs get the status and Anthropic `request_id`, never the error body.

## Consequences
- **A fully hijacked model still can't leak.** It can't widen access, cite a
  chunk that wasn't retrieved, or change identity. The worst case is a
  misleading sentence about chunks the caller could already read, and the
  citations show which ones. This is tested with a scripted malicious
  `FakeLLM` (`tests/security/test_prompt_injection.py`).
- **The identity check covers every test.** An autouse fixture scans every LLM
  request in the whole suite for seeded user and tenant ids, emails and titles.
  A deliberate mutation that appended the user id to the question was caught.
- **Answer quality is not tested deterministically.** `FakeLLM` has no language
  ability. `tests/live/` samples real-model behaviour on a few adversarial
  cases, but it is opt-in, costs money, and is not part of CI. It is a sample,
  not proof.
- **Refusing is the default when anything is unverifiable,** so some
  answerable questions will be refused. That trade-off is deliberate.
