"""
Persisted machine characterization: the weigh-in belongs to the MACHINE.

A weigh-in measures how fast this host runs the reference workload, which is
a property of the machine and its configuration — not of any single run. Yet
every run re-measured it, costing ~20 minutes per set and, worse, re-rolling
a 4-sample draw whose noise can move a borderline machine between tiers.

The profile fixes both. A machine's observations accumulate under a
fingerprint of everything that changes its speed, the tier is placed from the
POOLED medians (so confidence grows with every run instead of resetting), and
a run whose fingerprint matches a fresh profile reuses it and records that it
did. Change the workload, the model, the engine geometry, or the hardware and
the fingerprint changes with it, so a stale characterization can never be
silently applied to a machine that is no longer the same machine.
"""
from __future__ import annotations

import json
import logging
import os
import platform
import statistics
import time

from pathlib import Path

logger = logging.getLogger(__name__)

PROFILE_PATH = Path("data/capacity/machine_profiles.json")
DEFAULT_TTL_DAYS = 14.0
MAX_OBSERVATIONS = 12


def fingerprint(*, benchmark_target: str, inference_backend: str,
                benchmark_version: int | None, model: str | None,
                engine: dict | None, host: dict | None) -> str:
    """Everything that changes how fast this machine runs the workload.

    Deliberately EXCLUDES the orchestrator commit: control-plane code churns
    constantly and rarely moves workflow latency, while the workload version
    bumps whenever the WORK changes. The commit that produced each
    observation is still recorded, so drift stays auditable.
    """
    eng = engine or {}
    h = host or {}
    parts = [
        f"target={benchmark_target}", f"backend={inference_backend}",
        f"workload=v{benchmark_version}", f"model={model or '-'}",
        f"ctx={eng.get('context_length', '-')}",
        f"maxtot={eng.get('max_total_tokens', '-')}",
        f"quant={eng.get('quantization', '-')}",
        f"cpu={h.get('cpu_model', platform.processor() or '-')}",
        f"cores={h.get('cpu_count', os.cpu_count())}",
        f"mem={h.get('mem_total_gb', '-')}",
        f"workers={h.get('orchestrator_workers', '-')}",
    ]
    return "|".join(str(p) for p in parts)


def _load() -> dict:
    try:
        return json.loads(PROFILE_PATH.read_text())
    except (OSError, ValueError):
        return {}


def _save(data: dict) -> None:
    try:
        PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
        PROFILE_PATH.write_text(json.dumps(data, indent=1))
    except OSError:
        logger.debug("could not persist machine profile", exc_info=True)


def lookup(fp: str, *, ttl_days: float = DEFAULT_TTL_DAYS) -> dict | None:
    """A fresh profile for this fingerprint, or None."""
    entry = _load().get(fp)
    if not entry or not entry.get("observations"):
        return None
    age_days = (time.time() - float(entry.get("updated_at", 0))) / 86400.0
    if age_days > ttl_days:
        return None
    entry["age_days"] = round(age_days, 2)
    return entry


def record(fp: str, medians_s: dict, *, commit: str | None,
           tiers: list[dict]) -> dict:
    """Add an observation and re-place the machine from the POOLED data.

    Pooling is the point: a single 4-sample draw put this project's anchor
    system on both sides of a tier boundary on different nights. The median
    of every observation is a steadier statement about the machine than any
    one of them, and it only gets steadier.
    """
    data = _load()
    entry = data.get(fp) or {"observations": []}
    entry["observations"].append({
        "at": time.time(), "commit": commit,
        "medians_s": medians_s,
        "worst_median_s": max(medians_s.values()) if medians_s else None,
    })
    entry["observations"] = entry["observations"][-MAX_OBSERVATIONS:]
    worsts = [o["worst_median_s"] for o in entry["observations"]
              if o.get("worst_median_s") is not None]
    pooled = statistics.median(worsts) if worsts else None
    tier = place(pooled, tiers) if pooled is not None else None
    entry.update({
        "updated_at": time.time(),
        "pooled_worst_median_s": round(pooled, 1) if pooled is not None else None,
        "observation_count": len(entry["observations"]),
        "observed_range_s": [round(min(worsts), 1), round(max(worsts), 1)]
                            if worsts else None,
        "tier": tier["name"] if tier else None,
        "deadline_s": tier["deadline_s"] if tier else None,
    })
    data[fp] = entry
    _save(data)
    return entry


def place(worst_median_s: float, tiers: list[dict]) -> dict | None:
    """First tier whose ceiling covers this median. The last tier has none."""
    return next((t for t in tiers
                 if t.get("max_median_s") is None
                 or worst_median_s <= t["max_median_s"]), None)
