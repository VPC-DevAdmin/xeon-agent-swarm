"""Tier cost model + decomposed-vs-T5 savings rollup (tier-router migration §5)."""
from backend.observability import cost


def test_call_cost_prices_by_tier():
    # 1000 tokens at T2's default 0.0005/1K = 0.0005
    assert cost.call_cost(1000, "T2") == 0.0005
    # 2000 tokens at T5's 0.030/1K = 0.060
    assert cost.call_cost(2000, "T5") == 0.06


def test_tier_spelling_normalized():
    assert cost.call_cost(1000, "tier2") == cost.call_cost(1000, "T2")
    assert cost.call_cost(1000, "2") == cost.call_cost(1000, "T2")
    # Unknown / missing tier falls back to the baseline (T5).
    assert cost.call_cost(1000, None) == cost.call_cost(1000, "T5")


def test_cache_hit_is_free():
    assert cost.call_cost(5000, "T5", cache_hit=True) == 0.0


def test_rollup_savings_vs_t5_baseline():
    # Three calls, none at T5 → real cost well below the all-T5 baseline.
    calls = [
        {"tokens_out": 1000, "tier_observed": "T1"},
        {"tokens_out": 1000, "tier_observed": "T2"},
        {"tokens_out": 1000, "tier_observed": "T3"},
    ]
    rc = cost.rollup_run(calls)
    # baseline = 3 * 1000/1000 * 0.030 = 0.090
    assert rc.baseline_cost == 0.09
    # total = 0.0001 + 0.0005 + 0.002 = 0.0026
    assert abs(rc.total_cost - 0.0026) < 1e-9
    assert rc.savings_pct > 90          # huge savings vs monolithic T5
    assert rc.call_count == 3
    assert rc.cached_calls == 0


def test_rollup_all_t5_is_zero_savings():
    calls = [{"tokens_out": 1000, "tier_observed": "T5"} for _ in range(3)]
    rc = cost.rollup_run(calls)
    assert rc.total_cost == rc.baseline_cost
    assert rc.savings_pct == 0.0


def test_rollup_counts_cache_hits():
    calls = [
        {"tokens_out": 1000, "tier_observed": "T3", "cache_hit": True},
        {"tokens_out": 1000, "tier_observed": "T3", "cache_hit": False},
    ]
    rc = cost.rollup_run(calls)
    assert rc.cached_calls == 1
    # cached call contributes 0 to total but still counts toward the baseline.
    assert rc.total_cost == cost.call_cost(1000, "T3")
