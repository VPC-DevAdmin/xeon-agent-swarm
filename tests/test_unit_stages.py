"""Per-unit stage accounting: executor accumulator -> trace -> ledger row ->
per-archetype breakdown."""
import asyncio
import gzip
import json


def test_accumulator_follows_the_run_context_into_child_tasks_and_threads():
    from backend.capacity import stages

    async def run(rid, ms):
        stages.begin(rid)
        stages.note("retrieve_ms", ms)
        await asyncio.to_thread(stages.note, "sandbox_heavy_cpu_ms", 2 * ms)   # thread copies context
        await asyncio.create_task(_child(ms))                                  # child task inherits it
        return stages.collect(rid)

    async def _child(ms):
        stages.note("retrieve_ms", ms)

    async def main():
        return await asyncio.gather(run("a", 100.0), run("b", 5.0))

    a, b = asyncio.run(main())
    assert a == {"retrieve_ms": {"ms": 200.0, "n": 2}, "sandbox_heavy_cpu_ms": {"ms": 200.0, "n": 1}}
    assert b == {"retrieve_ms": {"ms": 10.0, "n": 2}, "sandbox_heavy_cpu_ms": {"ms": 10.0, "n": 1}}
    stages.note("retrieve_ms", 1.0)          # after collect: no run bound in this context -> dropped
    assert stages.collect("a") == {}


def test_retrieval_note_feeds_the_bound_run():
    from backend.capacity import retrieval as rt, stages
    stages.begin("r1")
    rt._note("rerank_call_ms", 170.0)
    rt._note("rerank_call_ms", 190.0)
    assert stages.collect("r1") == {"rerank_call_ms": {"ms": 360.0, "n": 2}}
    rt._stage_samples.clear()


def test_ledger_row_carries_the_unit_stage_sums(tmp_path):
    from backend.capacity.evidence import EvidenceWriter, read_evidence
    p = tmp_path / "e.jsonl.gz"
    w = EvidenceWriter(p)
    w.write("header", {})
    w.unit({"scenario": "research_brief", "ok": True, "latency_ms": 34000.0,
            "t_submit": 10.0, "ts": 44.0, "offered_rate": 9.0,
            "trace": {"llm_calls": 13, "stages": {"retrieve_ms": {"ms": 900.5, "n": 3},
                                                   "model_wait_ms": {"ms": 30000.0, "n": 13}}}})
    w.unit({"scenario": "digest", "ok": True, "latency_ms": 28000.0,
            "t_submit": 11.0, "ts": 39.0, "offered_rate": 9.0, "trace": {"llm_calls": 10}})
    w.write("footer", {})
    w.close()
    ev = read_evidence(p)
    assert ev["units"][0]["st"] == {"retrieve_ms": [900.5, 3], "model_wait_ms": [30000.0, 13]}
    assert "st" not in ev["units"][1]


def test_stage_breakdown_per_archetype(tmp_path):
    from backend.capacity import judge
    rows = [{"k": "header"}]
    t = 1000.0
    for i in range(300):
        if i % 2:
            rows.append({"k": "unit", "sid": "research_brief", "ok": True, "lat": 34000.0, "sub": t,
                         "end": t + 34.0, "r": 4.0,
                         "st": {"retrieve_ms": [900.0 + i, 3], "rerank_call_ms": [510.0, 3],
                                "model_wait_ms": [30000.0, 13]}})
        else:
            rows.append({"k": "unit", "sid": "data_analysis", "ok": True, "lat": 36000.0, "sub": t,
                         "end": t + 36.0, "r": 4.0,
                         "st": {"sandbox_heavy_wall_ms": [4700.0, 3], "sandbox_heavy_cpu_ms": [4400.0, 3],
                                "model_wait_ms": [31000.0, 13]}})
        t += 0.25
    rows.append({"k": "footer"})
    p = tmp_path / "e.jsonl.gz"
    with gzip.open(p, "wt") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    s = judge.stages([p])
    r = s["per_type"]["research_brief"]
    assert r["retrieval"]["calls"] == 3 and 0.9 <= r["retrieval"]["p50_s"] <= 1.3
    assert r["rerank_call"]["p50_s"] == 0.51 and "sandbox_wall" not in r
    a = s["per_type"]["data_analysis"]
    assert a["sandbox_wall"]["p50_s"] == 4.7 and a["sandbox_cpu"]["p50_s"] == 4.4
    assert a["model_wait"]["calls"] == 13 and "retrieval" not in a
    table = judge.stages_table([p])
    assert "| research_brief |" in table and "4.7 (3 calls)" in table
