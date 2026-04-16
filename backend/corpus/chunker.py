"""
Sliding-window text chunker.

Splits plain text into overlapping word-count-based chunks so that
each chunk fits comfortably within the TEI embedding model's 512-token
context window (400 words ≈ 500 tokens for English prose).
"""
from __future__ import annotations


def chunk_text(
    text: str,
    chunk_words: int = 400,
    overlap_words: int = 60,
) -> list[str]:
    """
    Split *text* into overlapping chunks of ~chunk_words words.

    Returns an empty list for blank input.
    Chunks are stripped; empty chunks are skipped.
    """
    words = text.split()
    if not words:
        return []

    chunks: list[str] = []
    start = 0
    while start < len(words):
        end = min(start + chunk_words, len(words))
        chunk = " ".join(words[start:end]).strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(words):
            break
        start += chunk_words - overlap_words
    return chunks
