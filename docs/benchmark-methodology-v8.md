# Agent capacity benchmark methodology v8

Version 8 preserves the two v7 reporting points and makes their statistical
and operational evidence executable. Results from earlier workload versions
are not comparable with v8.

## Reported points

**Service capability** is the largest whole-tile concurrent-session level at
which every active workflow type meets the selected fixed deadline with an
on-time success probability of at least 95 percent. The deadline is workload
policy, not a value learned from the host.

**Sustainable capacity** is the conservative clean-workflow arrival rate at
the fitted queue knee, measured by an open-loop generator before independently
confirmed backlog divergence or technical failure.

The closed-loop stability ceiling and efficiency knee remain diagnostics. They
do not substitute for either reported point.

## Service capability decision

Capability is one joint claim across all workflow types. Per-type one-sided
Wilson lower bounds therefore use a Bonferroni-adjusted alpha of
`0.05 / active_types`. The sample floor is derived from that adjusted bound and
the 95 percent target; an operator-configured sample floor may only raise it.

A candidate has three possible outcomes:

- **pass**: every type has enough decided samples and its adjusted lower bound
  is at least 0.95;
- **fail**: at least one sufficiently sampled type has a lower bound below
  0.95;
- **inconclusive**: the evidence budget expired without enough decided samples.

Search begins at the closed-loop level already reached. It descends
exponentially by whole tiles until it finds a pass, then refines between an
actual fail and pass to one tile. An inconclusive candidate never supplies the
failed side of a bracket. Without an adjacent failed level, the passing level
is reported only as a lower bound.

## Sustainable capacity decision

The generator schedules arrivals independently of completions and records the
achieved rate. A level is invalid when achieved rate is below 95 percent of
offered rate. Settling and measurement windows expand with observed workflow
duration so slow workflows are not judged from fractional completions. The
geometric step is reduced to its square root when clean output falls below 95
percent of achieved arrivals or a suspected boundary fails confirmation; this
adds denser experimental points around the knee without slowing the clear-
headroom region.

Backlog analysis uses non-overlapping queue increments rather than ordinary
least squares on queue levels. A moving-block bootstrap retains serial
dependence and returns a one-sided 95 percent lower bound on queue growth. A
queue boundary requires that bound to exceed zero in two disjoint windows at
the same fixed offered rate. Technical failure boundaries receive the same
same-rate confirmation.

The capacity curve is constrained to

`clean throughput = k * min(achieved arrival rate, breakpoint)`.

The fit uses achieved rather than offered rates. A fixed-design residual
bootstrap preserves the experimental rate levels. Results report the point
estimate, a distinct one-sided 95 percent lower bound used as the conservative
headline, and a two-sided 95 percent interval using the 2.5th and 97.5th
percentiles. A knee is refused when the constrained saturation curve does not
materially outperform a single proportional line.

## Stops and validity

Queue divergence and independently confirmed technical failure establish a
boundary. CPU, memory, KV-cache, dollar, duration, configured-rate, and load-
generator stops censor the run and produce at most a lower bound. Predominant
background CPU is interference. Missing durable writes, callbacks, executor
counters, or excessive workload-contract violations invalidate the run.

These resource and interference rules apply to both closed- and open-loop
runs. Harness counters are differenced from a snapshot taken at run start, so
one run cannot contaminate the next.

## Workload and inference isolation

Reference workflows carry deterministic, fixed-size, seed-varying prompt
suffixes to defeat accidental prefix-cache benchmarking. Required trace fields
must be present. Version 8 additionally requires three observed tool calls for
the three-worker mock contract, and every benchmark audit tool call waits for
its own durable commit acknowledgement.

A publishable agent-host result requires an inference stand-in on another host
and an independently measured stand-in request-rate ceiling at least twice the
model-call demand observed by the benchmark. A loopback or unqualified mock is
allowed for diagnosis but is explicitly marked not publication-eligible.

## Repetition

A reportable set contains the requested number of accepted runs, and every run
must contain the intended metric. Partial sets publish child observations but
no median. Comparability includes workload fingerprint, commit, target,
backend, load model, service class, prompt corpus, CPU and memory profile,
NUMA topology, worker topology, and database type.

## Remaining declared limits

The open-loop generator remains in the control process and launches workflows
through the internal API rather than public ingress. The benchmark measures a
single agent host, controlled benchmark tools, and a fixed workload mix. It
does not yet certify multi-node coordination, production third-party tool
backends, long-duration soak behavior, or recovery after overload.
