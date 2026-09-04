"""The sandboxed ingest-parse job (runs in an isolated interpreter; see
sandbox.py).

    python -I -S sandbox_ingest_job.py <seed> <site-packages> <docs-dir> <pages>

The parse half of an ingestion agent's step: open a seeded selection of
PDF documents from the document set (scripts/make_ingest_docs.py),
extract the text of `pages` pages, normalize it, split it into ~180-word
chunks with a 30-word overlap, drop exact duplicates, and hand the chunks
back for the executor to embed and index (toolbox.bench_execute). Prints
one JSON line: docs, pages, chars, chunks, parse_ms, cpu_ms, compute_ms,
and the chunk texts.
"""
import glob
import hashlib
import json
import os
import re
import resource
import sys
import time

seed, site, docs_dir, pages_wanted = int(sys.argv[1]), sys.argv[2], sys.argv[3], int(sys.argv[4])
if site:
    sys.path.append(site)
from pypdf import PdfReader  # noqa: E402

t0 = time.perf_counter()
paths = sorted(glob.glob(os.path.join(docs_dir, "doc-*.pdf")))
if not paths:
    print(json.dumps({"error": f"no documents under {docs_dir}"}))
    sys.exit(2)
start = seed % len(paths)
order = paths[start:] + paths[:start]
pages = 0
docs = 0
text_parts = []
t1 = time.perf_counter()
for p in order:
    if pages >= pages_wanted:
        break
    reader = PdfReader(p)
    docs += 1
    for page in reader.pages:
        if pages >= pages_wanted:
            break
        text_parts.append(page.extract_text() or "")
        pages += 1
parse_ms = (time.perf_counter() - t1) * 1000
text = "\n".join(text_parts)
text = re.sub(r"[ \t]+", " ", text)
text = re.sub(r"-\n(?=[a-z])", "", text)          # de-hyphenate line breaks
text = re.sub(r"\n{2,}", "\n", text)
words = text.split()
CHUNK, OVERLAP = 180, 30
chunks, seen = [], set()
i = 0
while i < len(words):
    piece = " ".join(words[i:i + CHUNK])
    h = hashlib.sha1(piece.encode()).hexdigest()
    if h not in seen:
        seen.add(h)
        chunks.append(piece)
    i += CHUNK - OVERLAP
cpu = resource.getrusage(resource.RUSAGE_SELF)
print(json.dumps({
    "docs": docs, "pages": pages, "chars": len(text), "words": len(words),
    "chunks": len(chunks), "duplicates": (len(words) + CHUNK - OVERLAP - 1) // (CHUNK - OVERLAP) - len(chunks),
    "parse_ms": round(parse_ms, 1),
    "cpu_ms": round((cpu.ru_utime + cpu.ru_stime) * 1000, 1),
    "compute_ms": round((time.perf_counter() - t0) * 1000, 1),
    "texts": chunks,
}))
