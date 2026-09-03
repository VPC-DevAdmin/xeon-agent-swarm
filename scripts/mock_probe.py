"""Probe an instance's model router from outside the executors while a run
is on: time a research-worker-shaped call (tool result of ~6,000 words in
context) and a task-agent-shaped call every few seconds, and report the
latency against the modeled wait the router itself computes, so router
queueing can be told apart from executor-side delay.

    python scripts/mock_probe.py http://127.0.0.1:8921 <seconds> <out.log>
"""
import json
import sys
import time
import urllib.request

base, dur, out = sys.argv[1], float(sys.argv[2]), sys.argv[3]
words = ("Record covers throughput cache scheduler operations for topic42 workloads. " * 12)
retrieved = "\n".join(f"[chunk-{i}] {words}" for i in range(12))    # ~6,000 words
heavy = {"model": "auto", "tools": [{"type": "function", "function": {"name": n, "parameters": {}}} for n in ("bench_retrieve", "bench_record")],
         "messages": [
             {"role": "system", "content": "You are a research specialist. Gather facts."},
             {"role": "user", "content": "Research the topic: Using ONLY the field notes below (do not research further), write a brief on CPU-only inference. Plan EXACTLY three worker subtasks and no more."},
             {"role": "assistant", "content": None, "tool_calls": [{"id": "p1", "type": "function", "function": {"name": "bench_retrieve", "arguments": "{}"}}]},
             {"role": "tool", "tool_call_id": "p1", "name": "bench_retrieve", "content": "[bench_retrieve] 12 chunks\n" + retrieved + "\nRETRIEVAL COMPLETE"},
             {"role": "assistant", "content": None, "tool_calls": [{"id": "p2", "type": "function", "function": {"name": "bench_record", "arguments": "{}"}}]},
             {"role": "tool", "tool_call_id": "p2", "name": "bench_record", "content": "RECORD COMPLETE"}]}
light = {"model": "auto", "tools": [{"type": "function", "function": {"name": "bench_record", "parameters": {}}}],
         "messages": [{"role": "system", "content": "You are a general-purpose reasoning agent."},
                      {"role": "user", "content": "Handle this single support ticket. Plan EXACTLY one worker subtask and no more."},
                      {"role": "assistant", "content": None, "tool_calls": [{"id": "q1", "type": "function", "function": {"name": "bench_record", "arguments": "{}"}}]},
                      {"role": "tool", "tool_call_id": "q1", "name": "bench_record", "content": "RECORD COMPLETE"}]}
def call(body):
    req = urllib.request.Request(base + "/v1/chat/completions", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    t0 = time.perf_counter()
    r = urllib.request.urlopen(req, timeout=120)
    hdr = dict(r.headers); r.read()
    return time.perf_counter() - t0, hdr.get("x-mock-wait-s") or hdr.get("X-Mock-Wait-S")
end = time.time() + dur
with open(out, "a") as fh:
    while time.time() < end:
        for name, body in (("research-worker", heavy), ("task-worker", light)):
            try:
                lat, wait = call(body)
                fh.write(f"{time.time():.0f} {name} latency={lat:.2f}s modeled_wait={wait}\n")
            except Exception as exc:
                fh.write(f"{time.time():.0f} {name} error={type(exc).__name__}\n")
            fh.flush()
        time.sleep(5)
