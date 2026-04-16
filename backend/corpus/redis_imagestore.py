"""
Redis Stack vector store for image metadata.

One index per corpus. Images are stored as Redis Hashes keyed
`images:{corpus_name}:{image_id}`, with fields:
  caption (TEXT + VECTOR HNSW), alt_text (TEXT), doc_title (TEXT),
  local_path (TEXT), source_url (TEXT), corpus (TAG).

Semantic search is over caption embeddings, enabling queries like
"AMX matrix accelerator chip die shot" to retrieve a photo of an
Intel Xeon processor die.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import redis.asyncio as aioredis
from redis.commands.search.field import TagField, TextField, VectorField
from redis.commands.search.index_definition import IndexDefinition, IndexType
from redis.commands.search.query import Query
from redis.exceptions import ResponseError


def _to_str(val: Any) -> str:
    if isinstance(val, bytes):
        return val.decode("utf-8", errors="replace")
    return str(val) if val is not None else ""


class RedisImageStore:
    """Per-corpus image metadata store backed by a RediSearch HNSW index."""

    def __init__(self, redis_url: str, corpus_name: str, embedding_dim: int = 384):
        self.r = aioredis.from_url(redis_url, decode_responses=False)
        self.corpus = corpus_name
        self.dim = embedding_dim
        self.index_name = f"idx:images:{corpus_name}"
        self.key_prefix = f"images:{corpus_name}:"

    # ── Index lifecycle ─────────────────────────────────────────────────────

    async def index_exists(self) -> bool:
        try:
            await self.r.ft(self.index_name).info()
            return True
        except ResponseError:
            return False

    async def create_index(self) -> bool:
        """Create the HNSW image index if it doesn't exist. Returns True if created."""
        if await self.index_exists():
            return False

        schema = (
            TextField("caption"),
            TextField("alt_text"),
            TextField("doc_title"),
            TextField("local_path"),
            TagField("corpus"),
            VectorField(
                "embedding",
                "HNSW",
                {
                    "TYPE": "FLOAT32",
                    "DIM": self.dim,
                    "DISTANCE_METRIC": "COSINE",
                    "M": 16,
                    "EF_CONSTRUCTION": 200,
                },
            ),
        )
        definition = IndexDefinition(
            prefix=[self.key_prefix],
            index_type=IndexType.HASH,
        )
        await self.r.ft(self.index_name).create_index(schema, definition=definition)
        return True

    async def drop_index(self, delete_documents: bool = True) -> None:
        try:
            await self.r.ft(self.index_name).dropindex(delete_documents=delete_documents)
        except ResponseError:
            pass

    # ── Writes ──────────────────────────────────────────────────────────────

    async def add_images(
        self,
        images: list[dict],
        embeddings: list[list[float]],
    ) -> int:
        """
        Insert image metadata records with caption embeddings.

        Each image dict must provide: local_path, caption, doc_title.
        Optional: alt_text, source_url.
        Returns number of records inserted.
        """
        if len(images) != len(embeddings):
            raise ValueError("images and embeddings must have equal length")
        if not images:
            return 0

        pipe = self.r.pipeline()
        for img, emb in zip(images, embeddings):
            if len(emb) != self.dim:
                raise ValueError(f"embedding dim {len(emb)} != store dim {self.dim}")
            # Use local_path as the unique key (one image per article per corpus)
            safe_key = img["local_path"].replace("/", "_").replace(".", "_")
            key = f"{self.key_prefix}{safe_key}"
            pipe.hset(
                key,
                mapping={
                    "caption": img["caption"],
                    "alt_text": img.get("alt_text", ""),
                    "doc_title": img.get("doc_title", ""),
                    "local_path": img["local_path"],
                    "source_url": img.get("source_url", ""),
                    "corpus": self.corpus,
                    "embedding": np.asarray(emb, dtype=np.float32).tobytes(),
                },
            )
        await pipe.execute()
        return len(images)

    # ── Reads ────────────────────────────────────────────────────────────────

    async def search(
        self,
        query_embedding: list[float],
        top_k: int = 2,
    ) -> list[dict]:
        """KNN search over caption embeddings. Returns hits sorted by score."""
        if len(query_embedding) != self.dim:
            raise ValueError(
                f"query dim {len(query_embedding)} != store dim {self.dim}"
            )
        query_vec = np.asarray(query_embedding, dtype=np.float32).tobytes()
        q = (
            Query(f"*=>[KNN {top_k} @embedding $query_vec AS score]")
            .sort_by("score")
            .return_fields("caption", "alt_text", "doc_title", "local_path", "source_url", "score")
            .paging(0, top_k)
            .dialect(2)
        )
        res = await self.r.ft(self.index_name).search(
            q, query_params={"query_vec": query_vec}
        )
        hits: list[dict] = []
        for doc in res.docs:
            hits.append(
                {
                    "key": _to_str(doc.id),
                    "caption": _to_str(getattr(doc, "caption", "")),
                    "alt_text": _to_str(getattr(doc, "alt_text", "")),
                    "doc_title": _to_str(getattr(doc, "doc_title", "")),
                    "local_path": _to_str(getattr(doc, "local_path", "")),
                    "source_url": _to_str(getattr(doc, "source_url", "")),
                    "score": float(getattr(doc, "score", 0.0)),
                }
            )
        return hits

    async def count(self) -> int:
        """Return number of images indexed."""
        if not await self.index_exists():
            return 0
        info = await self.r.ft(self.index_name).info()
        if isinstance(info, dict):
            return int(_to_str(info.get("num_docs", 0)) or 0)
        info_dict: dict = {}
        for i in range(0, len(info), 2):
            info_dict[_to_str(info[i])] = info[i + 1] if i + 1 < len(info) else None
        return int(_to_str(info_dict.get("num_docs", 0)) or 0)

    async def close(self) -> None:
        await self.r.aclose()
