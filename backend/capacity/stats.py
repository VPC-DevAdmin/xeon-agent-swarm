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
from statistics import NormalDist

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


def familywise_z(comparisons: int, confidence: float = 0.95) -> float:
    """Bonferroni-adjusted one-sided normal quantile.

    Capability is one joint claim over every workflow type.  Testing each
    type at 95% would make the family confidence materially lower than 95%, so
    the per-type interval spends only alpha / number_of_types.
    """
    comparisons = max(1, int(comparisons))
    alpha = max(1e-9, min(0.5, 1.0 - float(confidence)))
    return NormalDist().inv_cdf(1.0 - alpha / comparisons)


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


def queue_growth_lower_bound(xs: list[float], ys: list[float], *,
                             iterations: int = 1000, seed: int = 0,
                             block_size: int | None = None) -> float | None:
    """One-sided 95% lower bound for backlog growth, in units/second.

    Queue *levels* are an integrated, strongly autocorrelated process.  OLS on
    the levels treats adjacent samples as independent and produces confidence
    intervals that are far too narrow.  We instead analyze non-overlapping
    queue increments and moving-block-bootstrap their mean rate.  Blocks keep
    short-range dependence intact while the first difference removes the
    integrated level shared by adjacent observations.
    """
    if len(xs) != len(ys) or len(xs) < 6:
        return None
    increments: list[float] = []
    for x0, x1, y0, y1 in zip(xs, xs[1:], ys, ys[1:]):
        dt = x1 - x0
        if dt > 0:
            increments.append((y1 - y0) / dt)
    n = len(increments)
    if n < 5:
        return None
    block = block_size or max(2, int(round(math.sqrt(n))))
    block = min(block, n)
    blocks = [[increments[(start + j) % n] for j in range(block)]
              for start in range(n)]
    rng = random.Random(seed)
    draws: list[float] = []
    for _ in range(max(200, int(iterations))):
        sample: list[float] = []
        while len(sample) < n:
            sample.extend(blocks[rng.randrange(len(blocks))])
        draws.append(sum(sample[:n]) / n)
    draws.sort()
    return draws[max(0, int(0.05 * len(draws)) - 1)]


def slope_lower_bound(xs: list[float], ys: list[float]) -> float | None:
    """Compatibility name for the autocorrelation-safe queue-growth test."""
    return queue_growth_lower_bound(xs, ys)


def _capacity_fit(rates: list[float], throughput: list[float]
                  ) -> tuple[float, float, float, list[float]] | None:
    """Fit X(lambda)=k*min(lambda, breakpoint), continuous at the knee."""
    pts = sorted((float(x), float(y)) for x, y in zip(rates, throughput)
                 if x > 0 and y >= 0)
    if len(pts) < 4 or len({x for x, _ in pts}) < 4:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    lo, hi = xs[1], xs[-2]             # at least two observations per side
    if hi <= lo:
        return None
    candidates = [lo + (hi - lo) * i / 200 for i in range(201)]
    best: tuple[float, float, float, list[float]] | None = None
    for breakpoint in candidates:
        basis = [min(x, breakpoint) for x in xs]
        denom = sum(v * v for v in basis)
        if denom <= 0:
            continue
        k = max(0.0, sum(v * y for v, y in zip(basis, ys)) / denom)
        predicted = [k * v for v in basis]
        sse = sum((y - p) ** 2 for y, p in zip(ys, predicted))
        if best is None or sse < best[1]:
            best = (breakpoint, sse, k, predicted)
    if best is None:
        return None

    # Refuse a knee unless the saturation model materially improves on a
    # single proportional-throughput line through the origin.
    denom = sum(x * x for x in xs)
    k_linear = sum(x * y for x, y in zip(xs, ys)) / denom if denom else 0.0
    sse_linear = sum((y - k_linear * x) ** 2 for x, y in zip(xs, ys))
    if sse_linear <= 0 or best[1] >= 0.9 * sse_linear:
        return None
    return best


def segmented_breakpoint(rates: list[float], throughput: list[float]
                         ) -> tuple[float, float] | None:
    """Return the continuous proportional-then-plateau knee and its SSE."""
    fit = _capacity_fit(rates, throughput)
    return (fit[0], fit[1]) if fit is not None else None


def bootstrap_breakpoint_ci(rates: list[float], throughput: list[float],
                            iterations: int = 400, seed: int = 0
                            ) -> dict[str, float | list[float]] | None:
    """Fixed-design residual bootstrap for the saturation breakpoint.

    Offered/achieved rates are experimental design points, not random draws;
    they must never be duplicated or omitted by a pairs bootstrap.  Residuals
    are resampled around the fitted continuous curve while rates remain fixed.
    The one-sided lower bound used for publication is distinct from the
    central two-sided 95% interval.
    """
    pts = sorted((float(x), float(y)) for x, y in zip(rates, throughput))
    design_rates = [x for x, _y in pts]
    observed = [y for _x, y in pts]
    base = _capacity_fit(design_rates, observed)
    if base is None:
        return None
    estimate, _sse, _k, predicted = base
    rng = random.Random(seed)
    residuals = [y - p for y, p in zip(observed, predicted)]
    centre = sum(residuals) / len(residuals)
    residuals = [r - centre for r in residuals]
    draws: list[float] = []
    for _ in range(max(200, int(iterations))):
        sample_y = [max(0.0, p + residuals[rng.randrange(len(residuals))])
                    for p in predicted]
        fit = _capacity_fit(design_rates, sample_y)
        if fit is not None:
            draws.append(fit[0])
    if len(draws) < max(50, iterations // 4):
        return None
    draws.sort()
    def q(p: float) -> float:
        return draws[min(len(draws) - 1, max(0, int(p * (len(draws) - 1))))]
    return {"estimate": estimate,
            "lower_bound_95": q(0.05),
            "ci95": [q(0.025), q(0.975)]}
