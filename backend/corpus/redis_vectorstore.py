"""
Redis Stack vector store using RediSearch HNSW indexing.

One index per corpus. Chunks are stored as Redis Hashes keyed
`corpus:{corpus_name}:doc:{doc_id}:chunk:{chunk_id}`, with fields:
  text (TEXT), embedding (VECTOR HNSW), source (TAG),
  doc_title (TEXT), chunk_index (NUMERIC), token_count (NUMERIC).
"""
from __future__ import annotations

from typing import Any

import numpy as np
import redis.asyncio as aioredis
from redis.commands.search.field import NumericField, TagField, TextField, VectorField
from redis.commands.search.index_definition import IndexDefinition, IndexType
from redis.commands.search.query import Query
from redis.exceptions import ResponseError


def _to_str(val: Any) -> str:
    if isinstance(val, bytes):
        return val.decode("utf-8", errors="replace")
    return str(val) if val is not None else ""


class RedisVectorStore:
    """Per-corpus vector store backed by a single RediSearch HNSW index."""

    def __init__(self, redis_url: str, corpus_name: str, embedding_dim: int = 384):
        # decode_responses=False so we can read raw embedding bytes from Hashes.
        self.r = aioredis.from_url(redis_url, decode_responses=False)
        self.corpus = corpus_name
        self.dim = embedding_dim
        self.index_name = f"idx:{corpus_name}"
        self.key_prefix = f"corpus:{corpus_name}:"

    # ── Index lifecycle ─────────────────────────────────────────────────────

    async def index_exists(self) -> bool:
        try:
            await self.r.ft(self.index_name).info()
            return True
        except ResponseError:
            return False

    async def create_index(self) -> bool:
        """
        Create the HNSW vector index if it doesn't exist.
        Returns True if newly created, False if it already existed.
        """
        if await self.index_exists():
            return False

        schema = (
            TextField("text"),
            TagField("source"),
            TextField("doc_title"),
            NumericField("chunk_index"),
            NumericField("token_count"),
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
        """Drop the index. Optionally delete all indexed Hashes too."""
        try:
            await self.r.ft(self.index_name).dropindex(delete_documents=delete_documents)
        except ResponseError:
            pass

    # ── Writes ──────────────────────────────────────────────────────────────

    async def add_chunks(
        self,
        chunks: list[dict],
        embeddings: list[list[float]],
    ) -> int:
        """
        Batch-insert chunks with their embeddings.
        Each chunk dict must provide: doc_id, chunk_id, text.
        Optional: source, doc_title, chunk_index, token_count.
        Returns the number of chunks inserted.
        """
        if len(chunks) != len(embeddings):
            raise ValueError("chunks and embeddings must have equal length")
        if not chunks:
            return 0

        pipe = self.r.pipeline()
        for chunk, emb in zip(chunks, embeddings):
            if len(emb) != self.dim:
                raise ValueError(
                    f"embedding dim {len(emb)} != store dim {self.dim}"
                )
            key = f"{self.key_prefix}doc:{chunk['doc_id']}:chunk:{chunk['chunk_id']}"
            pipe.hset(
                key,
                mapping={
                    "text": chunk["text"],
                    "source": chunk.get("source", ""),
                    "doc_title": chunk.get("doc_title", ""),
                    "chunk_index": int(chunk.get("chunk_index", 0)),
                    "token_count": int(chunk.get("token_count", 0)),
                    "embedding": np.asarray(emb, dtype=np.float32).tobytes(),
                },
            )
        await pipe.execute()
        return len(chunks)

    # ── Reads ───────────────────────────────────────────────────────────────

    async def search(
        self,
        query_embedding: list[float],
        top_k: int = 5,
    ) -> list[dict]:
        """KNN vector search. Returns hits sorted by ascending cosine distance."""
        if len(query_embedding) != self.dim:
            raise ValueError(
                f"query embedding dim {len(query_embedding)} != store dim {self.dim}"
            )
        query_vec = np.asarray(query_embedding, dtype=np.float32).tobytes()
        q = (
            Query(f"*=>[KNN {top_k} @embedding $query_vec AS score]")
            .sort_by("score")
            .return_fields(
                "text", "source", "doc_title", "chunk_index", "token_count", "score"
            )
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
                    "text": _to_str(getattr(doc, "text", "")),
                    "source": _to_str(getattr(doc, "source", "")),
                    "doc_title": _to_str(getattr(doc, "doc_title", "")),
                    "chunk_index": int(getattr(doc, "chunk_index", 0) or 0),
                    "token_count": int(getattr(doc, "token_count", 0) or 0),
                    "score": float(getattr(doc, "score", 0.0)),
                }
            )
        return hits

    async def stats(self) -> dict:
        """Summary stats for the corpus (chunk count, unique doc count)."""
        if not await self.index_exists():
            return {
                "corpus": self.corpus,
                "exists": False,
                "num_chunks": 0,
                "num_docs": 0,
            }

        info = await self.r.ft(self.index_name).info()
        # info may come back as a list of k/v or as a dict depending on client version.
        info_dict: dict[Any, Any]
        if isinstance(info, dict):
            info_dict = info
        else:
            info_dict = {}
            for i in range(0, len(info), 2):
                info_dict[_to_str(info[i])] = info[i + 1] if i + 1 < len(info) else None

        num_chunks = int(_to_str(info_dict.get("num_docs", info_dict.get(b"num_docs", 0))) or 0)

        doc_ids: set[str] = set()
        async for key in self.r.scan_iter(
            match=f"{self.key_prefix}doc:*:chunk:*", count=1000
        ):
            key_str = _to_str(key)
            # corpus:<name>:doc:<doc_id>:chunk:<chunk_id>
            parts = key_str.split(":")
            if len(parts) >= 6:
                doc_ids.add(parts[3])
        return {
            "corpus": self.corpus,
            "exists": True,
            "num_chunks": num_chunks,
            "num_docs": len(doc_ids),
        }

    async def close(self) -> None:
        await self.r.aclose()
