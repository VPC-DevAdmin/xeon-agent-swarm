"""INT8 cross-encoder reranker on ONNX Runtime, TEI-compatible API.

Why this exists instead of the TEI container for reranking: the ONNX
Runtime bundled in TEI's CPU image runs a dynamically-quantized INT8
BERT SLOWER than FP32 (22 vs 34 queries/s on the 32-core pin), while the
venv's onnxruntime 1.29 runs the same INT8 graph 2.6x FASTER than FP32
(746 vs 288 pairs/s per 8-thread slice) - its int8 GEMM kernels use the
AMX / AVX-512 VNNI units on this Xeon. Serving the model through ORT
directly is what turns the quantization into throughput.

Serves POST /rerank {query, texts, truncate} -> [{index, score}] sorted
descending, and GET /health, exactly like TEI, so the retrieval client
does not know the difference. Run several worker processes pinned to the
reranker's cpuset; each process runs one 8-thread inference at a time
(8-thread ops running N-wide beat one 32-thread op on this model).

    taskset -c 88-119 uvicorn scripts.ort_rerank_server:app \\
        --workers 4 --port 8881 --host 127.0.0.1

Backpressure is preserved: when more than RERANK_MAX_QUEUE requests wait
in a process, the request is refused with 429 (TEI's behavior), which the
client backs off from.
"""
from __future__ import annotations

import os
import threading

import numpy as np
import onnxruntime as ort
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from tokenizers import Tokenizer

MODEL_DIR = os.getenv("RERANK_MODEL_DIR", "/data/local/ms-marco-int8")
THREADS = int(os.getenv("RERANK_THREADS", "8") or 8)
MAX_LEN = int(os.getenv("RERANK_MAX_LEN", "256") or 256)
MAX_BATCH = int(os.getenv("RERANK_MAX_BATCH", "32") or 32)
MAX_QUEUE = int(os.getenv("RERANK_MAX_QUEUE", "16") or 16)

app = FastAPI(title="ort-rerank")

_tok = Tokenizer.from_file(os.path.join(MODEL_DIR, "tokenizer.json"))
_tok.enable_truncation(MAX_LEN)
_tok.enable_padding(pad_id=0, pad_token="[PAD]")
_so = ort.SessionOptions()
_so.intra_op_num_threads = THREADS
_so.inter_op_num_threads = 1
_so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
# ORT's intra-op threads spin-wait after every run by default; N processes
# x 8 spinning threads on one cpuset contend with each other's real work.
_so.add_session_config_entry("session.intra_op.allow_spinning",
                             os.getenv("RERANK_SPIN", "0") or "0")
_sess = ort.InferenceSession(os.path.join(MODEL_DIR, "onnx", "model.onnx"),
                             _so, providers=["CPUExecutionProvider"])
_inputs = {i.name for i in _sess.get_inputs()}
_run_lock = threading.Lock()      # one inference at a time per process
_queue_lock = threading.Lock()
_waiting = 0


class RerankRequest(BaseModel):
    query: str
    texts: list[str]
    truncate: bool = True


@app.get("/health")
def health() -> dict:
    return {"ok": True, "model": MODEL_DIR, "threads": THREADS}


@app.post("/rerank")
def rerank(req: RerankRequest) -> list[dict]:
    global _waiting
    if len(req.texts) > MAX_BATCH:
        raise HTTPException(413, f"batch size {len(req.texts)} > {MAX_BATCH}")
    if not req.texts:
        return []
    with _queue_lock:
        if _waiting >= MAX_QUEUE:
            raise HTTPException(429, "reranker queue full")
        _waiting += 1
    try:
        enc = _tok.encode_batch([(req.query, t) for t in req.texts])
        feed = {"input_ids": np.array([e.ids for e in enc], dtype=np.int64),
                "attention_mask": np.array([e.attention_mask for e in enc],
                                           dtype=np.int64)}
        if "token_type_ids" in _inputs:
            feed["token_type_ids"] = np.array([e.type_ids for e in enc],
                                              dtype=np.int64)
        with _run_lock:
            logits = _sess.run(None, feed)[0].reshape(-1)
    finally:
        with _queue_lock:
            _waiting -= 1
    scored = [{"index": i, "score": float(s)} for i, s in enumerate(logits)]
    scored.sort(key=lambda d: -d["score"])
    return scored
