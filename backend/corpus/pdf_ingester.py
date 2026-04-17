"""
PDF image extractor for the corpus.

Downloads technical PDFs (arXiv papers, whitepapers) from the curated list
in config/corpus_pdfs.yaml, extracts meaningful images — architecture block
diagrams, benchmark charts, comparison tables — and indexes them in the Redis
image store alongside Wikipedia thumbnail images.

The extracted images feed the vision worker (Phi-3.5-vision via vllm-vision),
which retrieves them by caption embedding similarity and describes what it sees.
This is the core of the vision demo: the VLM can extract specific data points
from a bar chart or describe structural relationships in an architecture diagram,
producing signal the single-model text baseline cannot recover from captions alone.

Image filtering:
  - Minimum 200×200 pixels (skips icons, logos, tiny rule decorations)
  - Aspect ratio 0.2–5.0 (skips horizontal rules and full-width banner stripes)
  - Content-hash deduplication within each PDF

Caption extraction priority:
  1. "Figure N: …" text found on the same page (explicit figure caption)
  2. Nearest bold/uppercase section heading above the image
  3. PDF description from YAML config + page number

Usage (via ingester CLI):
  docker compose exec backend python -m backend.corpus.ingester --all --pdfs
  docker compose exec backend python -m backend.corpus.ingester ai_hardware --pdfs
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
from pathlib import Path
from typing import Iterator

import httpx
import yaml

logger = logging.getLogger(__name__)

# ── Runtime paths ─────────────────────────────────────────────────────────────

IMAGE_DIR = Path(os.getenv("IMAGE_DIR", "/data/images"))
PDF_CACHE_DIR = Path(os.getenv("PDF_CACHE_DIR", "/data/pdf_cache"))

_CFG_PATHS = [
    Path(os.getenv("CONFIG_DIR", "/app/config")) / "corpus_pdfs.yaml",
    Path(__file__).parent.parent.parent / "config" / "corpus_pdfs.yaml",
]

# ── Download settings ─────────────────────────────────────────────────────────

_HEADERS = {
    "User-Agent": (
        "XeonAgentSwarm/1.0 "
        "(https://github.com/VPC-DevAdmin/xeon-agent-swarm; educational demo)"
    )
}
_TIMEOUT = 120.0   # arXiv PDFs can be large

# ── Image filtering thresholds ────────────────────────────────────────────────

_MIN_WIDTH  = 200
_MIN_HEIGHT = 200
_MIN_ASPECT = 0.2   # width/height — skip very tall narrow strips
_MAX_ASPECT = 5.0   # skip very wide horizontal banners/rules

# Embedding limits — TEI has a 512-token sequence limit and a per-batch cap
_EMBED_BATCH     = 32   # max captions per /embed call
_MAX_CAPTION_LEN = 380  # characters (~300 tokens); keeps us safely under 512

# ── Caption patterns ──────────────────────────────────────────────────────────

# Matches "Figure 3:", "Fig. 3.", "FIGURE 3 —", etc., then captures caption text
_FIG_RE = re.compile(
    r"(?:Figure|Fig\.?|FIGURE)\s+\d+\s*[.:\-\u2014]\s*(.{10,400}?)(?:\n\n|\Z)",
    re.IGNORECASE | re.DOTALL,
)

# Section heading: short ALL-CAPS lines (typical in IEEE/arXiv papers)
_HEADING_RE = re.compile(r"^([A-Z][A-Z\s\-:]{3,60})$", re.MULTILINE)


# ── Config loader ─────────────────────────────────────────────────────────────

def _load_config() -> dict:
    for path in _CFG_PATHS:
        if path.exists():
            with open(path) as f:
                return yaml.safe_load(f)
    raise FileNotFoundError(
        f"corpus_pdfs.yaml not found in {[str(p) for p in _CFG_PATHS]}"
    )


# ── PDF download (file-level cache) ──────────────────────────────────────────

async def _download_pdf(url: str) -> bytes:
    """
    Download a PDF from url, caching to PDF_CACHE_DIR by URL hash.
    Subsequent runs skip the network request entirely.
    """
    PDF_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    url_hash = hashlib.sha256(url.encode()).hexdigest()[:16]
    cache_path = PDF_CACHE_DIR / f"{url_hash}.pdf"

    if cache_path.exists() and cache_path.stat().st_size > 1024:
        logger.info("PDF cache hit: %s", url)
        return cache_path.read_bytes()

    logger.info("Downloading PDF: %s", url)
    async with httpx.AsyncClient(
        headers=_HEADERS,
        follow_redirects=True,
        timeout=_TIMEOUT,
    ) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        data = resp.content

    cache_path.write_bytes(data)
    logger.info("Cached PDF (%d bytes): %s", len(data), cache_path.name)
    return data


# ── PDF image extraction ──────────────────────────────────────────────────────

def _page_figure_captions(page_text: str, page_height: float) -> list[tuple[float, str]]:
    """
    Return (estimated_y, caption_text) for each figure caption on a page.
    y is estimated from the character offset as a fraction of page height.
    """
    results: list[tuple[float, str]] = []
    for m in _FIG_RE.finditer(page_text):
        y_frac = m.start() / max(len(page_text), 1)
        y_est = y_frac * page_height
        caption = " ".join(m.group(1).split())[:300]
        results.append((y_est, caption))
    return results


def _best_caption(
    img_bottom_y: float,
    fig_captions: list[tuple[float, str]],
    headings: list[str],
    pdf_description: str,
    page_num: int,
) -> str:
    """
    Pick the most relevant caption for an image whose bottom edge is at img_bottom_y.

    Tries:
      1. Closest figure caption within 250 points (above or below the image)
      2. First section heading on the page
      3. PDF description + page number (fallback)
    """
    best_dist = float("inf")
    best_fig  = ""
    for y_cap, cap_text in fig_captions:
        dist = abs(y_cap - img_bottom_y)
        if dist < best_dist:
            best_dist = dist
            best_fig  = cap_text

    if best_fig and best_dist < 250:
        return f"{best_fig} [{pdf_description}]"

    if headings:
        return f"{headings[0].strip().title()} — {pdf_description} (p.{page_num + 1})"

    return f"{pdf_description} — diagram, page {page_num + 1}"


def _extract_images_from_pdf(
    pdf_bytes: bytes,
    pdf_description: str,
    corpus_name: str,
    pdf_url: str,
) -> Iterator[dict]:
    """
    Yield image metadata dicts for each qualifying image found in the PDF.

    Deduplicates by MD5 of image bytes so the same figure embedded on multiple
    pages is only stored once. All images are saved as JPEG to IMAGE_DIR.
    """
    # Lazy import so the rest of the backend doesn't require pymupdf at runtime
    try:
        import pymupdf as fitz  # pymupdf >= 1.24
    except ImportError:
        import fitz              # pymupdf < 1.24 / PyMuPDF legacy

    url_hash   = hashlib.sha256(pdf_url.encode()).hexdigest()[:8]
    corpus_dir = IMAGE_DIR / corpus_name
    corpus_dir.mkdir(parents=True, exist_ok=True)

    doc: fitz.Document = fitz.open(stream=pdf_bytes, filetype="pdf")
    seen_hashes: set[str] = set()
    img_index = 0

    for page_num in range(len(doc)):
        page      = doc[page_num]
        page_rect = page.rect
        page_text = page.get_text("text")

        fig_captions = _page_figure_captions(page_text, page_rect.height)
        headings     = _HEADING_RE.findall(page_text)
        image_list   = page.get_images(full=True)

        for img_info in image_list:
            xref = img_info[0]

            # ── Size check via image-list metadata (fast) ─────────────────
            width_hint  = img_info[2]
            height_hint = img_info[3]
            if width_hint > 0 and height_hint > 0:
                if width_hint < _MIN_WIDTH or height_hint < _MIN_HEIGHT:
                    continue
                aspect = width_hint / height_hint
                if aspect < _MIN_ASPECT or aspect > _MAX_ASPECT:
                    continue

            # ── Extract raw image ─────────────────────────────────────────
            try:
                base_img = doc.extract_image(xref)
            except Exception as exc:
                logger.debug("extract_image xref=%d page=%d: %s", xref, page_num, exc)
                continue

            width  = base_img["width"]
            height = base_img["height"]

            # Final size/aspect filter on actual dimensions
            if width < _MIN_WIDTH or height < _MIN_HEIGHT:
                continue
            if not (_MIN_ASPECT <= width / height <= _MAX_ASPECT):
                continue

            # ── Deduplication ─────────────────────────────────────────────
            raw_bytes = base_img["image"]
            img_hash  = hashlib.md5(raw_bytes).hexdigest()
            if img_hash in seen_hashes:
                continue
            seen_hashes.add(img_hash)

            # ── Convert to JPEG via Pixmap ────────────────────────────────
            try:
                pix = fitz.Pixmap(doc, xref)
                # CMYK (n=4 channels without alpha) → RGB
                if pix.colorspace and pix.colorspace.n > 3:
                    pix = fitz.Pixmap(fitz.csRGB, pix)
                jpeg_bytes = pix.tobytes("jpeg", jpg_quality=85)
            except Exception as exc:
                logger.debug("Pixmap xref=%d: %s", xref, exc)
                # Fall back to raw bytes only if they're already JPEG
                if base_img.get("ext", "") not in ("jpeg", "jpg"):
                    continue
                jpeg_bytes = raw_bytes

            # ── Image position for caption matching ───────────────────────
            try:
                rects = page.get_image_rects(img_info)
                img_bottom_y = rects[0].y1 if rects else page_rect.height / 2
            except Exception:
                img_bottom_y = page_rect.height / 2

            # ── Caption ───────────────────────────────────────────────────
            caption = _best_caption(
                img_bottom_y, fig_captions, headings, pdf_description, page_num
            )

            # ── Save ──────────────────────────────────────────────────────
            filename     = f"pdf_{url_hash}_p{page_num:03d}_{img_index:02d}.jpg"
            local_path   = f"{corpus_name}/{filename}"
            dest         = corpus_dir / filename
            dest.write_bytes(jpeg_bytes)
            img_index   += 1

            yield {
                "local_path": local_path,
                "caption":    caption,
                "alt_text":   pdf_description,
                "source_url": pdf_url,
                "doc_title":  pdf_description,
            }

    doc.close()


# ── Public API ────────────────────────────────────────────────────────────────

async def ingest_pdf_images(
    corpus_name: str,
    embedder,
    image_store,
) -> dict:
    """
    Download and index all PDF images configured for corpus_name.

    Reads config/corpus_pdfs.yaml, downloads each PDF (cached), extracts
    qualifying images, embeds captions via TEI, and upserts to the Redis
    image store.

    Returns:
        {"corpus": str, "pdf_count": int, "image_count": int, "skipped_pdfs": int}
    """
    try:
        config = _load_config()
    except FileNotFoundError as exc:
        logger.warning("%s — skipping PDF image ingestion", exc)
        return {"corpus": corpus_name, "pdf_count": 0, "image_count": 0, "skipped_pdfs": 0}

    pdf_configs: list[dict] = (
        config.get("corpora", {}).get(corpus_name, {}).get("pdfs", [])
    )
    if not pdf_configs:
        print(f"[{corpus_name}:pdfs] No PDFs configured — skipping")
        return {"corpus": corpus_name, "pdf_count": 0, "image_count": 0, "skipped_pdfs": 0}

    await image_store.create_index()

    total_images = 0
    skipped_pdfs = 0

    for pdf_cfg in pdf_configs:
        url         = pdf_cfg["url"]
        description = pdf_cfg.get("description", url).strip()
        short_desc  = description[:70] + "…" if len(description) > 70 else description
        print(f"[{corpus_name}:pdfs] {short_desc}")

        try:
            pdf_bytes = await _download_pdf(url)
        except Exception as exc:
            print(f"[{corpus_name}:pdfs]   ✗ Download failed: {exc}")
            logger.error("PDF download %s: %s", url, exc)
            skipped_pdfs += 1
            continue

        images = list(_extract_images_from_pdf(pdf_bytes, description, corpus_name, url))
        print(f"[{corpus_name}:pdfs]   Extracted {len(images)} qualifying images")

        if not images:
            continue

        # Truncate captions to stay within TEI's 512-token sequence limit,
        # then batch embed to stay within TEI's per-request batch cap.
        for img in images:
            img["caption"] = img["caption"][:_MAX_CAPTION_LEN]

        captions = [img["caption"] for img in images]
        embeddings: list = []
        for i in range(0, len(captions), _EMBED_BATCH):
            batch_embs = await embedder.embed_texts(captions[i : i + _EMBED_BATCH])
            embeddings.extend(batch_embs)

        n = await image_store.add_images(images, embeddings)
        total_images += n
        print(f"[{corpus_name}:pdfs]   Indexed {n} images")

    return {
        "corpus":      corpus_name,
        "pdf_count":   len(pdf_configs) - skipped_pdfs,
        "image_count": total_images,
        "skipped_pdfs": skipped_pdfs,
    }
