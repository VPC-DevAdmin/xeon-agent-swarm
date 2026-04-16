"""
FastAPI router for corpus management and semantic search.

Endpoints (all under /corpus prefix):
  GET  /corpus                         — list all corpora + stats
  GET  /corpus/{name}/stats            — stats for one corpus
  POST /corpus/{name}/seed             — seed from predefined Wikipedia list
  POST /corpus/{name}/ingest           — seed from custom Wikipedia titles
  GET  /corpus/{name}/search           — semantic search (?q=...&top_k=5)
  DELETE /corpus/{name}                — drop index + documents
"""
from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from backend.corpus.embedder import Embedder
from backend.corpus.ingester import ingest_corpus, ingest_images
from backend.corpus.redis_imagestore import RedisImageStore
from backend.corpus.redis_vectorstore import RedisVectorStore
from backend.corpus.seed_data import CORPORA

router = APIRouter(prefix="/corpus", tags=["corpus"])

# ── Shared helpers ────────────────────────────────────────────────────────────


def _embedder() -> Embedder:
    return Embedder(
        endpoint=os.getenv("TEI_ENDPOINT", "http://tei-embedding:8090"),
        dim=int(os.getenv("EMBEDDING_DIM", "384")),
    )


def _store(corpus_name: str) -> RedisVectorStore:
    return RedisVectorStore(
        redis_url=os.getenv("REDIS_URL", "redis://localhost:6479"),
        corpus_name=corpus_name,
        embedding_dim=int(os.getenv("EMBEDDING_DIM", "384")),
    )


def _image_store(corpus_name: str) -> RedisImageStore:
    return RedisImageStore(
        redis_url=os.getenv("REDIS_URL", "redis://localhost:6479"),
        corpus_name=corpus_name,
        embedding_dim=int(os.getenv("EMBEDDING_DIM", "384")),
    )


# ── Request / response models ─────────────────────────────────────────────────


class IngestRequest(BaseModel):
    wikipedia_titles: list[str]
    drop_existing: bool = False
    include_images: bool = False


class SeedRequest(BaseModel):
    drop_existing: bool = False
    include_images: bool = False


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.get("")
async def list_corpora():
    """Return all known corpora with descriptions and live stats (text + images)."""
    results = []
    for name, meta in CORPORA.items():
        store = _store(name)
        istore = _image_store(name)
        try:
            stats = await store.stats()
            image_count = await istore.count()
        finally:
            await store.close()
            await istore.close()
        results.append(
            {
                "name": name,
                "description": meta["description"],
                "article_titles": len(meta["wikipedia_titles"]),
                "image_count": image_count,
                **stats,
            }
        )
    return {"corpora": results}


@router.get("/{name}/stats")
async def corpus_stats(name: str):
    """Stats for a single corpus (chunk count, doc count, exists flag)."""
    store = _store(name)
    try:
        return await store.stats()
    finally:
        await store.close()


@router.post("/{name}/seed")
async def seed_corpus(name: str, req: SeedRequest = SeedRequest()):
    """
    Seed a corpus from its predefined Wikipedia article list.
    Set drop_existing=true to rebuild from scratch.
    """
    if name not in CORPORA:
        known = list(CORPORA.keys())
        raise HTTPException(status_code=404, detail=f"Unknown corpus {name!r}. Known: {known}")

    titles = CORPORA[name]["wikipedia_titles"]
    store = _store(name)
    embedder = _embedder()
    try:
        if req.drop_existing:
            await store.drop_index(delete_documents=True)
        summary = await ingest_corpus(name, titles, embedder, store)
    finally:
        await store.close()

    if req.include_images:
        istore = _image_store(name)
        try:
            if req.drop_existing:
                await istore.drop_index(delete_documents=True)
            img_summary = await ingest_images(name, titles, embedder, istore)
            summary["image_count"] = img_summary["image_count"]
        finally:
            await istore.close()

    return summary


@router.post("/{name}/ingest")
async def ingest_custom(name: str, req: IngestRequest):
    """
    Ingest a custom list of Wikipedia article titles into a corpus.
    Useful for adding articles to an existing corpus.
    """
    if not req.wikipedia_titles:
        raise HTTPException(status_code=422, detail="wikipedia_titles must not be empty")

    store = _store(name)
    embedder = _embedder()
    try:
        if req.drop_existing:
            await store.drop_index(delete_documents=True)
        summary = await ingest_corpus(name, req.wikipedia_titles, embedder, store)
    finally:
        await store.close()

    if req.include_images:
        istore = _image_store(name)
        try:
            if req.drop_existing:
                await istore.drop_index(delete_documents=True)
            img_summary = await ingest_images(name, req.wikipedia_titles, embedder, istore)
            summary["image_count"] = img_summary["image_count"]
        finally:
            await istore.close()

    return summary


@router.get("/{name}/search")
async def search_corpus(
    name: str,
    q: str = Query(..., description="Search query"),
    top_k: int = Query(5, ge=1, le=20),
):
    """Semantic search within a corpus. Returns top_k nearest chunks."""
    store = _store(name)
    embedder = _embedder()
    try:
        if not await store.index_exists():
            raise HTTPException(status_code=404, detail=f"Corpus {name!r} not found or empty")
        query_vec = await embedder.embed_one(q)
        hits = await store.search(query_vec, top_k=top_k)
    finally:
        await store.close()
    return {"corpus": name, "query": q, "hits": hits}


@router.get("/{name}/images/search")
async def search_images(
    name: str,
    q: str = Query(..., description="Search query"),
    top_k: int = Query(2, ge=1, le=10),
):
    """Semantic search over image captions within a corpus."""
    istore = _image_store(name)
    embedder = _embedder()
    try:
        if not await istore.index_exists():
            raise HTTPException(status_code=404, detail=f"No image index for corpus {name!r}")
        query_vec = await embedder.embed_one(q)
        hits = await istore.search(query_vec, top_k=top_k)
    finally:
        await istore.close()
    return {"corpus": name, "query": q, "hits": hits}


@router.delete("/{name}")
async def drop_corpus(name: str):
    """Drop the corpus text + image indexes and all their documents."""
    store = _store(name)
    istore = _image_store(name)
    try:
        await store.drop_index(delete_documents=True)
        await istore.drop_index(delete_documents=True)
    finally:
        await store.close()
        await istore.close()
    return {"corpus": name, "dropped": True}
