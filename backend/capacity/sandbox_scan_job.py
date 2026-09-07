"""The sandboxed scanned-document intake job (isolated interpreter; see
sandbox.py).

    python -I -S sandbox_scan_job.py <seed> <site-packages> <docs-dir> <pages>

The parse half of an ingestion agent's step for scanned intake: a seeded
selection of documents is rendered page by page to images (pypdfium2),
each image is read with OCR on the CPU (rapidocr, ONNX Runtime, single
thread), the text is scanned for personal data (e-mail addresses, phone
and card numbers) and redacted, then normalized, split into ~180-word
chunks with a 30-word overlap and de-duplicated, and the chunks go back to
the executor for embedding and indexing. Prints one JSON line: docs,
pages, chars, chunks, render_ms, ocr_ms, pii, cpu_ms, compute_ms, texts.
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
os.environ.setdefault("OMP_NUM_THREADS", "1")
import numpy as np  # noqa: E402
import pypdfium2 as pdfium  # noqa: E402
from rapidocr_onnxruntime import RapidOCR  # noqa: E402

t0 = time.perf_counter()
paths = sorted(glob.glob(os.path.join(docs_dir, "doc-*.pdf")))
if not paths:
    print(json.dumps({"error": f"no documents under {docs_dir}"}))
    sys.exit(2)
ocr = RapidOCR(intra_op_num_threads=1, inter_op_num_threads=1)
start = seed % len(paths)
order = paths[start:] + paths[:start]
pages = docs = 0
render_ms = ocr_ms = 0.0
text_parts = []
for p in order:
    if pages >= pages_wanted:
        break
    pdf = pdfium.PdfDocument(p)
    docs += 1
    for i in range(len(pdf)):
        if pages >= pages_wanted:
            break
        t1 = time.perf_counter()
        img = pdf[i].render(scale=2.0).to_pil().convert("RGB")   # ~144 dpi, a typical scan
        arr = np.asarray(img)
        render_ms += (time.perf_counter() - t1) * 1000
        t2 = time.perf_counter()
        result, _ = ocr(arr)
        ocr_ms += (time.perf_counter() - t2) * 1000
        text_parts.append("\n".join(r[1] for r in (result or [])))
        pages += 1
text = "\n".join(text_parts)
# personal-data scan and redaction, the intake pipeline's compliance step
pii = 0
for pat, tag in ((r"[\w.+-]+@[\w-]+\.[\w.]+", "[email]"),
                 (r"\b(?:\+?\d{1,3}[ -]?)?(?:\(?\d{3}\)?[ -]?)\d{3}[ -]?\d{4}\b", "[phone]"),
                 (r"\b(?:\d[ -]?){13,16}\b", "[card]")):
    text, n = re.subn(pat, tag, text)
    pii += n
text = re.sub(r"[ \t]+", " ", text)
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
    "chunks": len(chunks), "duplicates": max(0, (len(words) + CHUNK - OVERLAP - 1) // (CHUNK - OVERLAP) - len(chunks)),
    "render_ms": round(render_ms, 1), "ocr_ms": round(ocr_ms, 1), "parse_ms": round(render_ms + ocr_ms, 1), "pii": pii,
    "cpu_ms": round((cpu.ru_utime + cpu.ru_stime) * 1000, 1),
    "compute_ms": round((time.perf_counter() - t0) * 1000, 1),
    "texts": chunks,
}))
