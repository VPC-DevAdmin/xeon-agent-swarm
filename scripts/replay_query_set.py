"""Replay the workload's query set against a real OpenAI-compatible endpoint
and record, per call, what the serving side actually did.

    OPENAI_API_KEY=... PYTHONPATH=. .venv/bin/python scripts/replay_query_set.py \\
        data/capacity/queryset/typical.jsonl --base-url https://api.together.xyz/v1 \\
        --model meta-llama/Llama-3.3-70B-Instruct-Turbo --concurrency 1,8,32 \\
        --min-calls 300 --out data/capacity/serving/llama70b-together

For each concurrency level the whole set is sent (repeated until at least
--min-calls have been made) with that many requests in flight; every call
is streamed so time to first token is measured, and the response's usage
gives the real tokenizer's counts. Each row of calls.jsonl carries: key
(archetype/role/phase), concurrency, ttft_ms, total_ms, prompt_tokens,
completion_tokens, decode_tps, status, retries (429s and 5xx, retried
with backoff), and the returned message. summary.md gives per-role time
to first token and decode rate, tokens per call, and the aggregate
throughput at each level: the serving tier's measured behaviour on this
workload's own calls, from a named model, repeatable and re-recordable.
The key is read from OPENAI_API_KEY (or TOGETHER_API_KEY) and never
written anywhere.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import statistics as st
import time
from pathlib import Path

import httpx


def pct(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(q * (len(xs) - 1))))] if xs else None


async def one_call(client: httpx.AsyncClient, base: str, model: str, item: dict,
                   sem: asyncio.Semaphore, level: int, max_tokens: int | None) -> dict:
    body = {"model": model, "messages": item["messages"], "stream": True,
            "stream_options": {"include_usage": True}, "temperature": 0.2}
    if item.get("tools"):
        body["tools"] = item["tools"]
    if max_tokens or item.get("max_tokens"):
        body["max_tokens"] = int(max_tokens or item["max_tokens"])
    row = {"key": item["key"], "archetype": item["archetype"], "role": item["role"],
           "phase": item["phase"], "concurrency": level, "model": model, "retries": 0}
    delay = 1.0
    async with sem:
        for attempt in range(8):
            t0 = time.perf_counter()
            ttft = None
            content, tool_calls, usage, finish = [], {}, None, None
            try:
                async with client.stream("POST", f"{base}/chat/completions", json=body) as r:
                    if r.status_code in (429, 500, 502, 503, 529):
                        row["retries"] += 1
                        await r.aread()
                        await asyncio.sleep(delay * (0.8 + 0.4 * random.random()))
                        delay = min(delay * 2, 30.0)
                        continue
                    if r.status_code != 200:
                        txt = (await r.aread())[:300].decode(errors="replace")
                        row.update(ok=False, status=r.status_code, error=txt)
                        return row
                    async for line in r.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data)
                        except ValueError:
                            continue
                        if chunk.get("usage"):
                            usage = chunk["usage"]
                        for ch in chunk.get("choices") or []:
                            d = ch.get("delta") or {}
                            if d.get("content"):
                                if ttft is None:
                                    ttft = time.perf_counter() - t0
                                content.append(d["content"])
                            for tc in d.get("tool_calls") or []:
                                if ttft is None:
                                    ttft = time.perf_counter() - t0
                                slot = tool_calls.setdefault(tc.get("index", 0), {"id": tc.get("id"), "name": "", "arguments": ""})
                                fn = tc.get("function") or {}
                                slot["name"] = slot["name"] or fn.get("name") or ""
                                slot["arguments"] += fn.get("arguments") or ""
                                slot["id"] = slot["id"] or tc.get("id")
                            if ch.get("finish_reason"):
                                finish = ch["finish_reason"]
            except (httpx.HTTPError, asyncio.TimeoutError) as exc:
                row["retries"] += 1
                row["last_error"] = type(exc).__name__
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30.0)
                continue
            total = time.perf_counter() - t0
            text = "".join(content)
            pin = int((usage or {}).get("prompt_tokens") or 0)
            pout = int((usage or {}).get("completion_tokens") or 0) or max(1, len(text) // 4)
            row.update(ok=True, status=200, ttft_ms=round((ttft or total) * 1000, 1),
                       total_ms=round(total * 1000, 1), prompt_tokens=pin, completion_tokens=pout,
                       decode_tps=round(pout / max(1e-3, total - (ttft or 0)), 1),
                       finish_reason=finish, usage_reported=bool(usage),
                       message={"content": text or None,
                                "tool_calls": [{"id": v["id"], "type": "function",
                                                "function": {"name": v["name"], "arguments": v["arguments"]}}
                                               for _, v in sorted(tool_calls.items())] or None})
            return row
    row.update(ok=False, status=None, error="gave up after retries")
    return row


async def run_level(items, base, model, level, min_calls, max_tokens, timeout):
    sem = asyncio.Semaphore(level)
    key = os.environ.get("OPENAI_API_KEY") or os.environ.get("TOGETHER_API_KEY") or ""
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    reps = max(1, -(-min_calls // len(items)))
    batch = [it for _ in range(reps) for it in items]
    random.Random(level).shuffle(batch)
    t0 = time.perf_counter()
    async with httpx.AsyncClient(headers=headers, timeout=timeout, limits=httpx.Limits(max_connections=level + 4)) as client:
        rows = await asyncio.gather(*[one_call(client, base, model, it, sem, level, max_tokens) for it in batch])
    span = time.perf_counter() - t0
    return list(rows), span


def summarize(rows: list[dict], spans: dict[int, float]) -> str:
    lines = ["| concurrency | calls | ok | 429/5xx retries | requests/s | gen tok/s | prompt tok/s | ttft p50/p95 ms | total p50/p95 ms | decode tok/s p50 |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for level in sorted(spans):
        rs = [r for r in rows if r["concurrency"] == level]
        ok = [r for r in rs if r.get("ok")]
        span = spans[level]
        lines.append(f"| {level} | {len(rs)} | {len(ok)} | {sum(r.get('retries', 0) for r in rs)} | "
                     f"{len(ok) / span:.2f} | {sum(r['completion_tokens'] for r in ok) / span:.0f} | "
                     f"{sum(r['prompt_tokens'] for r in ok) / span:.0f} | "
                     f"{pct([r['ttft_ms'] for r in ok], .5):.0f}/{pct([r['ttft_ms'] for r in ok], .95):.0f} | "
                     f"{pct([r['total_ms'] for r in ok], .5):.0f}/{pct([r['total_ms'] for r in ok], .95):.0f} | "
                     f"{pct([r['decode_tps'] for r in ok], .5):.1f} |")
    lines += ["", "| role (highest concurrency) | calls | prompt tok | completion tok | ttft p50 ms | total p50/p95 ms | decode tok/s |",
              "|---|---|---|---|---|---|---|"]
    top = max(spans)
    roles = sorted({r["role"] for r in rows})
    for role in roles:
        ok = [r for r in rows if r["concurrency"] == top and r["role"] == role and r.get("ok")]
        if not ok:
            continue
        lines.append(f"| {role} | {len(ok)} | {st.median(r['prompt_tokens'] for r in ok):.0f} | "
                     f"{st.median(r['completion_tokens'] for r in ok):.0f} | {pct([r['ttft_ms'] for r in ok], .5):.0f} | "
                     f"{pct([r['total_ms'] for r in ok], .5):.0f}/{pct([r['total_ms'] for r in ok], .95):.0f} | "
                     f"{st.median(r['decode_tps'] for r in ok):.1f} |")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("queryset")
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--concurrency", default="1,8,32",
                    help="levels to run; --sweep replaces it with 1,2,4,...,--max-concurrency")
    ap.add_argument("--sweep", action="store_true",
                    help="saturation sweep against ONE known GPU: doubling levels until aggregate "
                         "generation tokens/s stops rising; the plateau is that GPU's ceiling on this workload")
    ap.add_argument("--max-concurrency", type=int, default=256)
    ap.add_argument("--gpus", type=int, default=None,
                    help="GPUs behind the endpoint (a dedicated endpoint or a rented instance); "
                         "recorded so the ratio can read tokens per GPU from this file")
    ap.add_argument("--min-calls", type=int, default=300)
    ap.add_argument("--max-tokens", type=int, default=None)
    ap.add_argument("--timeout", type=float, default=180.0)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    items = [json.loads(l) for l in open(a.queryset) if l.strip()]
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    all_rows: list[dict] = []
    spans: dict[int, float] = {}
    if a.sweep:
        levels = []
        lv = 1
        while lv <= a.max_concurrency:
            levels.append(lv)
            lv *= 2
    else:
        levels = [int(x) for x in a.concurrency.split(",") if x.strip()]
    best = 0.0
    for level in levels:
        rows, span = asyncio.run(run_level(items, a.base_url.rstrip("/"), a.model, level,
                                           max(a.min_calls, level * 4), a.max_tokens, a.timeout))
        spans[level] = span
        for r in rows:
            if a.gpus:
                r["gpus"] = a.gpus
        all_rows += rows
        ok = [r for r in rows if r.get("ok")]
        gen = sum(r['completion_tokens'] for r in ok) / span
        print(f"concurrency {level}: {len(ok)}/{len(rows)} ok in {span:.0f}s, "
              f"{gen:.0f} gen tok/s, {sum(r.get('retries', 0) for r in rows)} retries", flush=True)
        with open(out / "calls.jsonl", "w") as fh:
            for r in all_rows:
                fh.write(json.dumps(r) + "\n")
        if a.sweep:
            # Stop once doubling the concurrency adds under 10% of throughput
            # (or throttling appears): the ceiling has been found.
            if sum(r.get("retries", 0) for r in rows) > len(rows) * 0.05:
                print("throttling at this level; stopping the sweep", flush=True)
                break
            if best and gen < best * 1.10:
                print("throughput flat; ceiling reached", flush=True)
                break
            best = max(best, gen)
    summary = summarize(all_rows, spans)
    (out / "summary.md").write_text(f"# Serving profile: {a.model} via {a.base_url}\n\n" + summary + "\n")
    ok_rows = [r for r in all_rows if r.get("ok")]
    per_level = {lv: sum(r["completion_tokens"] for r in ok_rows if r["concurrency"] == lv) / spans[lv]
                 for lv in spans}
    ceiling = max(per_level.values()) if per_level else None
    (out / "profile.json").write_text(json.dumps({"model": a.model, "base_url": a.base_url,
                                                   "queryset": a.queryset, "levels": sorted(spans),
                                                   "spans_s": spans, "calls": len(all_rows),
                                                   "gpus": a.gpus,
                                                   "gen_tok_s_per_level": per_level,
                                                   "gen_tok_s_ceiling": ceiling,
                                                   "gen_tok_s_per_gpu": (ceiling / a.gpus) if (a.gpus and ceiling) else None,
                                                   "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}, indent=1))
    print(summary)
    print(f"-> {out}/calls.jsonl (use CAPACITY_SERVING_PROFILE={out}/calls.jsonl CAPACITY_SERVING_CONCURRENCY=<level>)")


if __name__ == "__main__":
    main()
