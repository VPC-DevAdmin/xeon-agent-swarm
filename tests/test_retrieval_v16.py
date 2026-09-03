"""Workload v16: real on-box retrieval — sparse, fused, reranked, packed.

The corpus is a fixture (built once from versioned parameters); per-run
determinism comes from the seeded queries. The dense vector service is
modeled off-box; everything else is genuine CPU work.
"""
import asyncio
import importlib.util
import pathlib

import pytest


@pytest.fixture()
def small_corpus(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPACITY_CORPUS_CHUNKS", "2000")
    monkeypatch.setenv("CAPACITY_VDB_MS", "1")
    import backend.capacity.retrieval as rt
    import importlib
    importlib.reload(rt)
    monkeypatch.setattr(rt, "CORPUS_DIR", tmp_path)
    rt._local_con.clear()
    return rt


def test_corpus_builds_once_and_reuses(small_corpus):
    rt = small_corpus
    p1 = rt.ensure_corpus()
    mtime = p1.stat().st_mtime_ns
    p2 = rt.ensure_corpus()
    assert p1 == p2 and p2.stat().st_mtime_ns == mtime


def test_sparse_search_finds_in_topic_chunks(small_corpus):
    rt = small_corpus
    rt.ensure_corpus()
    hits = rt.sparse_search("topic7 throughput latency")
    assert hits
    per_topic = rt.CHUNKS // rt.TOPICS
    in_topic = [cid for cid, _s in hits if cid // per_topic == 7]
    assert len(in_topic) >= 1


def test_fusion_prefers_agreement(small_corpus):
    rt = small_corpus
    dense = [(10, 0.9), (11, 0.8), (12, 0.7)]
    sparse = [(12, 5.0), (99, 4.0)]
    fused = rt.rrf_fuse(dense, sparse)
    assert fused[0] == 12          # on both lists


def test_full_retrieve_is_deterministic_and_cited(small_corpus):
    rt = small_corpus
    rt.ensure_corpus()
    r1 = asyncio.run(rt.retrieve("topic3 cache tensor", budget_words=400))
    r2 = asyncio.run(rt.retrieve("topic3 cache tensor", budget_words=400))
    assert r1["chunks"] == r2["chunks"]
    assert r1["packed"] == r2["packed"]
    assert "[chunk-" in r1["packed"]
    assert len(r1["packed"].split()) <= 460      # budget + headers
    assert r1["elapsed_ms"] > 0


def test_mock_worker_sequences_retrieve_then_record():
    path = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "mock_router.py"
    spec = importlib.util.spec_from_file_location("mock_router_v16", path)
    mr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mr)
    # The sequencing predicate is what matters: with no retrieve result the
    # worker must call bench_retrieve; with one, bench_record; then answer.
    def call(name, cid):
        return {"role": "assistant", "tool_calls": [
            {"id": cid, "function": {"name": name, "arguments": "{}"}}]}

    def result(cid):
        return {"role": "tool", "tool_call_id": cid, "content": "..."}

    msgs_none = [{"role": "user", "content": "analyze the topic"}]
    msgs_ret = msgs_none + [call("bench_retrieve", "t1"), result("t1")]
    msgs_both = msgs_ret + [call("bench_record", "t2"), result("t2")]
    assert mr._tool_result_count(msgs_none, "bench_retrieve") == 0
    assert mr._tool_result_count(msgs_ret, "bench_retrieve") == 1
    assert mr._tool_result_count(msgs_ret, "bench_record") == 0
    assert mr._tool_result_count(msgs_both, "bench_record") == 1


def test_tier_gate_bounds_concurrency(monkeypatch):
    """Admission control: at most N calls in flight to the sized tier per
    process; excess callers wait in-process instead of producing 429s."""
    import asyncio
    import backend.capacity.retrieval as rt
    monkeypatch.setenv("CAPACITY_RERANK_CONCURRENCY", "2")
    rt._tier_gate = None
    peak = {"now": 0, "max": 0}

    class _Resp:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return []

    class _Client:
        async def post(self, url, json=None):
            peak["now"] += 1
            peak["max"] = max(peak["max"], peak["now"])
            await asyncio.sleep(0.02)
            peak["now"] -= 1
            return _Resp()

    monkeypatch.setattr(rt, "_http", lambda: _Client())

    async def main():
        await asyncio.gather(*[rt._post_backpressure("http://x/rerank", {})
                               for _ in range(8)])
    asyncio.run(main())
    assert peak["max"] == 2


def test_transport_reset_is_retried_once(monkeypatch):
    """A keep-alive race (server closed the pooled connection) surfaces as
    httpx.ReadError on the next send; the call is retried on a fresh
    connection instead of failing the workflow. Three in a row still raise."""
    import asyncio
    import httpx
    import backend.capacity.retrieval as rt
    rt._tier_gate = None
    calls = {"n": 0}

    class _Resp:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return []

    class _Client:
        def __init__(self, fail_first):
            self.fail_first = fail_first
        async def post(self, url, json=None):
            calls["n"] += 1
            if calls["n"] <= self.fail_first:
                raise httpx.ReadError("")
            return _Resp()

    monkeypatch.setattr(rt, "_http", lambda: _Client(fail_first=1))
    asyncio.run(rt._post_backpressure("http://x/rerank", {}))
    assert calls["n"] == 2
    calls["n"] = 0
    monkeypatch.setattr(rt, "_http", lambda: _Client(fail_first=3))
    try:
        asyncio.run(rt._post_backpressure("http://x/rerank", {}))
    except httpx.ReadError:
        assert calls["n"] == 3
    else:
        raise AssertionError("three resets must raise")


def test_rerank_depth_is_a_declared_parameter(monkeypatch):
    import backend.capacity.retrieval as rt
    monkeypatch.delenv("CAPACITY_RERANK_DEPTH", raising=False)
    assert rt.rerank_depth() == 16
    monkeypatch.setenv("CAPACITY_RERANK_DEPTH", "48")
    assert rt.rerank_depth() == 48
    from backend.capacity import controller as ctl
    assert "rerank=48" in ctl.CapacityTest._serving_tier_tag()
