"""Corpus subsystem: embeddings and Redis-backed vector store."""
from backend.corpus.embedder import Embedder
from backend.corpus.redis_vectorstore import RedisVectorStore
from backend.corpus.chunker import chunk_text
from backend.corpus.downloader import fetch_articles

__all__ = ["Embedder", "RedisVectorStore", "chunk_text", "fetch_articles"]
