"""The sandboxed ops job (runs in an isolated interpreter; see sandbox.py).

    python -I -S sandbox_ops_job.py <seed>

The shape of the lab study's install-configure-verify tasks (fix a git
repository, set up a service and prove it works), kept deliberately light
on compute: most of the wall time is git and a service round trip, not
arithmetic. Two parts, both verified:

1. Repository repair: a seeded repository with two branches that edited
   the same files; the merge conflicts, the conflicts are resolved (both
   sides kept in order), the merge is committed, and the repository is
   checked (fsck, commit count, no markers left, clean tree).
2. Service: a small HTTP service is configured from a generated config
   file, started on the loopback inside the sandbox's network namespace,
   probed on its health and config routes, and stopped.

Prints one JSON line: commits, conflicts, resolved, checks, failures,
service_ok, cpu_ms, compute_ms.
"""
import json
import os
import random
import resource
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

seed = int(sys.argv[1])
rng = random.Random(seed)
t0 = time.perf_counter()
checks = 0
failures = 0


def check(ok):
    global checks, failures
    checks += 1
    if not ok:
        failures += 1


work = tempfile.mkdtemp(prefix="bench-ops-", dir="/tmp")
repo = os.path.join(work, "repo")
os.makedirs(repo)
GIT = ["git", "-c", "user.name=bench", "-c", "user.email=bench@example.invalid",
       "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false"]


def git(*args, ok=True):
    r = subprocess.run([*GIT, *args], cwd=repo, capture_output=True, text=True)
    if ok and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr[-200:]}")
    return r


try:
    # 1. repository with a conflicting history
    git("init", "-q", "-b", "main")
    files = [f"svc/module_{i:02d}.conf" for i in range(24)]
    os.makedirs(os.path.join(repo, "svc"))
    for f in files:
        open(os.path.join(repo, f), "w").write(
            "\n".join(f"key{k} = {rng.randrange(1000)}" for k in range(20)) + "\n")
    git("add", "-A"); git("commit", "-q", "-m", "base config")
    git("checkout", "-q", "-b", "feature")
    edited_feature = rng.sample(files, 12)
    for f in edited_feature:
        with open(os.path.join(repo, f), "a") as fh:
            fh.write(f"feature_flag = {rng.randrange(2)}\ntimeout_s = {rng.randrange(5, 60)}\n")
    git("add", "-A"); git("commit", "-q", "-m", "feature: flags and timeouts")
    git("checkout", "-q", "main")
    edited_main = rng.sample(edited_feature, 6) + rng.sample([f for f in files if f not in edited_feature], 3)
    for f in edited_main:
        with open(os.path.join(repo, f), "a") as fh:
            fh.write(f"retries = {rng.randrange(1, 9)}\nregion = r{rng.randrange(8)}\n")
    git("add", "-A"); git("commit", "-q", "-m", "main: retries and regions")
    merge = git("merge", "--no-edit", "feature", ok=False)
    conflicted = [l.split("\t", 1)[1] for l in git("diff", "--name-only", "--diff-filter=U").stdout.splitlines()
                  if "\t" in l] or git("diff", "--name-only", "--diff-filter=U").stdout.split()
    check(merge.returncode != 0 and len(conflicted) == 6)
    resolved = 0
    for f in conflicted:
        path = os.path.join(repo, f)
        ours, theirs, common, side = [], [], [], None
        for line in open(path).read().splitlines():
            if line.startswith("<<<<<<<"):
                side = "ours"; continue
            if line.startswith("======="):
                side = "theirs"; continue
            if line.startswith(">>>>>>>"):
                side = None; continue
            (ours if side == "ours" else theirs if side == "theirs" else common).append(line)
        open(path, "w").write("\n".join(common + ours + theirs) + "\n")
        resolved += 1
    git("add", "-A"); git("commit", "-q", "-m", "merge feature into main")
    log = git("log", "--oneline").stdout.strip().splitlines()
    check(len(log) == 4)
    check(git("fsck", "--no-progress", ok=False).returncode == 0)
    markers = sum(1 for f in files if "<<<<<<<" in open(os.path.join(repo, f)).read())
    check(markers == 0)
    check(git("status", "--porcelain").stdout.strip() == "")

    # 2. configure, start and probe a small service on the loopback
    port = 20000 + (seed % 20000)
    config = {"service": f"svc-{seed % 1000}", "port": port,
              "routes": {f"/api/{k}": rng.randrange(100) for k in ("a", "b", "c")},
              "modules": len(files)}
    open(os.path.join(work, "service.json"), "w").write(json.dumps(config))
    cfg = json.load(open(os.path.join(work, "service.json")))

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):  # quiet
            pass

        def do_GET(self):
            if self.path == "/health":
                body = b'{"ok": true}'
            elif self.path == "/config":
                body = json.dumps(cfg).encode()
            elif self.path in cfg["routes"]:
                body = json.dumps({"value": cfg["routes"][self.path]}).encode()
            else:
                self.send_response(404); self.end_headers(); return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    service_ok = False
    try:
        srv = HTTPServer(("127.0.0.1", port), H)
        th = threading.Thread(target=srv.serve_forever, daemon=True); th.start()
        try:
            r = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=5))
            check(r.get("ok") is True)
            r = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/config", timeout=5))
            check(r.get("port") == port and r.get("modules") == 24)
            for route, val in cfg["routes"].items():
                r = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}{route}", timeout=5))
                check(r.get("value") == val)
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/missing", timeout=5)
                check(False)
            except urllib.error.HTTPError as exc:
                check(exc.code == 404)
            service_ok = True
        finally:
            srv.shutdown(); srv.server_close()
    except OSError as exc:
        check(False)
        service_error = str(exc)[:120]
    cpu = resource.getrusage(resource.RUSAGE_SELF)
    kids = resource.getrusage(resource.RUSAGE_CHILDREN)
    out = {"commits": len(log), "conflicts": len(conflicted), "resolved": resolved,
           "checks": checks, "failures": failures, "service_ok": service_ok,
           "cpu_ms": round((cpu.ru_utime + cpu.ru_stime + kids.ru_utime + kids.ru_stime) * 1000, 1),
           "compute_ms": round((time.perf_counter() - t0) * 1000, 1)}
    if not service_ok:
        out["service_error"] = locals().get("service_error", "unknown")
    print(json.dumps(out))
    sys.exit(0 if failures == 0 else 1)
finally:
    shutil.rmtree(work, ignore_errors=True)
