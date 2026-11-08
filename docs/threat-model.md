# Threat model

Scope: the API in this repository, run as documented (one process, Postgres on
loopback, Claude through the Anthropic API). Each row names the control and the
test that checks it. Anything not listed here is not claimed.

## Assets
- **Document content** (chunks), especially restricted documents.
- **Who can read what:** memberships, grants, roles.
- **Credentials:** the JWT secret, database passwords, the Anthropic API key.

## Trust boundaries
| Boundary | Trusted side | Untrusted side |
|---|---|---|
| HTTP | API | client: body, query string, headers |
| JWT | verified claims (`sub`, `tid`) | everything else in the request |
| Database | RLS policies running as `app_user` | application SQL, which may forget a filter |
| LLM | code before and after the call | the model's output, document text, the question |

## Threats and controls
| # | Threat | Control | Tested in |
|---|---|---|---|
| 1 | Query forgets a tenant or permission filter | RLS on every table, `FORCE`, `app_user` is `NOBYPASSRLS` | `tests/db/test_document_read.py`, `test_chunk_read.py`, `test_hardening.py` |
| 2 | Identity leaks between pooled connections | `set_config(..., true)` inside the request transaction | `tests/db/test_tenant_transaction.py` |
| 3 | Missing or blank identity matches rows | `nullif(current_setting(..., true), '')` makes every policy false | `tests/db/test_fail_closed.py` |
| 4 | Forged or invalid token | HS256 pinned, required claims, 30 s leeway | `tests/api/test_auth.py` |
| 5 | Token claims a tenant the user doesn't belong to | `is_member()` in every policy | `tests/db/test_document_read.py`, `tests/api/test_documents.py` |
| 6 | Revoked access with a still-valid token | membership and grants re-checked by RLS on every request | `test_membership_removed_*` (documents, query) |
| 7 | Client sends `tenant_id` / `user_id` / `created_by` | `extra="forbid"` request models; identity comes only from the token | `tests/security/test_identity_injection.py`, `tests/api/test_query.py` |
| 8 | A new or hidden route without auth | router-level auth; route walk plus hidden-route check | `tests/api/test_auth.py` |
| 9 | Raw database or API errors leak details | generic 422 / 403 / 500 / 503 responses | `tests/api/test_errors.py`, `test_query.py` |
| 10 | Content, queries or tokens in logs | only ids and counts are logged | `tests/security/test_logging.py` |
| 11 | **Prompt injection in a document** widens access | retrieval is RLS-scoped and done by code before the LLM; text is escaped and delimited | `tests/security/test_prompt_injection.py` |
| 12 | **Prompt injection in the question** changes identity or scope | the LLM never receives identity; the retriever takes a query string only | same file; identity sweep across the whole suite |
| 13 | **Hijacked model** invents sources or cites unreadable chunks | server-side citation check; anything unverifiable is refused | `test_hijacked_model_*`, `tests/unit/test_graph.py` |
| 14 | Restricted text reaches the model for an unauthorized user | RLS before the LLM; no chunks means no LLM call | canary tests (`test_canary_*`) |
| 15 | Secrets committed | `.env*` gitignored; placeholders only; staged-secrets check before commits | process; `.gitignore` |

## Residual risks (not mitigated, stated plainly)
- **A hijacked model can write a misleading answer** about chunks the caller may
  read. Citations show which chunks, but the wording is not verified.
- **Answer quality is not evaluated.** Tests use `FakeLLM`; `tests/live/` is a
  small opt-in sample.
- **No rate limiting and no request body size limit at the server.** See
  README "Known limitations".
- **HS256 shared secret and no revocation list.** Fine for a demo; production
  should use RS256/JWKS (ADR 0005).
- **SQL injection inside the API could call `set_config` and claim any
  identity.** All SQL is parameterised; RLS doesn't protect against code
  execution as `app_user`.
- **Data sent to the Anthropic API** (chunks the caller may read, plus the
  question) leaves this system, under Anthropic's data retention terms.
