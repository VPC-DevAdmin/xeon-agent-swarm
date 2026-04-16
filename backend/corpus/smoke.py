"""
Smoke test for the corpus subsystem: TEI embedder + Redis Stack vector store.

Runs inside the backend container so it picks up the same REDIS_URL / TEI_ENDPOINT
the production code will use.

Usage:
  docker compose exec backend python -m backend.corpus.smoke

What it does:
  1. Creates a throwaway corpus (random suffix) so repeated runs don't collide
  2. Embeds ~10 sample sentences via TEI
  3. Inserts them into the vector store
  4. Runs 3 semantic search queries and prints the top hits
  5. Drops the index (and chunks) when done

Exit code:
  0 on success; non-zero if embedding or search fails.
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid

from backend.corpus.embedder import Embedder
from backend.corpus.redis_vectorstore import RedisVectorStore


SAMPLE_TEXTS: list[str] = [
    "Intel Xeon Scalable processors support Advanced Matrix Extensions (AMX) for accelerated AI inference.",
    "AMD EPYC processors offer up to 128 Zen 4 cores aimed at data center workloads.",
    "NVIDIA H100 GPUs provide 80GB of HBM3 memory and roughly 3TB/s of memory bandwidth.",
    "The Python programming language was created by Guido van Rossum and first released in 1991.",
    "Redis is an in-memory data store often used as a cache, message broker, and vector database.",
    "Large language models are trained on massive text corpora using transformer architectures.",
    "OpenVINO is Intel's toolkit for optimizing and deploying deep-learning inference on Intel hardware.",
    "vLLM is a high-throughput LLM inference engine that uses PagedAttention to manage KV cache.",
    "The BAAI/bge-small-en-v1.5 embedding model produces 384-dimensional English sentence vectors.",
    "Retrieval-augmented generation combines vector search with language models for grounded answers.",
]

QUERIES: list[str] = [
    "What AI matrix accelerator does Intel provide?",
    "Who created Python and when?",
    "How does retrieval help language models give grounded answers?",
]


async def main() -> int:
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6479")
    tei_endpoint = os.getenv("TEI_ENDPOINT", "http://localhost:8090")
    emb_dim = int(os.getenv("EMBEDDING_DIM", "384"))
    corpus = f"smoke_{uuid.uuid4().hex[:8]}"

    print(f"redis_url     = {redis_url}")
    print(f"tei_endpoint  = {tei_endpoint}")
    print(f"embedding_dim = {emb_dim}")
    print(f"corpus        = {corpus}")
    print()

    store = RedisVectorStore(redis_url=redis_url, corpus_name=corpus, embedding_dim=emb_dim)
    embedder = Embedder(endpoint=tei_endpoint, dim=emb_dim)

    try:
        print(f"Creating index idx:{corpus} ...")
        created = await store.create_index()
        print(f"  created = {created}")

        print(f"Embedding {len(SAMPLE_TEXTS)} sample texts ...")
        embeddings = await embedder.embed_texts(SAMPLE_TEXTS)
        print(f"  got {len(embeddings)} vectors, dim = {len(embeddings[0])}")
        if len(embeddings[0]) != emb_dim:
            print(
                f"  !! expected dim {emb_dim}; override EMBEDDING_DIM or the "
                f"TEI model to match",
                file=sys.stderr,
            )
            return 2

        chunks = [
            {
                "doc_id": f"doc{i:02d}",
                "chunk_id": "0",
                "text": text,
                "source": "smoke-test",
                "doc_title": f"Sample {i}",
                "chunk_index": 0,
                "token_count": len(text.split()),
            }
            for i, text in enumerate(SAMPLE_TEXTS)
        ]
        print("Inserting chunks ...")
        n = await store.add_chunks(chunks, embeddings)
        print(f"  inserted = {n}")

        print("\nStats:")
        print(f"  {await store.stats()}")

        for q in QUERIES:
            print(f"\nQuery: {q!r}")
            q_emb = await embedder.embed_one(q)
            hits = await store.search(q_emb, top_k=3)
            for h in hits:
                snippet = h["text"][:90] + ("…" if len(h["text"]) > 90 else "")
                print(f"  [{h['score']:.4f}] {h['doc_title']}: {snippet}")

        print("\nCleaning up (dropping index + chunks) ...")
        await store.drop_index(delete_documents=True)
        print("  done.")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"\nSMOKE FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        # Best-effort cleanup even on failure
        try:
            await store.drop_index(delete_documents=True)
        except Exception:  # noqa: BLE001
            pass
        return 1
    finally:
        await store.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
