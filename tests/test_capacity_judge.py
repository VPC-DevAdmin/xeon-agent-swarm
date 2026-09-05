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


def _sweep_ledger(path, phases):
    """phases: [(rate_per_s, duration_s, lat_s), ...] one type per third."""
    rows = [{"k": "header", "seed": 1, "mode": "e2e"}]
    sids = ("research_brief", "comparison", "digest")
    t = 1000.0
    for rate, dur, lat_s in phases:
        n = int(rate * dur)
        for i in range(n):
            sub = t + i / rate
            rows.append({"k": "unit", "sid": sids[i % 3], "ok": True,
                         "lat": lat_s * 1000.0, "sub": sub,
                         "end": sub + lat_s, "r": rate})
        t += dur
    rows.append({"k": "footer", "deadline_s": 15.0})
    _write(path, rows)
    return path


def test_sweep_finds_the_sustainable_rate_per_tier(tmp_path):
    """Three offered rates: healthy fast, healthy slow (inside 45s but past
    15s), and collapsing. The conversational tier's sustainable rate is the
    first phase's; the interactive tier also sustains the second."""
    path = _sweep_ledger(tmp_path / "ev.jsonl.gz", [
        (20.0, 120, 3.0),     # 20/s, 3s latency: fine for every tier
        (40.0, 120, 30.0),    # 40/s, 30s latency: misses 15s, inside 45s
        (80.0, 120, 200.0),   # collapse: misses everything
    ])
    s = judge.sweep(path, window_s=30.0)
    conv = s["tiers"]["conversational"]
    inter = s["tiers"]["interactive"]
    assert conv["confirmed"] and abs(conv["sustainable_rate"] - 20.0) < 2.0
    assert inter["confirmed"] and abs(inter["sustainable_rate"] - 40.0) < 4.0
    # derived sessions: rate x (latency + think)
    assert abs(conv["derived_sessions"] - 20 * (3 + 3)) <= 20
    assert abs(inter["derived_sessions"] - 40 * (30 + 3)) <= 140


def test_sweep_requires_two_confirming_windows(tmp_path):
    """A single 15-second spike at a high rate is not sustainable."""
    path = _sweep_ledger(tmp_path / "ev.jsonl.gz", [
        (10.0, 120, 3.0),
        (100.0, 15, 3.0),     # one half-window burst
    ])
    s = judge.sweep(path, window_s=30.0)
    conv = s["tiers"]["conversational"]
    assert conv["confirmed"]
    assert conv["sustainable_rate"] < 50.0


def test_plateau_judges_one_held_rate_as_a_cohort(tmp_path):
    """A flat, on-time plateau at 2/s: sweep-2's 30 s windows cannot certify
    it (20 units per type per window bound below the target), the plateau
    rule pools the whole steady cohort and can. Two ledgers pool."""
    import gzip, json
    from backend.capacity import judge

    def ledger(path, seed):
        rows = [{"k": "header", "capability_target": 0.95,
                 "capability_confidence": 0.95}]
        sids = ["research_brief", "comparison", "digest"]
        t = 1000.0 + seed
        for i in range(600):                       # 300 s at 2/s
            sid = sids[i % 3]
            lat = 30000.0 + (i % 7) * 500.0
            rows.append({"k": "unit", "sid": sid, "ok": True, "lat": lat,
                         "sub": t, "end": t + lat / 1000.0})
            t += 0.5
        rows.append({"k": "footer"})
        with gzip.open(path, "wt") as fh:
            for r in rows:
                fh.write(json.dumps(r) + "\n")
        return path

    a = ledger(tmp_path / "a.jsonl.gz", 0)
    b = ledger(tmp_path / "b.jsonl.gz", 0.25)
    s = judge.sweep(a)
    assert not any(v.get("sustainable_rate") for v in s["tiers"].values())
    p = judge.plateau([a, b])
    assert p["units"] == 1200 and p["ledgers"] == 2
    assert 3.6 <= p["rate"] <= 4.1                 # two 2/s ledgers pooled
    assert p["keeps_up"] is True
    assert "interactive" in p["sustained_tiers"]   # 45 s deadline, 30-33 s latency
    assert "conversational" not in p["sustained_tiers"]
    # Little's law: ~4/s x (31.5 s mean + 3 s think) ~ 138 resident
    assert 120 <= p["resident_sessions"] <= 150


