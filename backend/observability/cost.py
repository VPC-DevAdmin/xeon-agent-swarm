"""
Tier cost model and decomposed-vs-monolithic savings rollup.

The cost story (tier-router migration §5): each call is priced at the tier the
router actually served (tier_observed), and the whole run is compared against a
baseline where every call ran at the top tier (T5, the "just send it all to the
big model" monolith). The gap is the savings the tier routing buys.

The price table is CONFIG and labeled ILLUSTRATIVE until measured against real
gateway billing. Override per-tier prices via env: TIER_COST_T1..TIER_COST_T5
(USD per 1K completion tokens).

Nothing here calls a model or a DB — it's pure arithmetic over telemetry, so it
is trivially testable and safe to wire into the run finalize step.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

# Illustrative USD per 1K completion tokens by tier. Ordered cheap → expensive.
# These are placeholders — replace with measured gateway prices before quoting
# a real savings number to anyone.
_DEFAULT_TIER_COST: dict[str, float] = {
    "T1": 0.0001,
    "T2": 0.0005,
    "T3": 0.002,
    "T4": 0.008,
    "T5": 0.030,
}

BASELINE_TIER = "T5"   # the monolithic "everything at the top tier" comparison


def _normalize_tier(tier: str | None) -> str:
    """Map router tier spellings (T5 / tier5 / 5) to the canonical Tn key."""
    if not tier:
        return BASELINE_TIER
    t = str(tier).strip().lower()
    digits = "".join(c for c in t if c.isdigit())
    if digits and digits[0] in "12345":
        return f"T{digits[0]}"
    up = t.upper()
    return up if up in _DEFAULT_TIER_COST else BASELINE_TIER


def tier_cost_table() -> dict[str, float]:
    """The active price table, with env overrides applied."""
    table = dict(_DEFAULT_TIER_COST)
    for tier in table:
        override = os.getenv(f"TIER_COST_{tier}")
        if override:
            try:
                table[tier] = float(override)
            except ValueError:
                pass
    return table


def call_cost(tokens_out: int, tier: str | None, *, cache_hit: bool = False) -> float:
    """Cost of one call: completion tokens × the served tier's per-1K price.

    A cache hit costs nothing (the router served a cached result, no model spend).
    """
    if cache_hit or not tokens_out:
        return 0.0
    price = tier_cost_table()[_normalize_tier(tier)]
    return round((tokens_out / 1000.0) * price, 6)


@dataclass
class RunCost:
    total_cost: float        # priced at the tiers actually served
    baseline_cost: float     # the same calls priced entirely at T5
    savings_pct: float       # 100 * (1 - total/baseline), 0 when baseline is 0
    call_count: int
    cached_calls: int

    def as_dict(self) -> dict:
        return {
            "total_cost": round(self.total_cost, 6),
            "baseline_cost": round(self.baseline_cost, 6),
            "savings_pct": round(self.savings_pct, 2),
            "call_count": self.call_count,
            "cached_calls": self.cached_calls,
        }


def rollup_run(calls: list[dict]) -> RunCost:
    """Aggregate per-call telemetry into a run-level cost + savings figure.

    Each call dict needs: tokens_out (int), tier_observed (str|None),
    cache_hit (bool, optional). Missing/None tier is treated as the baseline tier.
    """
    table = tier_cost_table()
    baseline_price = table[BASELINE_TIER]
    total = 0.0
    baseline = 0.0
    cached = 0
    for c in calls:
        tokens = int(c.get("tokens_out") or 0)
        hit = bool(c.get("cache_hit"))
        if hit:
            cached += 1
        total += call_cost(tokens, c.get("tier_observed"), cache_hit=hit)
        # Baseline always pays the top tier for every call's tokens (even cached —
        # the monolith has no cache tier story), so it's the honest "what it would
        # have cost without routing" comparison.
        baseline += round((tokens / 1000.0) * baseline_price, 6)

    savings = 0.0 if baseline <= 0 else 100.0 * (1.0 - total / baseline)
    return RunCost(
        total_cost=total,
        baseline_cost=baseline,
        savings_pct=max(0.0, savings),
        call_count=len(calls),
        cached_calls=cached,
    )
