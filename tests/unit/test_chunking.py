from __future__ import annotations

import pytest

from app.rag.chunking import chunk_text


def test_short_text_is_one_chunk() -> None:
    assert chunk_text("  hello world  ") == ["hello world"]


def test_deterministic() -> None:
    text = "abcdefghij" * 300
    assert chunk_text(text) == chunk_text(text)


def test_windows_overlap_and_cover_the_text() -> None:
    text = "".join(chr(ord("a") + i % 26) for i in range(2_500))
    chunks = chunk_text(text, size=800, overlap=100)
    assert [len(c) for c in chunks] == [800, 800, 800, 400]
    for left, right in zip(chunks, chunks[1:], strict=False):
        assert left[-100:] == right[:100]
    # Dropping each overlap reconstructs the original exactly.
    assert chunks[0] + "".join(c[100:] for c in chunks[1:]) == text


@pytest.mark.parametrize("text", ["", "   ", "\n\t "])
def test_blank_text_rejected(text: str) -> None:
    with pytest.raises(ValueError, match="empty"):
        chunk_text(text)


@pytest.mark.parametrize(("size", "overlap"), [(0, 0), (10, 10), (10, -1)])
def test_invalid_window_rejected(size: int, overlap: int) -> None:
    with pytest.raises(ValueError):
        chunk_text("text", size=size, overlap=overlap)
