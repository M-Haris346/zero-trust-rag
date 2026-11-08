"""scripts/serve.py fails with a clear message instead of a traceback."""

from __future__ import annotations

import sys
from types import ModuleType

import pytest

from app.rag import embedder as embedder_module
from scripts import serve
from tests.api.client import JWT_SECRET
from tests.env import TEST_APP_DATABASE_URL


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    monkeypatch.setenv("APP_DATABASE_URL", TEST_APP_DATABASE_URL)
    monkeypatch.setenv("JWT_SECRET", JWT_SECRET)
    monkeypatch.setattr(sys, "argv", ["serve.py"])
    return monkeypatch


def test_missing_fastembed_prints_install_hint(
    env: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def _missing(name: str) -> ModuleType:
        raise ImportError(name)

    env.setenv("EMBEDDER", "fastembed")
    env.setattr(embedder_module.importlib, "import_module", _missing)

    assert serve.main() == 2
    err = capsys.readouterr().err
    assert "fastembed is not installed" in err
    assert "uv sync --extra embeddings" in err
    assert "EMBEDDER=fake" in err


def test_bad_config_prints_message(
    env: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    env.setenv("JWT_SECRET", "too-short")
    assert serve.main() == 2
    assert "JWT_SECRET must be at least 32 characters" in capsys.readouterr().err
