"""Generate the seeded HTML source set the research agent fetches.

    PYTHONPATH=. .venv/bin/python scripts/make_html_docs.py [--pages 300] [--out data/capacity/html]

Each page is the shape of an article on an enterprise site: navigation,
promotional asides, a long article body of several sections built from the
retrieval corpus's vocabulary, comment blocks, and a footer, so main-text
extraction has real boilerplate to remove. Deterministic for the seed.
"""
from __future__ import annotations

import argparse
import random
from pathlib import Path

WORDS = ("throughput latency quantization bandwidth cache tensor batch prefill "
         "decode scheduler memory socket thread kernel affinity numa buffer queue "
         "token weight gradient checkpoint shard replica pipeline attention context "
         "window layer head embedding vocabulary sampling temperature logit softmax "
         "epoch dataset benchmark baseline regression profile allocator "
         "fragmentation contention saturation backlog deadline percentile median "
         "variance capacity ceiling merchant ledger invoice settlement region "
         "category anomaly rollout policy incident escalation runbook service "
         "cluster node rack power thermal firmware telemetry sampler evidence").split()


def para(rng, n):
    return " ".join(rng.choice(WORDS) for _ in range(n)).capitalize() + "."


def page(rng, i):
    nav = "".join(f'<li><a href="/s/{rng.randrange(900)}">{rng.choice(WORDS).title()}</a></li>' for _ in range(14))
    promo = "".join(f'<aside class="promo"><h4>{rng.choice(WORDS).title()} offer</h4><p>{para(rng, 25)}</p><a href="/buy">Learn more</a></aside>' for _ in range(3))
    body = "".join(f"<h2>{rng.choice(WORDS).title()} and {rng.choice(WORDS)}</h2>" + "".join(f"<p>{para(rng, rng.randrange(60, 140))}</p>" for _ in range(rng.randrange(3, 6))) for _ in range(rng.randrange(5, 9)))
    table = "<table><tr><th>metric</th><th>value</th></tr>" + "".join(f"<tr><td>{rng.choice(WORDS)}</td><td>{rng.randrange(1, 9999)}</td></tr>" for _ in range(12)) + "</table>"
    comments = "".join(f'<div class="comment"><b>user{rng.randrange(999)}</b><p>{para(rng, 30)}</p></div>' for _ in range(8))
    return f"""<!doctype html><html><head><title>Report {i}: {rng.choice(WORDS)} {rng.choice(WORDS)}</title>
<meta name="description" content="{para(rng, 12)}"><script>window.__x={{}}</script></head><body>
<header><nav><ul>{nav}</ul></nav></header><div class="layout"><main><article>
<h1>Report {i}: {rng.choice(WORDS).title()} {rng.choice(WORDS)}</h1><p class="byline">Published by topic{i % 2000}</p>
{body}{table}</article><section class="comments">{comments}</section></main><div class="sidebar">{promo}</div></div>
<footer><ul>{nav}</ul><p>{para(rng, 40)}</p></footer></body></html>"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages", type=int, default=300)
    ap.add_argument("--seed", type=int, default=23)
    ap.add_argument("--out", default="data/capacity/html")
    a = ap.parse_args()
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    rng = random.Random(a.seed)
    for i in range(a.pages):
        (out / f"page-{i:04d}.html").write_text(page(rng, i), encoding="utf-8")
    total = sum(p.stat().st_size for p in out.glob("page-*.html"))
    print(f"wrote {a.pages} pages to {out} ({total / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
