"""
Evidence ledger: the raw record a run leaves behind so judgment can be
RE-RUN without re-running the test.

The in-run judge steers the ramp (which levels get visited), but its
verdicts are provisional. Every unit, level transition, and telemetry
sample streams to a gzipped JSONL ledger during the run, and the offline
judge (backend.capacity.judge) recomputes verdicts from the ledger alone.
When judgment rules evolve, historical ledgers are re-judged under the
new rules and the data never has to be bought again.

Row kinds, one JSON object per line:
  header  run identity: seed, mode, rung, deadline_s, tiers, config slice
  unit    a finished workflow: sid, ok, lat (ms), sub/end (epoch), err
  level   a level record with ts: phase, users, live slo_state, stats
  sample  a telemetry sample verbatim
  window  a collapse window [start, end]
  footer  harness counters, live verdict/breach, ended_at
"""
from __future__ import annotations

import gzip
import hashlib
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class EvidenceWriter:
    """Streaming, best-effort ledger. A ledger failure never fails a run:
    writes degrade to a logged warning and the run keeps its in-memory
    aggregates, exactly as before ledgers existed."""

    def __init__(self, path: Path):
        self.path = path
        self.rows = 0
        self._fh = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._fh = gzip.open(path, "wt", compresslevel=6)
        except OSError as exc:
            logger.warning("evidence ledger unavailable (%s) — run continues "
                           "without post-hoc judgability", exc)

    def write(self, kind: str, payload: dict) -> None:
        if self._fh is None:
            return
        try:
            self._fh.write(json.dumps({"k": kind, **payload},
                                      separators=(",", ":"),
                                      default=str) + "\n")
            self.rows += 1
        except OSError as exc:
            logger.warning("evidence ledger write failed (%s) — closing it",
                           exc)
            self.close()

    def unit(self, rec: dict) -> None:
        self.write("unit", {
            "sid": rec.get("scenario"),
            "ok": bool(rec.get("ok")),
            "lat": rec.get("latency_ms"),
            "sub": rec.get("t_submit"),
            "end": rec.get("ts"),
            **({"err": str(rec.get("error"))[:120]} if rec.get("error")
               else {}),
        })

    def close(self) -> dict | None:
        """Flush and return {path, sha256, rows} for the result payload."""
        if self._fh is None:
            return None
        try:
            self._fh.close()
        except OSError:
            pass
        self._fh = None
        try:
            digest = hashlib.sha256(self.path.read_bytes()).hexdigest()
        except OSError:
            return None
        return {"path": str(self.path), "sha256": digest, "rows": self.rows}


def read_evidence(path: str | Path) -> dict:
    """Load a ledger into {header, units, levels, samples, windows, footer}."""
    out: dict = {"header": None, "units": [], "levels": [], "samples": [],
                 "windows": [], "footer": None}
    with gzip.open(path, "rt") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            kind = row.pop("k", None)
            if kind == "unit":
                out["units"].append(row)
            elif kind == "level":
                out["levels"].append(row)
            elif kind == "sample":
                out["samples"].append(row)
            elif kind == "window":
                out["windows"].append(row)
            elif kind == "header":
                out["header"] = row
            elif kind == "footer":
                out["footer"] = row
    return out
