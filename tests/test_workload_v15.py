"""Workload v15: context-weighted archetypes with realistic slicing.

The archetypes differ by retrieval-context weight (researcher heavy,
comparison medium, digest light), the planner reads the corpus once, and
each worker carries only its assigned section. The corpus is seeded and
salted per unit so a prefix cache cannot deduplicate it across units.
"""
import importlib.util
import pathlib

from backend.capacity import controller as ctl
from tests.test_capacity_metrics import _cfg


def _mock_router():
    path = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "mock_router.py"
    spec = importlib.util.spec_from_file_location("mock_router_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_context_profile_builds_a_seeded_sectioned_corpus():
    """The prompt-corpus mechanism (v15) stays available for archetypes that
    declare a context_profile; in v16.1 every retrieving archetype earns its
    context instead, so this exercises the mechanism on a synthetic type."""
    test = ctl.CapacityTest("e2e", [], _cfg(seed=42), mix="tile")
    wf = {"query": "Do the synthetic task.",
          "context_profile": {"tokens_in": 7500, "sections": 3}}
    q1 = test._workflow_query(wf, "synthetic", 1)
    q2 = test._workflow_query(wf, "synthetic", 2)
    assert q1.count("### SECTION") == 3
    words = len(q1.split())
    assert 3_000 < words < 9_000
    assert q1.split("[retrieval-salt")[0].split("### SECTION")[1][:200] \
        == q2.split("[retrieval-salt")[0].split("### SECTION")[1][:200]
    salt1 = q1.split("[retrieval-salt ")[1][:16]
    salt2 = q2.split("[retrieval-salt ")[1][:16]
    assert salt1 != salt2


def test_task_agent_prompt_stays_light():
    test = ctl.CapacityTest("e2e", [], _cfg(seed=42), mix="tile")
    wf = test.scenarios["task_ticket"]
    q = test._workflow_query(wf, "task_ticket", 1)
    assert "### SECTION" not in q
    assert len(q.split()) < 500


def test_corpus_is_deterministic_across_instances_with_same_seed():
    a = ctl.CapacityTest("e2e", [], _cfg(seed=7), mix="tile")
    b = ctl.CapacityTest("e2e", [], _cfg(seed=7), mix="tile")
    wf = {"query": "Do the synthetic task.",
          "context_profile": {"tokens_in": 3000, "sections": 3}}
    assert a._workflow_query(wf, "synthetic", 3) \
        == b._workflow_query(wf, "synthetic", 3)


def test_mock_planner_hands_each_worker_only_its_section():
    mr = _mock_router()
    obj = ("Do the task.\n"
           "### SECTION A ###\n[retrieval-salt x-A]\nalpha corpus body\n"
           "### SECTION B ###\n[retrieval-salt x-B]\nbeta corpus body\n"
           "### SECTION C ###\n[retrieval-salt x-C]\ngamma corpus body")
    assert "alpha corpus body" in mr._worker_slice(obj, "research")
    assert "beta corpus body" in mr._worker_slice(obj, "analysis")
    assert "gamma corpus body" in mr._worker_slice(obj, "writing")
    assert "beta" not in mr._worker_slice(obj, "research")
    assert mr._worker_slice(obj, "tool_user") is None
    assert mr._worker_slice("no sections here", "research") is None


def test_contract_tokens_in_reads_from_the_record():
    test = ctl.CapacityTest("e2e", [], _cfg(), mix="tile")
    rec = {"ok": True, "tokens_in": 100,
           "trace": {"task_count": 3, "steps": 3, "llm_calls": 13,
                     "validations": 7, "tool_calls": 6}}
    test._check_contract("deep_research", rec)
    assert rec.get("invalid") is True          # 100 << the 30k floor
    rec2 = {"ok": True, "tokens_in": 55_000,
            "trace": {"task_count": 3, "steps": 3, "llm_calls": 13,
                      "validations": 7, "tool_calls": 6}}
    test._check_contract("deep_research", rec2)
    assert rec2.get("invalid") is None and rec2["ok"] is True


def test_model_wait_scales_with_payload(monkeypatch):
    """The modeled serving tier waits per the actual payload: a 24k-token
    planner call waits several times longer than a judge verdict, from the
    same three tier parameters."""
    mr = _mock_router()
    monkeypatch.setenv("CAPACITY_MODEL_TTFT_MS", "500")
    monkeypatch.setenv("CAPACITY_MODEL_DECODE_TPS", "100")
    monkeypatch.setenv("CAPACITY_MODEL_PREFILL_TPS", "8000")
    heavy = [{"role": "user", "content": "x" * 96_000}]     # ~24k tokens in
    light = [{"role": "user", "content": "x" * 2_000}]      # ~500 tokens in
    out = "y" * 800                                          # ~200 tokens out
    wh = mr._model_wait_s(heavy, out, "s1")
    wl = mr._model_wait_s(light, out, "s1")
    # heavy ~ 0.5 + 2 + 3 = 5.5s +/-20%; light ~ 0.5 + 2 + 0.06 = 2.56s
    assert 4.4 <= wh <= 6.6
    assert 2.0 <= wl <= 3.1
    assert wh > wl * 1.7


def test_model_wait_defaults_to_zero(monkeypatch):
    mr = _mock_router()
    for k in ("CAPACITY_MODEL_TTFT_MS", "CAPACITY_MODEL_DECODE_TPS",
              "CAPACITY_MODEL_PREFILL_TPS"):
        monkeypatch.delenv(k, raising=False)
    assert mr._model_wait_s([{"role": "user", "content": "x" * 9000}],
                            "y" * 900, "s") == 0.0


def test_serving_tier_changes_the_machine_fingerprint(monkeypatch):
    a = ctl.CapacityTest("e2e", [], _cfg(seed=1), mix="tile")
    for k in ("CAPACITY_MODEL_TTFT_MS", "CAPACITY_MODEL_DECODE_TPS",
              "CAPACITY_MODEL_PREFILL_TPS"):
        monkeypatch.delenv(k, raising=False)
    fp_instant = a._machine_fingerprint()
    monkeypatch.setenv("CAPACITY_MODEL_TTFT_MS", "500")
    monkeypatch.setenv("CAPACITY_MODEL_DECODE_TPS", "100")
    monkeypatch.setenv("CAPACITY_MODEL_PREFILL_TPS", "8000")
    assert a._machine_fingerprint() != fp_instant
