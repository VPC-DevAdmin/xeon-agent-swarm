"""The sandboxed build-and-test job (runs in an isolated interpreter; see
sandbox.py).

    python -I -S sandbox_build_job.py <seed> <work>

The shape of a code agent's step: the working tree holds a small C
library, the agent builds it and runs its test suite. Here the library is
generated from the seed (six translation units of invertible 64-bit
mixing functions, each with its inverse derived at generation time from
the modular inverse of its multiplier), compiled with gcc -O2, and tested
by a harness that checks, for every function, that inverse(f(x)) == x over
`work` seeded inputs and that a fixed-buffer digest matches the value
computed independently in Python. Deterministic for a seed. Prints one
JSON line: files, functions, lines, compile_ms, test_ms, tests, failures,
cpu_ms, compute_ms.
"""
import json
import os
import random
import resource
import shutil
import subprocess
import sys
import tempfile
import time

seed = int(sys.argv[1])
WORK = int(sys.argv[2]) if len(sys.argv) > 2 else 1_500_000
FILES, PER_FILE = 6, 60
MASK = (1 << 64) - 1
t0 = time.perf_counter()
rng = random.Random(seed)


def mix_params():
    a = rng.randrange(13, 33)
    c = rng.getrandbits(64) | 1                  # odd: invertible mod 2^64
    b = rng.randrange(13, 33)
    k = rng.getrandbits(64)
    return a, c, b, k


