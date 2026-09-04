"""The CPU-heavy mix: job kinds, tool wiring, stand-in policies, tile selection."""
import asyncio
import json
import os
import subprocess
import sys

import pytest

from backend.capacity import sandbox


@pytest.fixture(autouse=True)
def _rlimits(monkeypatch):
    monkeypatch.setenv("CAPACITY_SANDBOX_ISOLATION", "rlimits")
    sandbox._mode = None


def test_build_job_compiles_and_its_tests_pass(monkeypatch):
    monkeypatch.setattr(sandbox, "BUILD_WORK", 20_000)
    r = asyncio.run(sandbox.run_job("build", 11))
    assert r["ok"] and r["failures"] == 0 and r["tests"] == 721
    assert r["functions"] == 1440 and r["lines"] > 10000 and r["cpu_ms"] > 0
    again = asyncio.run(sandbox.run_job("build", 11))
    assert again["digest"] == r["digest"]                      # deterministic for a seed


def test_ops_job_repairs_the_repository_and_proves_the_service():
    r = asyncio.run(sandbox.run_job("ops", 5))
    assert r["ok"] and r["commits"] == 4 and r["conflicts"] == 6 == r["resolved"]
    assert r["failures"] == 0 and r["service_ok"] is True and r["checks"] >= 10


def test_ingest_job_parses_and_chunks(tmp_path, monkeypatch):
    subprocess.run([sys.executable, "scripts/make_ingest_docs.py", "--docs", "2", "--pages", "6",
                    "--out", str(tmp_path)], check=True, capture_output=True)
    monkeypatch.setattr(sandbox, "INGEST_DOCS", str(tmp_path))
    monkeypatch.setattr(sandbox, "INGEST_PAGES", 8)
    r = asyncio.run(sandbox.run_job("ingest", 2))
    assert r["ok"] and r["pages"] == 8 and r["docs"] == 2 and r["chunks"] > 20
    assert all(len(t.split()) <= 180 for t in r["texts"])


def test_xl_size_is_the_data_job_at_nine_times_the_rows():
    assert sandbox.SIZES["xl"] == 60_000_000
    assert sandbox.wall_limit("xl") > sandbox.wall_limit("heavy")


def test_execute_tool_reports_each_kind(tmp_path, monkeypatch):
    from backend.agents.toolbox import build_bench_execute_tool
    monkeypatch.setattr(sandbox, "BUILD_WORK", 20_000)
    tool = build_bench_execute_tool()
    out = asyncio.run(tool.coroutine(task="build the library", size="build"))
    assert out.startswith("[bench_execute] build:") and " 0 failures" in out and "EXECUTION COMPLETE" in out
    out = asyncio.run(tool.coroutine(task="repair the service", size="ops"))
    assert "service up and answering" in out
    subprocess.run([sys.executable, "scripts/make_ingest_docs.py", "--docs", "1", "--pages", "4",
                    "--out", str(tmp_path)], check=True, capture_output=True)
    monkeypatch.setattr(sandbox, "INGEST_DOCS", str(tmp_path))
    monkeypatch.setattr(sandbox, "INGEST_PAGES", 4)
    monkeypatch.delenv("CAPACITY_EMBED_URL", raising=False)      # no embedder: parse + index only
    out = asyncio.run(tool.coroutine(task="ingest the reports", size="ingest"))
    assert out.startswith("[bench_execute] ingest: parsed 4 pages") and "indexed in" in out


def test_stand_in_policies_pick_the_kind_and_depth():
    from fastapi.testclient import TestClient
    from tests.test_workload_v15 import _mock_router
    client = TestClient(_mock_router().app)
    exec_tools = [{"type": "function", "function": {"name": n, "parameters": {}}}
                  for n in ("bench_execute", "bench_record")]
    ret_tools = [{"type": "function", "function": {"name": n, "parameters": {}}}
                 for n in ("bench_retrieve", "bench_record")]

    def first_call(system, obj, tools):
        r = client.post("/v1/chat/completions", json={
            "model": "auto", "tools": tools,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": obj}]})
        assert r.status_code == 200, r.text
        calls = r.json()["choices"][0]["message"].get("tool_calls") or []
        fn = calls[0]["function"]
        return fn["name"], json.loads(fn["arguments"])

    research = "You are a research specialist. Gather facts."
    general = "You are a general-purpose specialist."
    for obj, kind in (("Research the topic: Using ONLY the build available through the execution tool", "build"),
                      ("Handle this task end to end: Using ONLY the repository available through the execution tool", "ops"),
                      ("Handle this task end to end: Using ONLY the document set available through the execution tool", "ingest"),
                      ("Research the topic: Using ONLY the dataset (XL) available through the execution tool", "xl"),
                      ("Research the topic: Using ONLY the dataset available through the execution tool", "heavy")):
        name, args = first_call(general if "Handle" in obj else research, obj, exec_tools)
        assert (name, args["size"]) == ("bench_execute", kind), (obj, name, args)
    name, args = first_call(research, "Research the topic: Using ONLY the field notes below, and retrieving at rerank depth 128, write a brief", ret_tools)
    assert name == "bench_retrieve" and args["depth"] == 128
    name, args = first_call(research, "Research the topic: Using ONLY the field notes below, write a brief", ret_tools)
    assert name == "bench_retrieve" and "depth" not in args


def test_heavy_tile_is_selected_by_name(monkeypatch):
    from backend.capacity import scenarios as sc
    monkeypatch.setenv("CAPACITY_E2E_TILE", "heavy")
    sc._file_cache = None
    tile = sc.load_e2e_tile()
    assert tile == {"code_agent": 2, "deep_research": 1, "ingestion": 1, "analyst_xl": 1,
                    "task_ticket": 1, "ops_task": 1}
    assert len(sc.e2e_tile_sessions()) == 7
    wfs = sc.load_e2e_workflows()
    assert wfs["code_agent"]["contract"]["llm_calls"] == [13, 13]
    assert wfs["ops_task"]["contract"]["tool_calls"] == [2, 2]
    monkeypatch.setenv("CAPACITY_E2E_TILE", "nope")
    sc._file_cache = None
    with pytest.raises(KeyError):
        sc.load_e2e_tile()
    monkeypatch.delenv("CAPACITY_E2E_TILE")
    sc._file_cache = None
    assert "research_brief" in sc.load_e2e_tile() and "code_agent" not in sc.load_e2e_tile()


def test_fingerprint_names_the_tile(monkeypatch):
    from backend.capacity.controller import CapacityTest
    monkeypatch.delenv("CAPACITY_E2E_TILE", raising=False)
    base = CapacityTest._serving_tier_tag()
    assert "|tile=" not in base
    monkeypatch.setenv("CAPACITY_E2E_TILE", "heavy")
    assert CapacityTest._serving_tier_tag().endswith("|tile=heavy")
