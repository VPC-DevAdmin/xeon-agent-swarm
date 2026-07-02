"""Per-run routing rollup: tier distribution, cache hits, token totals."""
from backend.observability.routing import normalize_tier, rollup_routing


def test_tier_spelling_normalized():
    assert normalize_tier("tier2") == "T2"
    assert normalize_tier("T2") == "T2"
    assert normalize_tier("2") == "T2"
    assert normalize_tier(None) == "unknown"
    assert normalize_tier("bogus") == "unknown"


def test_rollup_distribution():
    calls = [
        {"tokens_out": 1000, "tier_observed": "T1"},
        {"tokens_out": 500, "tier_observed": "tier1"},
        {"tokens_out": 200, "tier_observed": "T3"},
        {"tokens_out": 900, "tier_observed": "T5"},
    ]
    r = rollup_routing(calls)
    assert r.call_count == 4
    assert r.cached_calls == 0
    assert r.tokens_out == 2600
    assert r.tier_calls == {"T1": 2, "T3": 1, "T5": 1}
    assert r.tier_tokens_out == {"T1": 1500, "T3": 200, "T5": 900}


def test_rollup_counts_cache_hits_in_their_tier():
    # A cache hit is still a routing decision — it lands in its tier bucket
    # AND in cached_calls.
    calls = [
        {"tokens_out": 100, "tier_observed": "T3", "cache_hit": True},
        {"tokens_out": 100, "tier_observed": "T3", "cache_hit": False},
    ]
    r = rollup_routing(calls)
    assert r.cached_calls == 1
    assert r.tier_calls == {"T3": 2}


def test_rollup_unknown_tier_bucketed():
    r = rollup_routing([{"tokens_out": 50, "tier_observed": None}])
    assert r.tier_calls == {"unknown": 1}


def test_as_dict_shape():
    d = rollup_routing([{"tokens_out": 10, "tier_observed": "T2"}]).as_dict()
    assert set(d) == {"call_count", "cached_calls", "tokens_out",
                      "tier_calls", "tier_tokens_out"}


def test_empty_rollup():
    r = rollup_routing([])
    assert r.call_count == 0 and r.tier_calls == {}