def unshift(x, s):
    """Invert x ^= x >> s (64-bit)."""
    y = x
    for _ in range(64 // s + 1):
        y = x ^ (y >> s)
    return y & MASK


def mix_py(x, p):
    a, c, b, k = p
    x = (x ^ (x >> a)) & MASK
    x = (x * c) & MASK
    x = (x ^ (x >> b)) & MASK
    return (x + k) & MASK


funcs = []
for fi in range(FILES):
    for j in range(PER_FILE):
        funcs.append(mix_params())

work = tempfile.mkdtemp(prefix="bench-build-", dir="/tmp")
try:
    lines = 0
    # header
    hdr = ["#include <stdint.h>", "#include <stddef.h>"]
    for i in range(len(funcs)):
        hdr.append(f"uint64_t f{i}(uint64_t x);")
        hdr.append(f"uint64_t g{i}(uint64_t x);")
    hdr.append("uint64_t digest(const unsigned char *buf, size_t n, int fn);")
    open(os.path.join(work, "lib.h"), "w").write("\n".join(hdr) + "\n")
    lines += len(hdr)
    # translation units: f_i (mix) and g_i (its inverse)
    for fi in range(FILES):
        src = ['#include "lib.h"', ""]
        for j in range(PER_FILE):
            i = fi * PER_FILE + j
            a, c, b, k = funcs[i]
            cinv = pow(c, -1, 1 << 64)
            src += [f"uint64_t f{i}(uint64_t x) {{",
                    f"    x ^= x >> {a};",
                    f"    x *= {c}ULL;",
                    f"    x ^= x >> {b};",
                    f"    return x + {k}ULL;",
                    "}",
                    f"uint64_t g{i}(uint64_t x) {{",
                    f"    uint64_t y;",
                    f"    x -= {k}ULL;",
                    f"    y = x; for (int r = 0; r < {64 // b + 1}; r++) y = x ^ (y >> {b}); x = y;",
                    f"    x *= {cinv}ULL;",
                    f"    y = x; for (int r = 0; r < {64 // a + 1}; r++) y = x ^ (y >> {a}); x = y;",
                    "    return x;",
                    "}", ""]
        open(os.path.join(work, f"unit{fi}.c"), "w").write("\n".join(src))
        lines += len(src)
    # digest over a buffer through a chain of functions (independent Python reference)
    dig = ['#include "lib.h"',
           "uint64_t digest(const unsigned char *buf, size_t n, int fn) {",
           "    uint64_t h = 0x9E3779B97F4A7C15ULL;",
           "    for (size_t i = 0; i < n; i++) {",
           "        h ^= buf[i];",
           "        switch (fn % 8) {"]
    for r in range(8):
        dig.append(f"            case {r}: h = f{r * 7}(h); break;")
    dig += ["        }", "        fn++;", "    }", "    return h;", "}"]
    open(os.path.join(work, "digest.c"), "w").write("\n".join(dig) + "\n")
    lines += len(dig)
    buf = bytes(rng.getrandbits(8) for _ in range(4096))
    h = 0x9E3779B97F4A7C15
    fn = seed % 8
    for byte in buf:
        h ^= byte
        h = mix_py(h, funcs[(fn % 8) * 7])
        fn += 1
    expected_digest = h
    # test harness: round-trip property over WORK inputs per function, then the digest
    harness = ['#include "lib.h"', "#include <stdio.h>", "#include <stdlib.h>",
               "typedef uint64_t (*fn_t)(uint64_t);",
               "static fn_t F[] = {" + ", ".join(f"f{i}" for i in range(len(funcs))) + "};",
               "static fn_t G[] = {" + ", ".join(f"g{i}" for i in range(len(funcs))) + "};",
               "int main(void) {",
               f"    const uint64_t n = {WORK}ULL; long tests = 0, failures = 0;",
               f"    uint64_t x = {rng.getrandbits(64)}ULL;",
               f"    for (int i = 0; i < {len(funcs)}; i++) {{",
               "        uint64_t s = x + (uint64_t)i * 0x9E3779B97F4A7C15ULL;",
               "        for (uint64_t t = 0; t < n; t++) { s += 0x2545F4914F6CDD1DULL;",
               "            if (G[i](F[i](s)) != s) failures++; }",
               "        tests++;",
               "    }",
               "    static unsigned char buf[4096] = {" + ",".join(str(b) for b in buf) + "};",
               f"    uint64_t d = digest(buf, 4096, {seed % 8}); tests++;",
               f"    if (d != {expected_digest}ULL) failures++;",
               '    printf("{\\"tests\\": %ld, \\"failures\\": %ld, \\"digest\\": \\"%016llx\\"}\\n", tests, failures, (unsigned long long)d);',
               "    return failures ? 1 : 0;",
               "}"]
    open(os.path.join(work, "test_main.c"), "w").write("\n".join(harness) + "\n")
    lines += len(harness)
    cc = shutil.which("gcc") or shutil.which("cc") or "gcc"
    t1 = time.perf_counter()
    srcs = sorted(f for f in os.listdir(work) if f.endswith(".c"))
    comp = subprocess.run([cc, "-O2", "-std=c11", "-o", "prog", *srcs], cwd=work,
                          capture_output=True, text=True)
    compile_ms = (time.perf_counter() - t1) * 1000
    if comp.returncode != 0:
        print(json.dumps({"error": "compile failed: " + comp.stderr[-400:]}))
        sys.exit(2)
    t2 = time.perf_counter()
    run = subprocess.run([os.path.join(work, "prog")], cwd=work, capture_output=True, text=True)
    test_ms = (time.perf_counter() - t2) * 1000
    try:
        res = json.loads(run.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        print(json.dumps({"error": "test harness produced no result: " + run.stderr[-300:]}))
        sys.exit(3)
    cpu = resource.getrusage(resource.RUSAGE_SELF)
    kids = resource.getrusage(resource.RUSAGE_CHILDREN)
    print(json.dumps({
        "files": FILES + 3, "functions": len(funcs) * 2, "lines": lines,
        "compile_ms": round(compile_ms, 1), "test_ms": round(test_ms, 1),
        "tests": res["tests"], "failures": res["failures"], "digest": res["digest"],
        "cpu_ms": round((cpu.ru_utime + cpu.ru_stime + kids.ru_utime + kids.ru_stime) * 1000, 1),
        "compute_ms": round((time.perf_counter() - t0) * 1000, 1),
    }))
    sys.exit(0 if res["failures"] == 0 else 1)
finally:
    shutil.rmtree(work, ignore_errors=True)
