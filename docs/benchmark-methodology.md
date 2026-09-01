# Agent capacity benchmark methodology

The methodology of record is the living document maintained alongside the
benchmark, currently at workload version 14 with offline judge rule post-2.
It supersedes the archived v8 text (docs/archive/benchmark-methodology-v8.md),
which predates several deliberate revisions and must not be cited:

- **Record-not-refuse for the inference stand-in.** A co-located (loopback)
  stand-in no longer disqualifies a run. Every result instead records the
  stand-in's location, worker count, latency distribution, request-rate
  headroom arithmetic, and its measured share of host CPU on the same
  attribution basis as every other component, so a reader weighs the cost
  (1.8-2% of host in published runs, counted inside the box totals) rather
  than trusting an eligibility rule.
- **Evidence ledgers and post-processable judgment.** Every run streams a
  per-unit ledger (submit/completion times, outcome, level, telemetry).
  Verdicts are recomputed from the ledger by a versioned offline judge
  (backend/capacity/judge.py); the in-run judge only steers the ramp. Rule
  changes are applied to history by re-judging stored ledgers, never by
  re-running load. Ledgers, judgments, and results are versioned in
  data/capacity/.
- **The deadline anchor and the confidence claim, stated precisely.** A
  certified level's claim is: each workflow type's on-time fraction is at
  least 95%, at 95% confidence jointly across types (Wilson lower bound,
  Bonferroni-split alpha), over the units that level decided. A level fails
  only when the Wilson upper bound refutes the target. The bound covers one
  level; selecting the best of many tested levels and run-to-run variation
  are covered by the repeat series, not by the within-level bound.
- **Vocabulary.** A *workflow* is one fixed-size unit of agent work (planner,
  three workers, synthesizer, validator: 10 model calls, 7 validations,
  3 tool records). A *session* is a closed-loop driver that runs workflows
  back to back with think time; session count is the capability metric. An
  *agent* in prose means a session. A *subagent* is one worker inside a
  workflow (about three in flight per session).
- **Throughput, measured.** The open-loop measurement ran at workload v15
  (context-weighted archetypes): a four-instance fleet sustained 453.6
  workflows/s box-wide, terminated by the cpu verdict at 90% host with all
  128 threads over 85% busy. The sweep judge (rule sweep-1) post-processes
  any ledger into rate windows with per-tier on-time bounds and backlog
  checks, so one sweep answers every deadline policy. Open loop is the
  primary throughput measurement; the closed loop's remaining role is the
  residency photograph. Sizing from the measured rate is Derived, no longer
  Projected.
