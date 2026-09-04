"""The sandboxed build-and-test job (runs in an isolated interpreter; see
sandbox.py).

    python -I -S sandbox_build_job.py <seed> <src-root>

The shape of a code agent's step: build the working tree and run its
test suite. The tree is a real, recognizable project vendored into the
repository: Lua 5.4.7 (MIT; lua.org) built with gcc -O2 through its own
Makefile and tested with its own suite (all.lua in its portable mode),
and, when the SQLite amalgamation is present beside it (public domain;
sqlite.org), the database engine compiled from sqlite3.c with its shell
and exercised by an integration script (schema, 300k inserted rows, an
index, aggregates, integrity check). Every step is the project's own
tooling; nothing is generated. Deterministic for a seed (the seed only
names the working copy). Prints one JSON line: project, sources, lines,
build_ms, test_ms, suites, failures, cpu_ms, compute_ms.
"""
import glob
import json
import os
import resource
import shutil
import subprocess
import sys
import tempfile
import time

seed, src_root = int(sys.argv[1]), sys.argv[2]
t0 = time.perf_counter()
work = tempfile.mkdtemp(prefix=f"bench-build-{seed % 1000}-", dir="/tmp")
env = {**os.environ, "CC": "gcc", "MAKEFLAGS": "", "LC_ALL": "C"}
failures = 0
suites = 0
build_ms = 0.0
test_ms = 0.0


def run(cmd, cwd, timeout=900):
    return subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)


try:
    lua = os.path.join(work, "lua-5.4.7")
    tests = os.path.join(work, "lua-5.4.7-tests")
    shutil.copytree(os.path.join(src_root, "lua-5.4.7"), lua)
    shutil.copytree(os.path.join(src_root, "lua-5.4.7-tests"), tests)
    sources = glob.glob(os.path.join(lua, "src", "*.c")) + glob.glob(os.path.join(lua, "src", "*.h"))
    lines = sum(sum(1 for _ in open(f, errors="replace")) for f in sources)
    target = "macosx" if sys.platform == "darwin" else "generic"
    t1 = time.perf_counter()
    r = run(["make", "-s", target], lua)
    build_ms += (time.perf_counter() - t1) * 1000
    if r.returncode != 0:
        print(json.dumps({"error": "lua build failed: " + r.stderr[-400:]}))
        sys.exit(2)
    t2 = time.perf_counter()
    r = run([os.path.join(lua, "src", "lua"), "-e", "_U=true", "all.lua"], tests)
    test_ms += (time.perf_counter() - t2) * 1000
    suites += 1
    if r.returncode != 0 or "final OK" not in r.stdout:
        failures += 1
    project = "lua-5.4.7"

    sqlite_src = os.path.join(src_root, "sqlite")
    if os.path.isdir(sqlite_src):
        sq = os.path.join(work, "sqlite")
        shutil.copytree(sqlite_src, sq)
        sources += [os.path.join(sq, "sqlite3.c"), os.path.join(sq, "shell.c")]
        lines += sum(sum(1 for _ in open(f, errors="replace")) for f in sources[-2:])
        t3 = time.perf_counter()
        r = run(["gcc", "-O2", "-DSQLITE_THREADSAFE=0", "-DSQLITE_OMIT_LOAD_EXTENSION",
                 "sqlite3.c", "shell.c", "-o", "sqlite3", "-lm"], sq)
        build_ms += (time.perf_counter() - t3) * 1000
        if r.returncode != 0:
            print(json.dumps({"error": "sqlite build failed: " + r.stderr[-400:]}))
            sys.exit(2)
        sql = """
CREATE TABLE events(id INTEGER PRIMARY KEY, merchant INTEGER, value REAL, ts INTEGER);
WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i+1 FROM n WHERE i<300000)
INSERT INTO events(merchant, value, ts) SELECT (i*7919)%4096, ((i*104729)%100000)/100.0, (i*31)%86400 FROM n;
CREATE INDEX ev_m ON events(merchant);
SELECT merchant, COUNT(*), ROUND(SUM(value),2) FROM events GROUP BY merchant ORDER BY 3 DESC LIMIT 5;
SELECT COUNT(*) FROM events WHERE value > 990;
SELECT ts/3600, COUNT(*) FROM events GROUP BY 1 ORDER BY 2 DESC LIMIT 1;
PRAGMA integrity_check;
"""
        t4 = time.perf_counter()
        r = run([os.path.join(sq, "sqlite3"), os.path.join(work, "smoke.db")], sq)
        r = subprocess.run([os.path.join(sq, "sqlite3"), os.path.join(work, "smoke.db")], input=sql,
                           cwd=sq, env=env, capture_output=True, text=True, timeout=900)
        test_ms += (time.perf_counter() - t4) * 1000
        suites += 1
        if r.returncode != 0 or "ok" not in r.stdout.strip().splitlines()[-1:]:
            failures += 1
        project += "+sqlite"
    cpu = resource.getrusage(resource.RUSAGE_SELF)
    kids = resource.getrusage(resource.RUSAGE_CHILDREN)
    print(json.dumps({
        "project": project, "sources": len(sources), "lines": lines,
        "build_ms": round(build_ms, 1), "test_ms": round(test_ms, 1),
        "suites": suites, "failures": failures,
        "cpu_ms": round((cpu.ru_utime + cpu.ru_stime + kids.ru_utime + kids.ru_stime) * 1000, 1),
        "compute_ms": round((time.perf_counter() - t0) * 1000, 1),
    }))
    sys.exit(0 if failures == 0 else 1)
finally:
    shutil.rmtree(work, ignore_errors=True)
