"""
Corpus ingestion pipeline: download → chunk → embed → upsert.

Can be run as a CLI to seed one or all predefined corpora:

  # Seed a single corpus (text only)
  docker compose exec backend python -m backend.corpus.ingester ai_hardware

  # Seed all corpora including Wikipedia images
  docker compose exec backend python -m backend.corpus.ingester --all --images

  # Seed all corpora including PDF-extracted images (architecture diagrams,
  # benchmark charts, comparison tables — used by the vision worker)
  docker compose exec backend python -m backend.corpus.ingester --all --pdfs

  # Full ingest: text + Wikipedia images + PDF images
  docker compose exec backend python -m backend.corpus.ingester --all --images --pdfs

  # Seed from a custom list of Wikipedia titles
  docker compose exec backend python -m backend.corpus.ingester my_corpus \\
      --titles "Intel Xeon" "AMD EPYC" "NVIDIA H100"
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

from backend.corpus.chunker import chunk_text
from backend.corpus.downloader import fetch_articles
from backend.corpus.embedder import Embedder
from backend.corpus.image_downloader import fetch_corpus_images
from backend.corpus.pdf_ingester import ingest_pdf_images
from backend.corpus.redis_imagestore import RedisImageStore
from backend.corpus.redis_vectorstore import RedisVectorStore
from backend.corpus.seed_data import CORPORA

logger = logging.getLogger(__name__)

_EMBED_BATCH = 32  # max texts per TEI /embed call


async def ingest_corpus(
    corpus_name: str,
    wikipedia_titles: list[str],
    embedder: Embedder,
    store: RedisVectorStore,
    chunk_words: int = 400,
    overlap_words: int = 60,
) -> dict:
    """
    Full ingestion pipeline for a single corpus.

    1. Fetch Wikipedia articles
    2. Chunk each article
    3. Batch-embed chunks via TEI
    4. Upsert to Redis vector store

    Returns a summary dict with article_count, chunk_count, skipped.
    """
    print(f"\n[{corpus_name}] Fetching {len(wikipedia_titles)} Wikipedia articles …")
    articles = await fetch_articles(wikipedia_titles)
    skipped = len(wikipedia_titles) - len(articles)
    print(f"[{corpus_name}] Retrieved {len(articles)} articles ({skipped} skipped)")

    if not articles:
        return {"corpus": corpus_name, "article_count": 0, "chunk_count": 0, "skipped": skipped}

    # Build flat list of (chunk_dict, text) pairs
    chunk_dicts: list[dict] = []
    chunk_texts: list[str] = []

    for art_idx, art in enumerate(articles):
        chunks = chunk_text(art["text"], chunk_words=chunk_words, overlap_words=overlap_words)
        doc_id = f"art{art_idx:03d}"
        for chunk_idx, chunk in enumerate(chunks):
            chunk_dicts.append(
                {
                    "doc_id": doc_id,
                    "chunk_id": str(chunk_idx),
                    "text": chunk,
                    "source": art["source"],
                    "doc_title": art["title"],
                    "chunk_index": chunk_idx,
                    "token_count": len(chunk.split()),
                }
            )
            chunk_texts.append(chunk)

    total_chunks = len(chunk_texts)
    print(f"[{corpus_name}] {total_chunks} chunks across {len(articles)} articles; embedding …")

    # Ensure index exists
    created = await store.create_index()
    if created:
        print(f"[{corpus_name}] Created index idx:{corpus_name}")
    else:
        print(f"[{corpus_name}] Index idx:{corpus_name} already exists — appending")

    # Embed and upsert in batches
    inserted = 0
    for i in range(0, total_chunks, _EMBED_BATCH):
        batch_texts = chunk_texts[i : i + _EMBED_BATCH]
        batch_dicts = chunk_dicts[i : i + _EMBED_BATCH]
        embeddings = await embedder.embed_texts(batch_texts)
        n = await store.add_chunks(batch_dicts, embeddings)
        inserted += n
        pct = int(100 * (i + len(batch_texts)) / total_chunks)
        print(f"[{corpus_name}]   {pct:3d}% — {inserted}/{total_chunks} chunks inserted", end="\r")

    print(f"\n[{corpus_name}] Done. {inserted} chunks inserted.")
    return {
        "corpus": corpus_name,
        "article_count": len(articles),
        "chunk_count": inserted,
        "skipped": skipped,
    }


async def ingest_images(
    corpus_name: str,
    wikipedia_titles: list[str],
    embedder: Embedder,
    image_store: RedisImageStore,
) -> dict:
    """
    Image ingestion pipeline for a single corpus.

    1. Download primary Wikipedia thumbnails for each article
    2. Embed captions via TEI
    3. Upsert to Redis image store

    Returns summary dict with image_count, skipped.
    """
    print(f"\n[{corpus_name}:images] Downloading images for {len(wikipedia_titles)} articles …")
    images = await fetch_corpus_images(wikipedia_titles, corpus_name)
    skipped = len(wikipedia_titles) - len(images)
    print(f"[{corpus_name}:images] Downloaded {len(images)} images ({skipped} had none)")

    if not images:
        return {"corpus": corpus_name, "image_count": 0, "skipped": skipped}

    created = await image_store.create_index()
    if created:
        print(f"[{corpus_name}:images] Created index idx:images:{corpus_name}")

    captions = [img["caption"] for img in images]
    embeddings = await embedder.embed_texts(captions)
    n = await image_store.add_images(images, embeddings)
    print(f"[{corpus_name}:images] Done. {n} images indexed.")
    return {"corpus": corpus_name, "image_count": n, "skipped": skipped}


async def main() -> int:
    logging.basicConfig(level=logging.WARNING)

    parser = argparse.ArgumentParser(description="Seed corpus vector stores from Wikipedia")
    parser.add_argument(
        "corpus",
        nargs="?",
        help="Corpus name to seed (must be a key in seed_data.CORPORA, or use --all)",
    )
    parser.add_argument("--all", action="store_true", help="Seed all predefined corpora")
    parser.add_argument(
        "--titles",
        nargs="+",
        metavar="TITLE",
        help="Custom Wikipedia titles (requires positional corpus name)",
    )
    parser.add_argument("--drop", action="store_true", help="Drop existing index before seeding")
    parser.add_argument("--images", action="store_true", help="Also download and index Wikipedia thumbnail images")
    parser.add_argument(
        "--pdfs",
        action="store_true",
        help="Extract and index images from curated PDFs (config/corpus_pdfs.yaml) — "
             "architecture diagrams, benchmark charts, comparison tables for the vision worker",
    )
    args = parser.parse_args()

    redis_url = os.getenv("REDIS_URL", "redis://localhost:6479")
    tei_endpoint = os.getenv("TEI_ENDPOINT", "http://tei-embedding:8090")
    emb_dim = int(os.getenv("EMBEDDING_DIM", "384"))

    embedder = Embedder(endpoint=tei_endpoint, dim=emb_dim)

    corpora_to_seed: dict[str, list[str]] = {}

    if args.all:
        for name, meta in CORPORA.items():
            corpora_to_seed[name] = meta["wikipedia_titles"]
    elif args.corpus:
        if args.titles:
            corpora_to_seed[args.corpus] = args.titles
        elif args.corpus in CORPORA:
            corpora_to_seed[args.corpus] = CORPORA[args.corpus]["wikipedia_titles"]
        else:
            known = ", ".join(CORPORA)
            print(
                f"Unknown corpus {args.corpus!r}. Known: {known}. "
                f"Pass --titles to use a custom list.",
                file=sys.stderr,
            )
            return 1
    else:
        parser.print_help()
        return 1

    summaries = []
    for corpus_name, titles in corpora_to_seed.items():
        store = RedisVectorStore(redis_url=redis_url, corpus_name=corpus_name, embedding_dim=emb_dim)
        try:
            if args.drop:
                print(f"[{corpus_name}] Dropping existing text index …")
                await store.drop_index(delete_documents=True)
            summary = await ingest_corpus(corpus_name, titles, embedder, store)
            summaries.append(summary)
        finally:
            await store.close()

        if args.images or args.pdfs:
            image_store = RedisImageStore(redis_url=redis_url, corpus_name=corpus_name, embedding_dim=emb_dim)
            try:
                if args.drop:
                    print(f"[{corpus_name}] Dropping existing image index …")
                    await image_store.drop_index(delete_documents=True)

                wiki_img_count = 0
                if args.images:
                    img_summary = await ingest_images(corpus_name, titles, embedder, image_store)
                    wiki_img_count = img_summary["image_count"]

                pdf_img_count = 0
                if args.pdfs:
                    pdf_summary = await ingest_pdf_images(corpus_name, embedder, image_store)
                    pdf_img_count = pdf_summary["image_count"]
                    if pdf_summary["skipped_pdfs"]:
                        print(
                            f"[{corpus_name}:pdfs] Warning: {pdf_summary['skipped_pdfs']} "
                            f"PDF(s) failed to download"
                        )

                summaries[-1]["image_count"] = wiki_img_count + pdf_img_count
                summaries[-1]["pdf_image_count"] = pdf_img_count
            finally:
                await image_store.close()

    print("\n── Summary ─────────────────────────────────────────")
    for s in summaries:
        img_info = ""
        if args.images or args.pdfs:
            total_imgs = s.get("image_count", 0)
            pdf_imgs   = s.get("pdf_image_count", 0)
            wiki_imgs  = total_imgs - pdf_imgs
            parts = []
            if args.images:
                parts.append(f"wiki={wiki_imgs}")
            if args.pdfs:
                parts.append(f"pdf={pdf_imgs}")
            img_info = f"  images={total_imgs} ({', '.join(parts)})"
        print(
            f"  {s['corpus']:20s}  articles={s['article_count']:3d}"
            f"  chunks={s['chunk_count']:5d}  skipped={s['skipped']}{img_info}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
