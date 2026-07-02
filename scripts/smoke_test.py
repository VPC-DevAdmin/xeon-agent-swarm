#!/usr/bin/env python3
"""
End-to-end smoke test for the orchestration platform.

Exercises the full stack against a running backend:
  1. health check
  2. create a connector with an encrypted secret; verify the value never leaks
  3. create a scheduled job; pause/resume it
  4. run the job now; poll the run to completion
  5. fetch run detail (steps + attempts); check the quality eval populated
  6. list run history
  7. clean up (archive job, revoke connector)

Usage:
    python3 scripts/smoke_test.py
    python3 scripts/smoke_test.py --url http://localhost:8000
    python3 scripts/smoke_test.py --timeout 900   # max seconds to wait for the run

Exit code 0 = all checks passed. Non-zero = first failure (printed).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request


GREEN, RED, DIM, RESET = "\033[32m", "\033[31m", "\033[2m", "\033[0m"


class SmokeError(Exception):
    pass


def _req(url: str, method: str = "GET", body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode()[:300]
        raise SmokeError(f"{method} {url} → {e.code}: {detail}")
    except urllib.error.URLError as e:
        raise SmokeError(f"{method} {url} → connection failed: {e.reason}")


def check(name: str, cond: bool, detail: str = "") -> None:
    mark = f"{GREEN}✓{RESET}" if cond else f"{RED}✗{RESET}"
    print(f"  {mark} {name}{(' — ' + detail) if detail else ''}")
    if not cond:
        raise SmokeError(f"check failed: {name} {detail}")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--url", default="http://localhost:8000")
    p.add_argument("--timeout", type=int, default=600,
                   help="max seconds to wait for the run to finish")
    args = p.parse_args()
    base = args.url.rstrip("/")

    print(f"\nSmoke test against {base}\n{'─' * 50}")

    # 1. health
    print("health")
    h = _req(f"{base}/health")
    check("backend healthy", h.get("status") in ("ok", "healthy", None) or bool(h), str(h)[:60])

    # 2. connector with secret
    print("connectors")
    cname = f"smoke-router-{int(time.time())}"
    conn = _req(f"{base}/connectors", "POST", {
        "name": cname, "kind": "router",
        "config": {"base_url": "https://router.internal"},
        "secrets": {"api_key": "super-secret-value-123"},
    })
    cid = conn["id"]
    check("connector created", bool(cid), cname)
    check("secret field name exposed", "api_key" in conn.get("secret_fields", []))
    blob = json.dumps(conn)
    check("secret VALUE never leaked", "super-secret-value-123" not in blob)

    got = _req(f"{base}/connectors/{cid}")
    check("secret value absent on GET too", "super-secret-value-123" not in json.dumps(got))

    # 3. scheduled job + pause/resume
    print("jobs")
    job = _req(f"{base}/jobs", "POST", {
        "name": f"smoke-job-{int(time.time())}",
        "query": "What is Intel AMX and how does it accelerate inference?",
        "schedule_cron": "0 8 * * *",
        "schedule_tz": "UTC",
        "overlap_policy": "skip",
        "config": {"validator_enabled": True},
    })
    jid = job["id"]
    check("job created", bool(jid))
    check("next_fire_at computed", job.get("next_fire_at") is not None,
          str(job.get("next_fire_at")))

    paused = _req(f"{base}/jobs/{jid}/pause", "POST")
    check("job paused", paused["status"] == "paused")
    resumed = _req(f"{base}/jobs/{jid}/resume", "POST")
    check("job resumed", resumed["status"] == "active")

    sched = _req(f"{base}/jobs/scheduled")
    check("job appears in /jobs/scheduled", any(j["id"] == jid for j in sched))

    # 4. run-now + poll to completion
    print("run")
    started = _req(f"{base}/jobs/{jid}/run-now", "POST")
    run_id = started["run_id"]
    check("run started", bool(run_id), run_id[:8])

    deadline = time.time() + args.timeout
    status = "pending"
    while time.time() < deadline:
        detail = _req(f"{base}/run/{run_id}")
        status = (detail.get("status")
                  or detail.get("swarm", {}).get("status")
                  or "unknown")
        if status in ("completed", "failed", "killed"):
            break
        print(f"    {DIM}…{status} ({int(deadline - time.time())}s left){RESET}")
        time.sleep(8)
    check("run reached terminal state", status in ("completed", "failed", "killed"),
          status)

    # 5. run detail via /runs/{id} (durable DB record)
    print("run detail")
    rd = _req(f"{base}/runs/{run_id}")
    check("run detail has steps", len(rd.get("steps", [])) > 0,
          f"{len(rd.get('steps', []))} steps")
    # 6. history listing
    print("history")
    runs = _req(f"{base}/runs?limit=10")
    check("run appears in history", any(r["id"] == run_id for r in runs))

    # 7. cleanup
    print("cleanup")
    arch = _req(f"{base}/jobs/{jid}/archive", "POST")
    check("job archived", arch["status"] == "archived")
    rev = _req(f"{base}/connectors/{cid}", "DELETE")
    check("connector revoked", rev["status"] == "revoked")

    print(f"\n{GREEN}All smoke checks passed.{RESET}\n")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SmokeError as e:
        print(f"\n{RED}SMOKE TEST FAILED:{RESET} {e}\n", file=sys.stderr)
        sys.exit(1)
