"""The offline judge: verdicts as a pure function over an evidence ledger.

The ledger exists so judgment can be revised without re-running load. These
tests pin the post-1 rules to the failure that motivated them: seed 20690
certified 3,756 sessions while p95 sat at 160 s against a 15 s deadline,
because drift-based stability had no absolute anchor and the final hold
only saw fresh survivors after timeouts culled the backlog.
"""
import gzip
import json

from backend.capacity.evidence import EvidenceWriter, read_evidence
from backend.capacity import judge


def _write(path, rows):
    with gzip.open(path, "wt") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


def _ledger(path, levels):
    """Build a ledger from [(users, t0, per-sid [(lat_s, ok), ...]), ...]."""
    rows = [{"k": "header", "seed": 1, "mode": "e2e",
             "capability_target": 0.95, "capability_confidence": 0.95}]
    for users, t0, by_sid in levels:
        rows.append({"k": "sample", "ts": t0, "users": users})
        for sid, units in by_sid.items():
            for i, (lat_s, ok) in enumerate(units):
                sub = t0 + 0.001 * i
                rows.append({"k": "unit", "sid": sid, "ok": ok,
                             "lat": lat_s * 1000.0, "sub": sub,
                             "end": sub + lat_s})
    rows.append({"k": "footer", "deadline_s": 15.0, "live_verdict": "unstable"})
    _write(path, rows)
    return path


def test_evidence_writer_round_trip(tmp_path):
    w = EvidenceWriter(tmp_path / "ev.jsonl.gz")
    w.write("header", {"seed": 7})
    w.unit({"scenario": "digest", "ok": True, "latency_ms": 1200.5,
            "t_submit": 10.0, "ts": 11.2})
    w.unit({"scenario": "digest", "ok": False, "latency_ms": None,
            "t_submit": 12.0, "ts": 42.0, "error": "workflow timeout after 30s"})
    w.write("footer", {"deadline_s": 15.0})
    info = w.close()
    assert info["rows"] == 4 and info["sha256"]
    ev = read_evidence(info["path"])
    assert ev["header"]["seed"] == 7
    assert len(ev["units"]) == 2
    assert ev["units"][1]["err"].startswith("workflow timeout")
    assert ev["footer"]["deadline_s"] == 15.0


def _perfect(n, lat_s=3.0):
    return [(lat_s, True)] * n


def test_judge_certifies_on_deadline_levels_and_blocks_at_first_failure(
        tmp_path):
    """The child-2 shape in miniature: healthy levels, then a level whose
    units complete fine but far past the deadline, then a culled-queue hold
    of fresh fast survivors. post-1 certifies the last healthy level and
    refuses everything at or above the first failing one."""
    sids = ("research_brief", "comparison", "digest")
    healthy = {s: _perfect(90) for s in sids}
    boiled = {s: [(160.0, True)] * 90 for s in sids}   # ok but 10x deadline
    survivors = {s: _perfect(90) for s in sids}         # fresh, fast, biased
    path = _ledger(tmp_path / "ev.jsonl.gz", [
        (1200, 100.0, healthy),
        (3750, 200.0, boiled),
        (3756, 300.0, survivors),
    ])
    j = judge.judge_evidence(path)
    assert j["capability_users"] == 1200
    assert j["first_failing_level"] == 3750
    by_users = {row["users"]: row for row in j["levels"]}
    assert by_users[3750]["fail"] is True
    # The survivor hold PASSES in isolation, and is still not certified,
    # because certification cannot climb past a failing level.
    assert by_users[3756]["pass"] is True


def test_judge_counts_timeouts_as_decided_failures(tmp_path):
    sids = ("research_brief", "comparison", "digest")
    mixed = {s: _perfect(80) + [(None, False)] * 10 for s in sids}
    mixed = {s: [((lat if lat is not None else 0.0), ok) for lat, ok in v]
             for s, v in mixed.items()}
    path = _ledger(tmp_path / "ev.jsonl.gz", [(500, 100.0, mixed)])
    j = judge.judge_evidence(path)
    row = j["levels"][0]
    assert row["per_type"]["digest"]["decided"] == 90
    assert row["per_type"]["digest"]["on_time"] == 80
    assert j["capability_users"] is None
    assert j["first_failing_level"] == 500


def test_judge_thin_evidence_neither_passes_nor_blocks(tmp_path):
    """A perfect record too small to reach the bound is silence: the level
    above it can still certify."""
    sids = ("research_brief", "comparison", "digest")
    thin = {s: _perfect(10) for s in sids}
    thick = {s: _perfect(90) for s in sids}
    path = _ledger(tmp_path / "ev.jsonl.gz", [
        (100, 100.0, thin),
        (200, 200.0, thick),
    ])
    j = judge.judge_evidence(path)
    assert j["capability_users"] == 200
    assert j["first_failing_level"] is None


def test_judge_is_deterministic(tmp_path):
    sids = ("research_brief", "comparison", "digest")
    path = _ledger(tmp_path / "ev.jsonl.gz",
                   [(100, 100.0, {s: _perfect(90) for s in sids})])
    a = judge.judge_evidence(path)
    b = judge.judge_evidence(path)
    assert a == b
    assert a["judge_version"] == judge.JUDGE_VERSION


def test_single_failure_at_thin_level_does_not_block(tmp_path):
    """post-2: one spurious failure in a thin ramp level (the fleet's 13
    DB-read hiccups) is insufficient evidence, not refutation. The level
    goes silent and higher levels still certify."""
    sids = ("research_brief", "comparison", "digest")
    thin_blip = {s: _perfect(80) for s in sids}
    thin_blip["digest"] = _perfect(79) + [(0.0, False)]   # 79/80, one hiccup
    thick = {s: _perfect(90) for s in sids}
    path = _ledger(tmp_path / "ev.jsonl.gz", [
        (295, 100.0, thin_blip),
        (654, 200.0, thick),
    ])
    j = judge.judge_evidence(path)
    assert j["first_failing_level"] is None
    assert j["capability_users"] == 654
    by_users = {row["users"]: row for row in j["levels"]}
    assert by_users[295]["pass"] is False
    assert by_users[295]["fail"] is False
