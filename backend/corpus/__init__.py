"""Corpus subsystem: text + image embeddings and Redis-backed vector stores."""
from backend.corpus.embedder import Embedder
from backend.corpus.redis_vectorstore import RedisVectorStore
from backend.corpus.redis_imagestore import RedisImageStore
from backend.corpus.chunker import chunk_text
from backend.corpus.downloader import fetch_articles
from backend.corpus.image_downloader import fetch_corpus_images

__all__ = [
    "Embedder",
    "RedisVectorStore",
    "RedisImageStore",
    "chunk_text",
    "fetch_articles",
    "fetch_corpus_images",
]
