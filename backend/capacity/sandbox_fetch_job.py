"""The sandboxed source-fetch job (isolated interpreter; see sandbox.py).

    python -I -S sandbox_fetch_job.py <seed> <site-packages> <html-dir> <pages>

The research agent's first step: a seeded selection of HTML pages from the
source set (scripts/make_html_docs.py) is parsed, boilerplate (navigation,
footers, promotions) is removed and the main text extracted (trafilatura),
and the pages are summarized into the sources the worker then retrieves
against. Prints one JSON line: pages, chars, words, parse_ms, cpu_ms,
compute_ms, and the first lines of each page.
"""
import glob
import json
import os
import resource
import sys
import time

seed, site, html_dir, wanted = int(sys.argv[1]), sys.argv[2], sys.argv[3], int(sys.argv[4])
if site:
    sys.path.append(site)
import trafilatura  # noqa: E402

t0 = time.perf_counter()
paths = sorted(glob.glob(os.path.join(html_dir, "page-*.html")))
if not paths:
    print(json.dumps({"error": f"no pages under {html_dir}"}))
    sys.exit(2)
start = seed % len(paths)
order = (paths[start:] + paths[:start])[:wanted]
t1 = time.perf_counter()
texts = []
for p in order:
    raw = open(p, encoding="utf-8", errors="replace").read()
    txt = trafilatura.extract(raw, include_comments=False, include_tables=True, favor_precision=True) or ""
    texts.append(txt)
parse_ms = (time.perf_counter() - t1) * 1000
words = sum(len(t.split()) for t in texts)
cpu = resource.getrusage(resource.RUSAGE_SELF)
print(json.dumps({
    "pages": len(texts), "chars": sum(len(t) for t in texts), "words": words,
    "parse_ms": round(parse_ms, 1),
    "cpu_ms": round((cpu.ru_utime + cpu.ru_stime) * 1000, 1),
    "compute_ms": round((time.perf_counter() - t0) * 1000, 1),
    "leads": [t[:160] for t in texts[:5]],
}))
