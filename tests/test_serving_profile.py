"""The recorded serving profile: call classification, tracing, capture, replay in the stand-in."""
import json
import os
import subprocess
import sys

from fastapi.testclient import TestClient

from tests.test_workload_v15 import _mock_router

TOOLS = [{"type": "function", "function": {"name": n, "parameters": {}}} for n in ("bench_execute", "bench_record")]


def _body(system, obj, tools=TOOLS, extra=()):
    return {"model": "auto", "tools": tools,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": obj}, *extra]}


def test_call_key_names_archetype_role_and_phase():
    sys.path.insert(0, "scripts")
    import mock_router as mr
    assert mr.call_key(_body("You are a research specialist.", "Research: Using ONLY the dataset (XL) available")) == "analyst_xl/research/0"
    assert mr.call_key(_body("You are a research specialist.", "Research: Using ONLY the dataset available",
                             extra=[{"role": "tool", "content": "x", "tool_call_id": "1"}])) == "data_analysis/research/1"
    assert mr.call_key(_body("You are the output validator.", "Using ONLY the field notes below")) == "research_brief/judge/0"
    planner = _body("You are the main agent.", "Handle this single support ticket",
                    tools=[{"type": "function", "function": {"name": "task", "parameters": {}}}])
    assert mr.call_key(planner) == "task_ticket/planner/0"


def test_trace_then_capture_then_replay(tmp_path, monkeypatch):
    sys.path.insert(0, "scripts")
    import mock_router as mr
    trace = tmp_path / "trace"
    monkeypatch.setenv("CAPACITY_MOCK_TRACE_DIR", str(trace))
    client = TestClient(_mock_router().app)
    for seed in range(3):
        r = client.post("/v1/chat/completions",
                        json=_body("You are a research specialist.", f"Research the topic (seed {seed}): Using ONLY the dataset available"))
        assert r.status_code == 200 and "x-mock-profile" not in r.headers
    out = subprocess.run([sys.executable, "scripts/capture_query_set.py", str(trace), str(tmp_path / "qs.jsonl"), "--per-key", "2"],
                         capture_output=True, text=True, check=True)
    rows = [json.loads(l) for l in open(tmp_path / "qs.jsonl")]
    assert len(rows) == 2 and rows[0]["key"] == "data_analysis/research/0" and rows[0]["tools"]
    assert "query set: 2 calls over 1 positions" in out.stdout
    # a recorded profile for that position: the stand-in answers with its timing and token counts
    prof = tmp_path / "calls.jsonl"
    prof.write_text("\n".join(json.dumps(x) for x in [
        {"key": "data_analysis/research/0", "concurrency": 8, "ok": True, "ttft_ms": 400.0, "total_ms": 1200.0,
         "prompt_tokens": 2200, "completion_tokens": 90, "model": "m"},
        {"key": "data_analysis/research/0", "concurrency": 32, "ok": True, "ttft_ms": 900.0, "total_ms": 30.0,
         "prompt_tokens": 2300, "completion_tokens": 95, "model": "m"},
        {"key": "digest/writing/0", "concurrency": 32, "ok": True, "ttft_ms": 100.0, "total_ms": 40.0,
         "prompt_tokens": 500, "completion_tokens": 300, "model": "m"}]) + "\n")
    monkeypatch.delenv("CAPACITY_MOCK_TRACE_DIR")
    monkeypatch.setenv("CAPACITY_SERVING_PROFILE", str(prof))
    monkeypatch.setenv("CAPACITY_SERVING_CONCURRENCY", "32")
    r = client.post("/v1/chat/completions", json=_body("You are a research specialist.", "Research: Using ONLY the dataset available"))
    assert r.headers["x-mock-profile"] == "data_analysis/research/0"
    assert abs(float(r.headers["x-mock-wait-s"]) - 0.03) < 1e-6
    assert r.json()["usage"] == {"prompt_tokens": 2300, "completion_tokens": 95, "total_tokens": 2395}
    # an unrecorded archetype falls back to the same role in any archetype
    r = client.post("/v1/chat/completions", json=_body("You are a technical writing specialist.", "Write the final brief for: Using ONLY the four items below"))
    assert r.headers["x-mock-profile"] == "digest/writing/0" and r.json()["usage"]["completion_tokens"] == 300
    monkeypatch.delenv("CAPACITY_SERVING_PROFILE")


def test_fingerprint_names_the_profile(monkeypatch):
    from backend.capacity.controller import CapacityTest
    monkeypatch.setenv("CAPACITY_SERVING_PROFILE", "/x/serving/llama70b-together/calls.jsonl")
    monkeypatch.setenv("CAPACITY_SERVING_CONCURRENCY", "32")
    assert "|svc=profile:llama70b-together@32" in CapacityTest._serving_tier_tag()


def test_replay_parses_a_streamed_response(tmp_path):
    """The cloud replay measures time to first token from the stream and
    takes token counts from the final usage chunk; a 429 is retried."""
    import asyncio
    import importlib.util
    import httpx
    spec = importlib.util.spec_from_file_location("replay_query_set", "scripts/replay_query_set.py")
    rq = importlib.util.module_from_spec(spec); spec.loader.exec_module(rq)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, json={"error": "slow down"})
        chunks = [
            {"choices": [{"delta": {"content": "Hello"}, "finish_reason": None}]},
            {"choices": [{"delta": {"content": " world"}, "finish_reason": "stop"}]},
            {"choices": [], "usage": {"prompt_tokens": 1234, "completion_tokens": 56}},
        ]
        body = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
        return httpx.Response(200, content=body.encode(), headers={"content-type": "text/event-stream"})

    async def main():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            item = {"key": "digest/writing/0", "archetype": "digest", "role": "writing", "phase": "0",
                    "messages": [{"role": "user", "content": "hi"}], "tools": None}
            return await rq.one_call(client, "http://x/v1", "m", item, asyncio.Semaphore(1), 8, None)
    row = asyncio.run(main())
    assert row["ok"] and row["retries"] == 1 and row["prompt_tokens"] == 1234 and row["completion_tokens"] == 56
    assert row["message"]["content"] == "Hello world" and row["ttft_ms"] <= row["total_ms"] and row["concurrency"] == 8
    md = rq.summarize([row], {8: 2.0})
    assert "| 8 | 1 | 1 | 1 |" in md and "| writing |" in md
