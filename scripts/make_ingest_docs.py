"""Generate the seeded PDF document set the ingestion agent parses.

    PYTHONPATH=. .venv/bin/python scripts/make_ingest_docs.py [--docs 40] [--pages 20] [--out data/capacity/ingest]

Plain PDF 1.4 written by hand (no library): every page carries about 45
lines, each line broken into several text runs with their own font, size
and positioning operators, and a header and footer in a second font, so
extraction has to do the work a real report makes it do (font state,
positioning, run assembly) rather than read one string per page. The
words come from the retrieval corpus's vocabulary so chunks are
searchable. Deterministic for the seed; the set is built once and shared.
"""
from __future__ import annotations

import argparse
import random
import zlib
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


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def page_stream(rng: random.Random, doc: int, page: int) -> bytes:
    ops = [f"BT /F2 9 Tf 50 800 Td ({_esc(f'Report {doc:03d} / section {page + 1}')}) Tj ET"]
    y = 770
    for li in range(45):
        x = 50
        if li % 9 == 4:
            # a contact line, so the intake pipeline's personal-data scan has
            # real items to find and redact (seeded, synthetic)
            who = rng.choice(("a.reyes", "m.okafor", "j.lindqvist", "s.tanaka", "p.novak"))
            line = (f"Contact: {who}@example.com, +1 415 555 {rng.randrange(1000, 9999)}; "
                    f"card on file 4111 1111 1111 {rng.randrange(1000, 9999)}")
            ops.append(f"BT /F1 10 Tf {x} {y} Td ({_esc(line)}) Tj ET")
            y -= 16
            continue
        size = rng.choice((9, 10, 11))
        ops.append(f"BT /F1 {size} Tf {x} {y} Td")
        runs = rng.randrange(3, 7)
        width = 0.0
        for r in range(runs):
            n = rng.randrange(2, 6)
            words = " ".join(rng.choice(WORDS) for _ in range(n)) + " "
            # Each run starts where the previous one ended (about 0.5 em per
            # character for these faces), so the rendered line reads as text
            # and stays inside the page's 500-point column.
            if r:
                advance = last_len * last_size * 0.5
                if width + advance + len(words) * size * 0.5 > 500:
                    break
                size = rng.choice((9, 10, 11))
                ops.append(f"/F{rng.choice((1, 1, 3))} {size} Tf {advance:.1f} 0 Td")
                width += advance
            ops.append(f"({_esc(words)}) Tj")
            last_len, last_size = len(words), size
        ops.append("ET")
        y -= 16
    ops.append(f"BT /F2 8 Tf 50 40 Td ({_esc(f'page {page + 1} · topic{zlib.crc32(f'{doc}-{page}'.encode()) % 2000}')}) Tj ET")
    return "\n".join(ops).encode("latin-1")


def write_pdf(path: Path, rng: random.Random, doc: int, pages: int) -> None:
    objs: list[bytes] = []

    def add(body: bytes) -> int:
        objs.append(body)
        return len(objs)

    font1 = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    font2 = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>")
    font3 = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Times-Roman >>")
    pages_id = len(objs) + 1 + 2 * pages          # allocated after the page objects
    page_ids = []
    for p in range(pages):
        stream = page_stream(rng, doc, p)
        cid = add(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream")
        pid = add(f"<< /Type /Page /Parent {pages_id} 0 R /MediaBox [0 0 595 842] /Contents {cid} 0 R "
                  f"/Resources << /Font << /F1 {font1} 0 R /F2 {font2} 0 R /F3 {font3} 0 R >> >> >>".encode())
        page_ids.append(pid)
    kids = " ".join(f"{i} 0 R" for i in page_ids)
    real_pages_id = add(f"<< /Type /Pages /Kids [{kids}] /Count {pages} >>".encode())
    assert real_pages_id == pages_id
    catalog = add(f"<< /Type /Catalog /Pages {pages_id} 0 R >>".encode())
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (f"trailer\n<< /Size {len(objs) + 1} /Root {catalog} 0 R >>\nstartxref\n{xref}\n%%EOF\n").encode()
    path.write_bytes(bytes(out))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--docs", type=int, default=40)
    ap.add_argument("--pages", type=int, default=20)
    ap.add_argument("--seed", type=int, default=17)
    ap.add_argument("--out", default="data/capacity/ingest")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    rng = random.Random(a.seed)
    for d in range(a.docs):
        write_pdf(out / f"doc-{d:03d}.pdf", rng, d, a.pages)
    total = sum(p.stat().st_size for p in out.glob("doc-*.pdf"))
    print(f"wrote {a.docs} documents x {a.pages} pages to {out} ({total / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
