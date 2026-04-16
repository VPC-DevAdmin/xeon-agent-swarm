"""
Client for the Hugging Face Text Embeddings Inference (TEI) service.

The `tei-embedding` service in docker-compose exposes an OpenAPI-style `/embed`
endpoint that takes {"inputs": ["text", ...]} and returns a list of vectors.
"""
from __future__ import annotations

import os

import httpx


class Embedder:
    """Async client for the TEI `/embed` endpoint."""

    def __init__(
        self,
        endpoint: str | None = None,
        model: str | None = None,
        dim: int | None = None,
        timeout_s: float = 30.0,
    ):
        self.endpoint = (endpoint or os.getenv("TEI_ENDPOINT", "http://tei-embedding:8090")).rstrip("/")
        self.model = model or os.getenv("EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")
        self.dim = dim if dim is not None else int(os.getenv("EMBEDDING_DIM", "384"))
        self._timeout_s = timeout_s

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """
        Batch-embed a list of texts. Returns one vector per input, in order.
        Empty input list returns an empty list.
        """
        if not texts:
            return []
        async with httpx.AsyncClient(timeout=self._timeout_s) as client:
            resp = await client.post(
                f"{self.endpoint}/embed",
                json={"inputs": texts, "truncate": True},
            )
            resp.raise_for_status()
            return resp.json()

    async def embed_one(self, text: str) -> list[float]:
        """Convenience wrapper for single-text embedding."""
        vecs = await self.embed_texts([text])
        return vecs[0]
