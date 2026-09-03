"""INT8 cross-encoder reranker on ONNX Runtime, TEI-compatible API.

Why this exists instead of the TEI container for reranking: the ONNX
Runtime bundled in TEI's CPU image runs a dynamically-quantized INT8
BERT SLOWER than FP32 (22 vs 34 queries/s on a 32-core pin), while the
venv's onnxruntime 1.29 runs the same INT8 graph 2.6x FASTER than FP32
(746 vs 288 pairs/s per 8-thread slice) - its int8 GEMM kernels use the
AMX / AVX-512 VNNI units on this Xeon. Serving the model through ORT
directly is what turns the quantization into throughput.

Serves POST /rerank {query, texts, truncate} -> [{index, score}] sorted
descending, and GET /health, exactly like TEI, so the retrieval client
does not know the difference.

Shape of the server (v2): one INFERENCE THREAD per process drains a
queue and DYNAMICALLY BATCHES waiting requests into a single ONNX run
(up to RERANK_MAX_BATCH_PAIRS pairs), the way TEI does. The first
version ran one ONNX call per request under a lock from the HTTP thread
pool; a 23 ms batch then cost ~115 ms of wall per request (thread
hand-offs, the runtime's threads sleeping and waking between calls) and
four workers ceilinged near 35 queries/s. Batching amortizes the
hand-off and keeps the runtime's threads hot across requests.

    taskset -c <cpus> uvicorn scripts.ort_rerank_server:app \\
        --workers 4 --port 8881 --host 127.0.0.1

Backpressure is preserved: when more than RERANK_MAX_QUEUE requests wait
in a process, the request is refused with 429 (TEI's behavior), which the
client backs off from.
"""
from __future__ import annotations

import asyncio
import os
import queue
import threading
from dataclasses import dataclass

import numpy as np
import onnxruntime as ort
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from tokenizers import Tokenizer

MODEL_DIR = os.getenv("RERANK_MODEL_DIR", "/data/local/ms-marco-int8")
THREADS = int(os.getenv("RERANK_THREADS", "8") or 8)
MAX_LEN = int(os.getenv("RERANK_MAX_LEN", "256") or 256)
MAX_BATCH = int(os.getenv("RERANK_MAX_BATCH", "32") or 32)        # per request
MAX_BATCH_PAIRS = int(os.getenv("RERANK_MAX_BATCH_PAIRS", "128") or 128)  # per ONNX run
# Queue depth per worker process. Sized to the admission control upstream
# (calls in flight per executor x executors): a queue of 16 refused a third
# of a saturating fleet's calls with the cores at 55%, and refusals that
# outlive the client's 120 s backoff are failed workflows. Queueing here is
# bounded by the executors' admission gates, so a deep queue cannot run
# away; 429 means genuine overload.
MAX_QUEUE = int(os.getenv("RERANK_MAX_QUEUE", "96") or 96)

app = FastAPI(title="ort-rerank")

_tok = Tokenizer.from_file(os.path.join(MODEL_DIR, "tokenizer.json"))
_tok.enable_truncation(MAX_LEN)
_tok.no_padding()
_so = ort.SessionOptions()
_so.intra_op_num_threads = THREADS
_so.inter_op_num_threads = 1
_so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
_so.add_session_config_entry("session.intra_op.allow_spinning",
                             os.getenv("RERANK_SPIN", "1") or "1")
_sess = ort.InferenceSession(os.path.join(MODEL_DIR, "onnx", "model.onnx"),
                             _so, providers=["CPUExecutionProvider"])
_inputs = {i.name for i in _sess.get_inputs()}


@dataclass
class _Job:
    ids: list[list[int]]
    types: list[list[int]]
    future: asyncio.Future
    loop: asyncio.AbstractEventLoop


_q: queue.Queue[_Job] = queue.Queue()
_batches = 0
_pairs = 0
_run_ms = 0.0      # time inside sess.run, cumulative
_wall_ms = 0.0     # inference-thread wall, cumulative (run + assembly)


