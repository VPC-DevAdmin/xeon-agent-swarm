"""
Wikipedia image downloader.

For each article title, fetches the primary thumbnail URL via the MediaWiki
pageimages API (no API key required), then downloads the image to the local
filesystem at IMAGE_DIR/{corpus_name}/{safe_title}.jpg.

Returns image metadata dicts suitable for indexing in RedisImageStore.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

_WP_API = "https://en.wikipedia.org/w/api.php"
_THUMB_SIZE = 800
_TIMEOUT = 30.0
IMAGE_DIR = Path(os.getenv("IMAGE_DIR", "/data/images"))


def _safe_filename(title: str) -> str:
    """Convert article title to a safe ASCII filename (no extension)."""
    return re.sub(r"[^\w\-]", "_", title).strip("_")


async def fetch_article_image(
    title: str,
    corpus_name: str,
    client: httpx.AsyncClient,
) -> dict | None:
    """
    Fetch and save the primary thumbnail for a Wikipedia article.

    Returns a metadata dict with keys:
      local_path   — path relative to IMAGE_DIR (e.g. "ai_hardware/Intel_Xeon.jpg")
      caption      — article title + image name (used for embedding)
      alt_text     — image filename on Wikimedia
      source_url   — original Wikimedia URL
      doc_title    — resolved Wikipedia article title
    Returns None if the article has no image or the download fails.
    """
    # Step 1: resolve thumbnail URL
    resp = await client.get(
        _WP_API,
        params={
            "action": "query",
            "titles": title,
            "prop": "pageimages",
            "piprop": "thumbnail|name",
            "pithumbsize": _THUMB_SIZE,
            "redirects": True,
            "format": "json",
        },
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    pages = data.get("query", {}).get("pages", {})
    page = next(iter(pages.values()))

    if "missing" in page or "thumbnail" not in page:
        logger.debug("No image for article: %r", title)
        return None

    resolved_title = page.get("title", title)
    thumb_url: str = page["thumbnail"]["source"]
    image_name: str = page.get("pageimage", "")

    # Step 2: download the image
    corpus_dir = IMAGE_DIR / corpus_name
    corpus_dir.mkdir(parents=True, exist_ok=True)

    safe = _safe_filename(resolved_title)
    dest = corpus_dir / f"{safe}.jpg"

    try:
        img_resp = await client.get(thumb_url, timeout=_TIMEOUT, follow_redirects=True)
        img_resp.raise_for_status()
        dest.write_bytes(img_resp.content)
    except Exception as exc:
        logger.warning("Failed to download image for %r: %s", resolved_title, exc)
        return None

    # Clean up the Wikimedia image name for use as alt text
    alt_text = image_name.replace("_", " ").rsplit(".", 1)[0] if image_name else resolved_title
    caption = f"{resolved_title} — {alt_text}" if alt_text != resolved_title else resolved_title
    local_path = f"{corpus_name}/{safe}.jpg"

    logger.info("Saved image: %s (%d bytes)", local_path, len(img_resp.content))
    return {
        "local_path": local_path,
        "caption": caption,
        "alt_text": alt_text,
        "source_url": thumb_url,
        "doc_title": resolved_title,
    }


async def fetch_corpus_images(titles: list[str], corpus_name: str) -> list[dict]:
    """
    Fetch images for all articles in a corpus concurrently.

    Skips articles with no image; logs but does not raise on download errors.
    Returns list of image metadata dicts (one per successfully saved image).
    """
    async with httpx.AsyncClient() as client:
        tasks = [fetch_article_image(t, corpus_name, client) for t in titles]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    images: list[dict] = []
    for title, result in zip(titles, results):
        if isinstance(result, Exception):
            logger.error("Image fetch error for %r: %s", title, result)
        elif result is not None:
            images.append(result)
    return images
