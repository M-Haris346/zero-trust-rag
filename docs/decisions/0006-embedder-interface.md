# ADR 0006: Embedder interface, fake embedder, optional fastembed

**Status:** accepted

## Context
Reviewers should be able to run every test without an API key, a GPU, or a
large model download. But the demo should still be able to use real embeddings.

## Decision
- `app/rag/embedder.py` defines a small `Embedder` protocol:
  `dim: int` and `embed(texts) -> list[list[float]]`.
- `FastEmbedEmbedder` runs `BAAI/bge-small-en-v1.5` (384 dims) locally with
  fastembed. It is an **optional extra** (`uv sync --extra embeddings`) because
  it pulls in onnxruntime. If it is selected but not installed, startup fails
  with a message saying exactly that; `scripts/serve.py` prints it and exits 2.
- `FakeEmbedder` hashes each word to a fixed direction and sums them. It is
  deterministic: identical text gives an identical vector. **It has no
  semantic meaning.** Tests and CI use it; `EMBEDDER=fake` lets a demo run
  offline.
- Embedding runs in a thread pool and **before** the database transaction
  opens. Slow CPU work never holds a pooled connection, and the transaction
  only does fast inserts, so a failure rolls back the document and all its
  chunks together.
- The `vector(384)` column is the authoritative dimension check. A
  wrong-dimension embedding is a server bug: the insert fails, the whole upload
  rolls back, and the client gets a generic 500.

## What the tests can and cannot claim
The canary test (`tests/security/test_canary_leak.py`) plants a unique string
as the only content of a restricted document and searches for it exactly. It
proves **access control**: the string comes back for authorized callers and
never for anyone else. With `FakeEmbedder` it proves nothing about **retrieval
quality**. That needs real embeddings and an eval set (a later phase).
