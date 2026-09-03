"""The sandboxed data job (runs in an isolated interpreter; see sandbox.py).

    python -I -S sandbox_job.py <light|heavy> <seed> <site-packages> <rows>

The shape of an analyst agent's tool run over one day of payment events:
generate a seeded event table (merchant, value, timestamp) and a merchant
table (category, region); join them; bucket by merchant and by minute;
sort-based per-merchant value percentiles; a rolling load window; tail
quantiles; z-scored anomaly ranking with a per-category baseline; and a
second pass over the flagged merchants. Deterministic for a seed,
single-threaded, O(n log n) in rows. Prints one JSON line.
"""
import json
import resource
import sys
import time

size, seed, site = sys.argv[1], int(sys.argv[2]), sys.argv[3]
ROWS = int(sys.argv[4]) if len(sys.argv) > 4 else {"light": 450_000, "heavy": 3_300_000}[size]
if site:
    sys.path.append(site)
import numpy as np  # noqa: E402

MERCHANTS = 4096
CATEGORIES = 32
t0 = time.perf_counter()
rng = np.random.default_rng(seed)
merchant = rng.integers(0, MERCHANTS, ROWS, dtype=np.int64)
value = rng.gamma(2.0, 50.0, ROWS)
ts = rng.integers(0, 86_400, ROWS, dtype=np.int64)
m_cat = rng.integers(0, CATEGORIES, MERCHANTS, dtype=np.int64)
m_region = rng.integers(0, 8, MERCHANTS, dtype=np.int64)

# join: category and region per event
cat = np.take(m_cat, merchant)
region = np.take(m_region, merchant)
# aggregates by merchant and by category
counts = np.bincount(merchant, minlength=MERCHANTS)
sums = np.bincount(merchant, weights=value, minlength=MERCHANTS)
means = np.divide(sums, counts, out=np.zeros(MERCHANTS), where=counts > 0)
cat_sum = np.bincount(cat, weights=value, minlength=CATEGORIES)
cat_cnt = np.bincount(cat, minlength=CATEGORIES)
cat_mean = np.divide(cat_sum, cat_cnt, out=np.zeros(CATEGORIES), where=cat_cnt > 0)
# time series: per-minute load, rolling 15-minute window, peak
minute = ts // 60
per_min = np.bincount(minute, weights=value, minlength=1440)
rolling = np.convolve(per_min, np.ones(15) / 15.0, mode="same")
peak_min = int(np.argmax(rolling))
# sort-based per-merchant percentiles (the expensive, realistic pass)
order = np.lexsort((value, merchant))
sm, sv = merchant[order], value[order]
starts = np.searchsorted(sm, np.arange(MERCHANTS), side="left")
ends = np.searchsorted(sm, np.arange(MERCHANTS), side="right")
idx95 = np.minimum(ends - 1, starts + ((ends - starts) * 0.95).astype(np.int64))
p95_by_m = np.where(ends > starts, sv[np.minimum(idx95, len(sv) - 1)], 0.0)
# tail quantiles and a full sort for the ECDF
q50, q95, q99 = np.quantile(value, [0.5, 0.95, 0.99])
sorted_all = np.sort(value)
ecdf_at_100 = float(np.searchsorted(sorted_all, 100.0) / len(sorted_all))
# anomalies: merchant mean vs its category's baseline, z-scored
base = np.take(cat_mean, m_cat)
dev = means - base
z = (dev - dev[counts > 0].mean()) / (dev[counts > 0].std() or 1.0)
flagged = np.where((np.abs(z) > 3.0) & (counts > 0))[0]
# second pass over the flagged merchants' own events
mask = np.isin(merchant, flagged)
flagged_events = int(mask.sum())
flagged_value = float(value[mask].sum())
hourly_flagged = np.bincount(ts[mask] // 3600, minlength=24)
top = np.argsort(-sums)[:5]
cpu = resource.getrusage(resource.RUSAGE_SELF)
print(json.dumps({
    "rows": int(ROWS), "merchants": int(MERCHANTS),
    "top_keys": [[int(k), round(float(sums[k]), 1)] for k in top],
    "q50": round(float(q50), 2), "q95": round(float(q95), 2), "q99": round(float(q99), 2),
    "ecdf_100": round(ecdf_at_100, 4),
    "hourly_peak_hour": peak_min // 60, "peak_rolling_load": round(float(rolling[peak_min]), 1),
    "p95_by_merchant_max": round(float(p95_by_m.max()), 2),
    "outliers": int(len(flagged)), "flagged_events": flagged_events,
    "flagged_value": round(flagged_value, 1),
    "flagged_peak_hour": int(np.argmax(hourly_flagged)),
    "hi_share_max": round(float(np.max(np.divide(np.bincount(merchant[value > q95], minlength=MERCHANTS), counts, out=np.zeros(MERCHANTS), where=counts > 0))), 4),
    "mean_of_means": round(float(means[counts > 0].mean()), 3),
    "cpu_ms": round((cpu.ru_utime + cpu.ru_stime) * 1000, 1),
    "compute_ms": round((time.perf_counter() - t0) * 1000, 1),
}))
