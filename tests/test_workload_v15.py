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


def test_researcher_prompt_carries_a_seeded_sectioned_corpus():
    test = ctl.CapacityTest("e2e", [], _cfg(seed=42), mix="tile")
    wf = test.scenarios["research_brief"]
    q1 = test._workflow_query(wf, "research_brief", 1)
    q2 = test._workflow_query(wf, "research_brief", 2)
    assert q1.count("### SECTION") == 3
    # sized to the profile: ~24k tokens at ~0.75 words/token
    words = len(q1.split())
    assert 12_000 < words < 30_000
    # same run, same type: identical corpus bodies, different unit salt
    assert q1.split("[retrieval-salt")[0].split("### SECTION")[1][:200] \
        == q2.split("[retrieval-salt")[0].split("### SECTION")[1][:200]
    assert "[retrieval-salt" in q1 and "[retrieval-salt" in q2
    salt1 = q1.split("[retrieval-salt ")[1][:16]
    salt2 = q2.split("[retrieval-salt ")[1][:16]
    assert salt1 != salt2


def test_digest_prompt_stays_light():
    test = ctl.CapacityTest("e2e", [], _cfg(seed=42), mix="tile")
    wf = test.scenarios["digest"]
    q = test._workflow_query(wf, "digest", 1)
    assert "### SECTION" not in q
    assert len(q.split()) < 500


def test_corpus_is_deterministic_across_instances_with_same_seed():
    a = ctl.CapacityTest("e2e", [], _cfg(seed=7), mix="tile")
    b = ctl.CapacityTest("e2e", [], _cfg(seed=7), mix="tile")
    wf = a.scenarios["research_brief"]
    assert a._workflow_query(wf, "research_brief", 3) \
        == b._workflow_query(wf, "research_brief", 3)


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
           "trace": {"task_count": 3, "steps": 3, "llm_calls": 10,
                     "validations": 7, "tool_calls": 3}}
    test._check_contract("research_brief", rec)
    assert rec.get("invalid") is True          # 100 << the 30k floor
    rec2 = {"ok": True, "tokens_in": 55_000,
            "trace": {"task_count": 3, "steps": 3, "llm_calls": 10,
                      "validations": 7, "tool_calls": 3}}
    test._check_contract("research_brief", rec2)
    assert rec2.get("invalid") is None and rec2["ok"] is True
