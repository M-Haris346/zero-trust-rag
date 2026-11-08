from __future__ import annotations

import importlib
import math
from types import ModuleType

import pytest

from app.rag import embedder as embedder_module
from app.rag.embedder import (
    EMBEDDING_DIM,
    EmbedderUnavailableError,
    FakeEmbedder,
    FastEmbedEmbedder,
    to_pgvector,
)


def _cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


def test_fake_embedder_shape_and_norm() -> None:
    [vector] = FakeEmbedder().embed(["hello world"])
    assert len(vector) == EMBEDDING_DIM
    assert math.isclose(math.sqrt(sum(x * x for x in vector)), 1.0, rel_tol=1e-9)


def test_fake_embedder_is_deterministic() -> None:
    assert FakeEmbedder().embed(["same text"]) == FakeEmbedder().embed(["same text"])


def test_fake_embedder_shared_words_are_closer() -> None:
    a, b, c = FakeEmbedder().embed(["red apple pie", "red apple tart", "network socket"])
    assert _cosine(a, b) > _cosine(a, c)


def test_pgvector_literal() -> None:
    assert to_pgvector([0.5, -1.0, 0.125]) == "[0.5,-1,0.125]"


def test_fastembed_missing_gives_install_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    def _missing(name: str) -> ModuleType:
        raise ImportError(name)

    monkeypatch.setattr(embedder_module.importlib, "import_module", _missing)
    with pytest.raises(EmbedderUnavailableError, match=r"uv sync --extra embeddings"):
        FastEmbedEmbedder()


def test_fastembed_real_model_if_installed() -> None:
    # Skipped in CI and wherever the optional extra is not installed.
    pytest.importorskip("fastembed")
    [vector] = FastEmbedEmbedder().embed(["hello"])
    assert len(vector) == EMBEDDING_DIM
    assert importlib.import_module("fastembed") is not None