def test_plateau_censors_a_generator_that_fell_behind(tmp_path):
    """Unit rows carry the offered rate; a cohort whose achieved arrival
    rate is under 95% of it is a generator-limited plateau: no tier is
    sustained, whatever the latencies say."""
    import gzip, json
    from backend.capacity import judge

    def ledger(path, offered, actual):
        rows = [{"k": "header"}]
        t = 1000.0
        for i in range(1500):
            rows.append({"k": "unit", "sid": ["a", "b", "c"][i % 3], "ok": True,
                         "lat": 30000.0, "sub": t, "end": t + 30.0, "r": offered})
            t += 1.0 / actual
        rows.append({"k": "footer"})
        with gzip.open(path, "wt") as fh:
            for r in rows:
                fh.write(json.dumps(r) + "\n")
        return path

    lagging = judge.plateau([ledger(tmp_path / "lag.jsonl.gz", 4.0, 3.0)])
    assert lagging["generator_ok"] is False and lagging["sustained_tiers"] == []
    honest = judge.plateau([ledger(tmp_path / "ok.jsonl.gz", 4.0, 3.95)])
    assert honest["generator_ok"] is True and "interactive" in honest["sustained_tiers"]


def test_read_evidence_survives_a_truncated_ledger(tmp_path):
    import gzip, json
    from backend.capacity.evidence import read_evidence
    p = tmp_path / "t.jsonl.gz"
    with gzip.open(p, "wt") as fh:
        fh.write(json.dumps({"k": "header"}) + "\n")
        for i in range(200):
            fh.write(json.dumps({"k": "unit", "sid": "a", "ok": True, "lat": 1.0,
                                 "sub": 1000.0 + i, "end": 1001.0 + i}) + "\n")
    raw = p.read_bytes()
    p.write_bytes(raw[: len(raw) - 40])          # lose the trailer and a tail
    ev = read_evidence(p)
    assert ev["truncated"] is True
    assert ev["header"] is not None and 150 < len(ev["units"]) <= 200


def test_plateau_counts_units_in_flight_at_the_end(tmp_path):
    """Censored units are arrivals: they hold the achieved rate honest and
    the backlog visible; for each tier they are pending (excluded) while
    younger than its deadline and late once older."""
    import gzip, json
    from backend.capacity import judge
    rows = [{"k": "header"}]
    t = 1000.0
    for i in range(1200):                      # 300 s at 4/s, 30 s latency
        rows.append({"k": "unit", "sid": ["a", "b", "c"][i % 3], "ok": True,
                     "lat": 30000.0, "sub": t, "end": t + 30.0, "r": 4.0})
        t += 0.25
    end = t
    for i in range(400):                       # last 100 s of arrivals still running
        rows.append({"k": "unit", "sid": ["a", "b", "c"][i % 3], "ok": False,
                     "lat": None, "sub": t, "end": None, "err": "inflight_at_end", "r": 4.0})
        t += 0.25
    rows.append({"k": "footer", "ended_at": t})
    p = tmp_path / "c.jsonl.gz"
    with gzip.open(p, "wt") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    pj = judge.plateau([p])
    assert pj["inflight_at_end"] == 400
    assert pj["generator_ok"] is True            # 4/s achieved incl. censored arrivals
    assert pj["keeps_up"] is False               # 400 admitted, none finished: backlog grew
    # interactive (45 s): every censored unit is younger than 45 s at the end
    # except the oldest ~55 s worth -> those count late; the bound still fails
    # only through keeps_up. Pending exclusion keeps the bound itself honest.
    assert pj["tiers"]["background"]["bounds"]["a"] >= 0.95


def test_plateau_reports_on_time_counts_per_tier(tmp_path):
    """A set pools its series' cohort counts for the joint bound; the
    per-tier counts ride the judgment for that."""
    import gzip, json
    from backend.capacity import judge
    rows = [{"k": "header"}]
    t = 1000.0
    for i in range(200):
        rows.append({"k": "unit", "sid": "a" if i % 2 else "b", "ok": True, "lat": 30000.0,
                     "sub": t, "end": t + 30.0, "r": 2.0})
        t += 0.5
    rows.append({"k": "footer"})
    p = tmp_path / "e.jsonl.gz"
    with gzip.open(p, "wt") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    j = judge.plateau([p])
    c = j["tiers"]["interactive"]["counts"]
    assert set(c) == {"a", "b"} and all(w == n and n > 40 for w, n in c.values())
