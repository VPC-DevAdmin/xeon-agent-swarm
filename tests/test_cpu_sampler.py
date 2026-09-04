"""Process-family CPU attribution: totals per family, per-executor spread."""


def test_sampler_reports_family_totals_and_executor_spread(monkeypatch):
    from backend.capacity import telemetry as t
    totals = iter([1000, 2000])            # 1000 jiffies of host time between samples
    jiff = {1: [100, 100], 2: [0, 50], 3: [0, 10], 4: [0, 10], 9: [0, 200]}   # pid -> [t0, t1]
    child = {2: [0, 300], 3: [0, 100], 4: [0, 0]}
    tick = {"i": 0}
    monkeypatch.setattr(t, "_read_proc_stat_total", lambda: next(totals))
    monkeypatch.setattr(t, "_proc_jiffies", lambda pid: jiff[pid][tick["i"]] if pid in jiff else None)
    monkeypatch.setattr(t, "_proc_child_jiffies", lambda pid: child[pid][tick["i"]] if pid in child else None)
    monkeypatch.setattr(t.os, "cpu_count", lambda: 8)
    s = t.ProcessCpuSampler()
    groups = {"control": [1], "executors": [2, 3, 4], "database": [9]}
    assert s.sample(groups, {"sandbox": [2, 3, 4]}, ("executors",)) is None   # no delta yet
    tick["i"] = 1
    out = s.sample(groups, {"sandbox": [2, 3, 4]}, ("executors",))
    assert out["control"] == 0.0
    assert out["executors"] == 7.0            # (50+10+10)/1000 of the host
    assert out["database"] == 20.0
    assert out["sandbox"] == 40.0             # reaped children: (300+100)/1000
    # per executor, percent of ONE thread on an 8-thread host: 50 -> 40%, 10 -> 8%
    assert out["executors_spread"] == {"n": 3, "min": 8.0, "p50": 8.0, "max": 40.0}
