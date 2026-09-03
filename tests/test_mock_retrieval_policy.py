"""Workload v16.1: retrieval policy by archetype and grounded citations.

The researcher retrieves in every worker; the comparison retrieves in its
research worker only; the digest never retrieves. A worker that retrieved
cites the [chunk-N] ids it was actually handed.
"""
from fastapi.testclient import TestClient

from tests.test_workload_v15 import _mock_router

RESEARCH_SYS = "You are a research specialist. Gather facts."
ANALYSIS_SYS = "You are an analysis specialist. Compare options."
RESEARCHER_OBJ = "Research the topic: Using ONLY the field notes below, brief on X"
COMPARISON_OBJ = "Analyze the research findings for: Using ONLY the measurements below, compare INT8 and FP32"
DIGEST_OBJ = "Write the final brief for: Summarize the week's changes"
TOOLS = [{"type": "function", "function": {"name": n, "parameters": {}}}
         for n in ("bench_retrieve", "bench_record")]


def _client():
    return TestClient(_mock_router().app)


def _first_tool(client, system, obj, tools=TOOLS):
    r = client.post("/v1/chat/completions", json={
        "model": "auto", "tools": tools,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": obj}]})
    assert r.status_code == 200, r.text
    msg = r.json()["choices"][0]["message"]
    calls = msg.get("tool_calls") or []
    return calls[0]["function"]["name"] if calls else None


def test_researcher_retrieves_in_every_worker():
    c = _client()
    assert _first_tool(c, RESEARCH_SYS, RESEARCHER_OBJ) == "bench_retrieve"
    assert _first_tool(c, ANALYSIS_SYS, RESEARCHER_OBJ) == "bench_retrieve"


def test_comparison_retrieves_in_research_worker_only():
    c = _client()
    assert _first_tool(c, RESEARCH_SYS, COMPARISON_OBJ) == "bench_retrieve"
    assert _first_tool(c, ANALYSIS_SYS, COMPARISON_OBJ) == "bench_record"


def test_digest_only_records():
    c = _client()
    record_only = [t for t in TOOLS if t["function"]["name"] == "bench_record"]
    assert _first_tool(c, ANALYSIS_SYS, DIGEST_OBJ, record_only) == "bench_record"


def test_worker_cites_the_chunks_it_was_given():
    c = _client()
    messages = [
        {"role": "system", "content": RESEARCH_SYS},
        {"role": "user", "content": RESEARCHER_OBJ},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "type": "function",
             "function": {"name": "bench_retrieve", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "name": "bench_retrieve",
         "content": "[bench_retrieve] 12 chunks\n[chunk-4021] alpha\n"
                    "[chunk-77] beta\n[chunk-90210] gamma\nRETRIEVAL COMPLETE"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c2", "type": "function",
             "function": {"name": "bench_record", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c2", "name": "bench_record",
         "content": "RECORD COMMITTED"},
    ]
    r = c.post("/v1/chat/completions",
               json={"model": "auto", "tools": TOOLS, "messages": messages})
    msg = r.json()["choices"][0]["message"]
    assert not msg.get("tool_calls")
    assert "Sources: [chunk-4021] [chunk-77] [chunk-90210]." in msg["content"]
