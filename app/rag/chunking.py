"""Split document text into overlapping fixed-size character windows.

Deliberately simple and deterministic: the same input always produces the same
chunks. Smarter splitting (sentences, headings) can replace this later without
touching anything security-related.
"""

from __future__ import annotations

CHUNK_SIZE = 800
CHUNK_OVERLAP = 100


def chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    if size <= 0 or not 0 <= overlap < size:
        raise ValueError("need size > 0 and 0 <= overlap < size")
    text = text.strip()
    if not text:
        raise ValueError("text is empty")

    step = size - overlap
    chunks: list[str] = []
    for start in range(0, len(text), step):
        chunks.append(text[start : start + size])
        if start + size >= len(text):
            break
    return chunks