def _inference_loop() -> None:
    global _batches, _pairs, _run_ms, _wall_ms
    import time
    while True:
        jobs = [_q.get()]
        t_w = time.perf_counter()
        n = len(jobs[0].ids)
        while n < MAX_BATCH_PAIRS:
            try:
                j = _q.get_nowait()
            except queue.Empty:
                break
            jobs.append(j)
            n += len(j.ids)
        maxlen = max(len(r) for j in jobs for r in j.ids)
        ids = np.zeros((n, maxlen), dtype=np.int64)
        mask = np.zeros((n, maxlen), dtype=np.int64)
        types = np.zeros((n, maxlen), dtype=np.int64)
        row = 0
        for j in jobs:
            for r, tt in zip(j.ids, j.types):
                ids[row, :len(r)] = r
                mask[row, :len(r)] = 1
                types[row, :len(tt)] = tt
                row += 1
        feed = {"input_ids": ids, "attention_mask": mask}
        if "token_type_ids" in _inputs:
            feed["token_type_ids"] = types
        try:
            t_r = time.perf_counter()
            logits = _sess.run(None, feed)[0].reshape(-1)
            _run_ms += (time.perf_counter() - t_r) * 1000.0
        except Exception as exc:  # noqa: BLE001 - fail the waiting requests, not the thread
            for j in jobs:
                j.loop.call_soon_threadsafe(_fail, j.future, exc)
            continue
        _batches += 1
        _pairs += n
        _wall_ms += (time.perf_counter() - t_w) * 1000.0
        row = 0
        for j in jobs:
            k = len(j.ids)
            scores = logits[row:row + k].tolist()
            row += k
            j.loop.call_soon_threadsafe(_resolve, j.future, scores)


def _resolve(fut: asyncio.Future, scores: list[float]) -> None:
    if not fut.done():
        fut.set_result(scores)


def _fail(fut: asyncio.Future, exc: Exception) -> None:
    if not fut.done():
        fut.set_exception(exc)


threading.Thread(target=_inference_loop, name="ort-inference", daemon=True).start()


class RerankRequest(BaseModel):
    query: str
    texts: list[str]
    truncate: bool = True


@app.get("/health")
def health() -> dict:
    return {"ok": True, "model": MODEL_DIR, "threads": THREADS,
            "pid": os.getpid(), "queued": _q.qsize(), "max_queue": MAX_QUEUE,
            "batches": _batches, "pairs": _pairs,
            "pairs_per_batch": round(_pairs / _batches, 1) if _batches else None,
            "run_ms_per_batch": round(_run_ms / _batches, 1) if _batches else None,
            "wall_ms_per_batch": round(_wall_ms / _batches, 1) if _batches else None,
            "run_ms_per_pair": round(_run_ms / _pairs, 2) if _pairs else None}


def _encode(query: str, texts: list[str]) -> tuple[list[list[int]], list[list[int]]]:
    enc = _tok.encode_batch([(query, t) for t in texts])
    return [e.ids for e in enc], [e.type_ids for e in enc]


@app.post("/rerank")
async def rerank(req: RerankRequest) -> list[dict]:
    if len(req.texts) > MAX_BATCH:
        raise HTTPException(413, f"batch size {len(req.texts)} > {MAX_BATCH}")
    if not req.texts:
        return []
    if _q.qsize() >= MAX_QUEUE:
        raise HTTPException(429, "reranker queue full")
    ids, types = await asyncio.to_thread(_encode, req.query, req.texts)
    loop = asyncio.get_running_loop()
    fut: asyncio.Future = loop.create_future()
    _q.put(_Job(ids, types, fut, loop))
    scores = await fut
    scored = [{"index": i, "score": float(s)} for i, s in enumerate(scores)]
    scored.sort(key=lambda d: -d["score"])
    return scored
