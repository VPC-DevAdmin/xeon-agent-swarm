"""
Wikipedia article downloader.

Uses the MediaWiki API (no API key required) to fetch plain-text
article extracts. Handles redirects automatically.
"""
from __future__ import annotations

import asyncio
import logging

import httpx

logger = logging.getLogger(__name__)

_WP_API = "https://en.wikipedia.org/w/api.php"
_TIMEOUT = 20.0
_HEADERS = {"User-Agent": "XeonAgentSwarm/1.0 (https://github.com/VPC-DevAdmin/xeon-agent-swarm; educational demo)"}


async def fetch_article(title: str, client: httpx.AsyncClient) -> dict | None:
    """
    Fetch the full plain-text extract for a Wikipedia article.

    Returns a dict with keys: title, text, source, word_count.
    Returns None if the article is missing or the extract is empty.
    """
    resp = await client.get(
        _WP_API,
        params={
            "action": "query",
            "titles": title,
            "prop": "extracts",
            "explaintext": True,
            "exsectionformat": "plain",
            "redirects": True,
            "format": "json",
        },
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()

    pages = data.get("query", {}).get("pages", {})
    page = next(iter(pages.values()))

    if "missing" in page:
        logger.warning("Wikipedia article not found: %r", title)
        return None

    resolved_title = page.get("title", title)
    text = page.get("extract", "").strip()
    if not text:
        logger.warning("Empty extract for article: %r", resolved_title)
        return None

    return {
        "title": resolved_title,
        "text": text,
        "source": f"https://en.wikipedia.org/wiki/{resolved_title.replace(' ', '_')}",
        "word_count": len(text.split()),
    }


async def fetch_articles(titles: list[str]) -> list[dict]:
    """
    Fetch multiple Wikipedia articles concurrently.

    Skips missing articles silently; logs a warning for each.
    Returns only successfully fetched articles in order.
    """
    async with httpx.AsyncClient(headers=_HEADERS) as client:
        tasks = [fetch_article(t, client) for t in titles]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    articles: list[dict] = []
    for title, result in zip(titles, results):
        if isinstance(result, Exception):
            logger.error("Failed to fetch %r: %s", title, result)
        elif result is not None:
            articles.append(result)
    return articles
