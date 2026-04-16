"""Corpus subsystem: embeddings and Redis-backed vector store."""
from backend.corpus.embedder import Embedder
from backend.corpus.redis_vectorstore import RedisVectorStore

__all__ = ["Embedder", "RedisVectorStore"]
