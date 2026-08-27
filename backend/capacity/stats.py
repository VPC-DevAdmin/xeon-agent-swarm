"""
Statistics for the two reported metrics.

Both metrics are decisions about a population from a sample, so both need
interval arithmetic rather than point estimates. Capability asks whether the
success probability of a workflow type is at least 95%, which a raw percentage
cannot answer: two clean completions are 100% and mean nothing. Capacity asks
whether a backlog is growing, which is a question about the sign of a
regression slope, not about its point value.

Everything here is a pure function over plain sequences so the rules can be
tested offline, and so a reader can check the arithmetic without running a
benchmark.
"""
from __future__ import annotations

import math
import random

# One-sided 95% normal quantile. Capability and slope tests are one-sided: we
# care that success is high enough and that a slope is above zero, not that
# either sits inside a symmetric band.
Z95 = 1.6449

# Student t, one-sided 95%, by degrees of freedom. Small windows hold few
# samples, where the normal quantile is optimistic.
_T95 = {1: 6.314, 2: 2.920, 3: 2.353, 4: 2.132, 5: 2.015, 6: 1.943, 7: 1.895,
        8: 1.860, 9: 1.833, 10: 1.812, 12: 1.782, 15: 1.753, 20: 1.725,
        25: 1.708, 30: 1.697, 40: 1.684, 60: 1.671, 120: 1.658}


def t95(df: int) -> float:
    if df <= 0:
        return float("inf")
    for k in sorted(_T95):
        if df <= k:
            return _T95[k]
    return Z95


def wilson_lower(successes: int, n: int, z: float = Z95) -> float:
    """Lower one-sided confidence bound on a success probability (Wilson).

    Wilson rather than the textbook normal interval because the normal
    interval degenerates near p=1, which is exactly where a healthy level
    sits. With zero failures the bound reaches 0.95 at n=52, which is the
    sample size a 95/95 claim actually costs.
    """
    if n <= 0:
        return 0.0
    successes = max(0, min(successes, n))
    p = successes / n
    denom = 1.0 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - margin)


def samples_for_bound(target: float = 0.95, z: float = Z95) -> int:
    """Clean observations needed to certify `target` with zero failures."""
    n = 1
    while n < 100_000 and wilson_lower(n, n, z) < target:
        n += 1
    return n


def ols_slope(xs: list[float], ys: list[float]) -> tuple[float, float] | None:
    """(slope, standard error of slope) by ordinary least squares."""
    n = len(xs)
    if n < 3 or n != len(ys):
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 0:
        return None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    slope = sxy / sxx
    intercept = my - slope * mx
    sse = sum((y - (intercept + slope * x)) ** 2 for x, y in zip(xs, ys))
    var = sse / (n - 2)
    return slope, math.sqrt(var / sxx) if var > 0 else 0.0


def slope_lower_bound(xs: list[float], ys: list[float]) -> float | None:
    """Lower one-sided 95% bound on the regression slope of ys against xs.

    A backlog is declared growing only when this bound is above zero, so
    ordinary scatter in a stable queue cannot manufacture divergence.
    """
    fit = ols_slope(xs, ys)
    if fit is None:
        return None
    slope, se = fit
    return slope - t95(len(xs) - 2) * se


def segmented_breakpoint(rates: list[float], throughput: list[float]
                         ) -> tuple[float, float] | None:
    """Fit 'throughput follows offered rate, then flattens' and return the
    breakpoint with its residual sum of squares.

    The model is deliberately the shape the system is expected to have: a
    proportional segment while the host keeps up, then a plateau once it does
    not. A grid search over candidate breakpoints is enough at the handful of
    rate levels a run produces, and it cannot invent a knee where the two
    segments fit no better than one line.
    """
    pts = sorted(zip(rates, throughput))
    if len(pts) < 4:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    best = None
    for i in range(2, len(pts) - 1):          # need >=2 points per segment
        b = xs[i]
        lo_x, lo_y = xs[:i + 1], ys[:i + 1]
        hi_y = ys[i:]
        fit = ols_slope(lo_x, lo_y)
        if fit is None:
            continue
        slope, _se = fit
        mx = sum(lo_x) / len(lo_x)
        my = sum(lo_y) / len(lo_y)
        icept = my - slope * mx
        sse = sum((y - (icept + slope * x)) ** 2 for x, y in zip(lo_x, lo_y))
        plateau = sum(hi_y) / len(hi_y)
        sse += sum((y - plateau) ** 2 for y in hi_y)
        if best is None or sse < best[1]:
            best = (b, sse)
    if best is None:
        return None
    # Reject a "knee" that fits no better than one straight line: a system
    # that never saturated must not be handed a boundary.
    single = ols_slope(xs, ys)
    if single is not None:
        slope, _se = single
        mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
        icept = my - slope * mx
        sse_single = sum((y - (icept + slope * x)) ** 2 for x, y in zip(xs, ys))
        if best[1] >= 0.9 * sse_single:
            return None
    return best


def bootstrap_breakpoint_ci(rates: list[float], throughput: list[float],
                            iterations: int = 400, seed: int = 0
                            ) -> tuple[float, float, float] | None:
    """(estimate, lower 95%, upper 95%) for the saturation breakpoint.

    Resampling is over observed (rate, throughput) pairs. The reported
    capacity uses the lower bound, so the published number understates rather
    than overstates what the host sustained.
    """
    base = segmented_breakpoint(rates, throughput)
    if base is None:
        return None
    rng = random.Random(seed)
    pts = list(zip(rates, throughput))
    draws: list[float] = []
    for _ in range(iterations):
        sample = [pts[rng.randrange(len(pts))] for _ in pts]
        fit = segmented_breakpoint([p[0] for p in sample], [p[1] for p in sample])
        if fit is not None:
            draws.append(fit[0])
    if len(draws) < iterations // 4:
        return None
    draws.sort()
    lo = draws[max(0, int(0.05 * len(draws)) - 1)]
    hi = draws[min(len(draws) - 1, int(0.95 * len(draws)))]
    return base[0], lo, hi
