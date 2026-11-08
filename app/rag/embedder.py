"""Text -> vector. One interface, two implementations (ADR 0006).

`embed` is synchronous (fastembed is CPU-bound). Callers run it in a thread
pool, and *before* opening a database transaction, so no pooled connection is
held while the CPU works.
"""

from __future__ import annotations

import hashlib
import importlib
import math
from collections.abc import Sequence
from typing import Any, Protocol

from app.config import Settings

EMBEDDING_DIM = 384  # must match chunks.embedding vector(384)
FASTEMBED_MODEL = "BAAI/bge-small-en-v1.5"


class Embedder(Protocol):
    dim: int

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class EmbedderUnavailableError(RuntimeError):
    pass


class FakeEmbedder:
    """Deterministic hash embedding. No semantic meaning at all.

    Each lower-cased word is hashed into a fixed pseudo-random direction and the
    directions are summed. Identical text gives an identical vector (distance 0)
    and texts sharing words land closer together. Good enough to test access
    control and plumbing; useless for judging retrieval quality.
    """

    dim = EMBEDDING_DIM

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed_one(t) for t in texts]

    def _embed_one(self, text: str) -> list[float]:
        total = [0.0] * self.dim
        for word in text.lower().split() or [""]:
            for i, value in enumerate(_word_direction(word, self.dim)):
                total[i] += value
        norm = math.sqrt(sum(x * x for x in total)) or 1.0
        return [x / norm for x in total]


def _word_direction(word: str, dim: int) -> list[float]:
    out: list[float] = []
    counter = 0
    while len(out) < dim:
        digest = hashlib.blake2b(f"{counter}:{word}".encode(), digest_size=64).digest()
        out.extend((b - 127.5) / 127.5 for b in digest)
        counter += 1
    return out[:dim]


class FastEmbedEmbedder:
    """Local ONNX model via fastembed. Optional extra: `uv sync --extra embeddings`."""

    dim = EMBEDDING_DIM

    def __init__(self, model_name: str = FASTEMBED_MODEL) -> None:
        try:
            # Imported dynamically: fastembed is an optional dependency, and
            # the rest of the app (and mypy) must work without it installed.
            fastembed = importlib.import_module("fastembed")
        except ImportError as exc:
            raise EmbedderUnavailableError(
                "fastembed is not installed. Run: uv sync --extra embeddings "
                "(or set EMBEDDER=fake for a demo without real embeddings)"
            ) from exc
        self._model: Any = fastembed.TextEmbedding(model_name=model_name)

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [[float(x) for x in vector] for vector in self._model.embed(list(texts))]


def build_embedder(settings: Settings) -> Embedder:
    if settings.embedder == "fake":
        return FakeEmbedder()
    return FastEmbedEmbedder()


def to_pgvector(values: Sequence[float]) -> str:
    """pgvector's text input format: '[0.1,0.2,...]'."""
    return "[" + ",".join(format(v, ".8g") for v in values) + "]"
