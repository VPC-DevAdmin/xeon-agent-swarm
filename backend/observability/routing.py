"""
Per-run routing rollup: the distribution of the router's tier decisions.

The demonstration story: every agent call goes to the external semantic router
as `auto` (or a pinned tier) and the router classifies its difficulty and picks
the tier — easy work stays on-box at cheap tiers, hard work escalates. This
module aggregates the per-call telemetry (served tier, cache hits, tokens) into
a run-level distribution so that story is visible per run.

Nothing here calls a model or a DB — it's pure arithmetic over telemetry, so it
is trivially testable and safe to wire into the run finalize step.
"""
from __future__ import annotations

from dataclasses import dataclass, field

TIERS = ("T1", "T2", "T3", "T4", "T5")
UNKNOWN = "unknown"   # tier not reported (e.g. header missing and no body model)


def normalize_tier(tier: str | None) -> str:
    """Map router tier spellings (T5 / tier5 / 5) to the canonical Tn key."""
    if not tier:
        return UNKNOWN
    t = str(tier).strip().lower()
    digits = "".join(c for c in t if c.isdigit())
    if digits and digits[0] in "12345":
        return f"T{digits[0]}"
    up = t.upper()
    return up if up in TIERS else UNKNOWN


@dataclass
class RoutingRollup:
    call_count: int = 0
    cached_calls: int = 0                              # served from router cache
    tokens_out: int = 0
    tier_calls: dict = field(default_factory=dict)     # canonical tier -> calls
    tier_tokens_out: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "call_count": self.call_count,
            "cached_calls": self.cached_calls,
            "tokens_out": self.tokens_out,
            "tier_calls": dict(self.tier_calls),
            "tier_tokens_out": dict(self.tier_tokens_out),
        }


def rollup_routing(calls: list[dict]) -> RoutingRollup:
    """Aggregate per-call telemetry into the run's tier distribution.

    Each call dict needs: tokens_out (int), tier_observed (str|None),
    cache_hit (bool, optional). Cache hits count toward cached_calls AND toward
    the tier bucket when the served tier is known (a hit is still a routing
    decision the router made earlier).
    """
    r = RoutingRollup()
    for c in calls:
        tokens = int(c.get("tokens_out") or 0)
        tier = normalize_tier(c.get("tier_observed"))
        r.call_count += 1
        if bool(c.get("cache_hit")):
            r.cached_calls += 1
        r.tokens_out += tokens
        r.tier_calls[tier] = r.tier_calls.get(tier, 0) + 1
        r.tier_tokens_out[tier] = r.tier_tokens_out.get(tier, 0) + tokens
    return r
