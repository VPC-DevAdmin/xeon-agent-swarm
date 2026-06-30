# Validation Directive — Tiered Per-Step Validation

Amends `execution_plan.md` (Stages 2 and 5) and resolves the validator/eval fork. Read this before implementing validation. The decision is **keep validation on every step**, implemented as a tier, not as a single frontier validator.

---

## Thesis this serves

The target is a high-volume orchestration fabric, not a one-shot research run: 100 to 1000 requests, each decomposing into 15 to 20 subtasks, many of them repeating structured operations (daily digests, looping updates, change notifications, tool pushes, data pulls) running largely unattended. In that setting a per-step validator is the trust layer. When no human reads each digest, the validator is the only thing asserting it was correct. So validation runs on **every step**, plus an output-level check on synthesis. Do not treat validation as an optional QA bolt-on.

---

## Validation is a tier, not a thing

Apply the router's own logic to verification: each step gets the cheapest sufficient validator. Three levels, chosen per subtask:

| Level | Cost | What it does | When |
| --- | --- | --- | --- |
| **L0 Mechanical** | zero tokens | schema/required-field presence, regex, range, JSON-validates, link-resolves | every structured/repeating subtask; runs first, always |
| **L1 Cheap judge** | tier1/tier2 via gateway | "coherent and on-instruction" grading where conformance isn't fully mechanical | subtasks L0 can't fully cover |
| **L2 Frontier** | tier4/tier5 via gateway | hard joins and high-stakes outputs | synthesis and complex subtasks only |

L0 catches the majority of failures in the repeating workload at no token cost (port the mechanical checks from the old `validator.py`). L1 and L2 route through the gateway like any other call. Most steps validate at L0 or L1; only the hard joins reach L2.

---

## Validator-as-agent

The validator is itself a tiered agent. Its level and tier are declared per role, the same way the planner's tier is declared. Extend `worker_roles.yaml` with a `validation` block per role:

```yaml
researcher:
  system_prompt: ...
  tools: [web_search]
  validation:
    level: judge            # mechanical | judge | frontier
    tier: tier1             # used when level != mechanical
    rubric: research_v1     # rubric id for the grader
    retries: 1              # bounded; see guardrails
digest_worker:
  validation:
    level: mechanical       # repeating structured output: no LLM needed
    retries: 1
synthesizer:
  validation:
    level: frontier
    tier: tier4             # hard join gets a strong validator
    rubric: synthesis_v1
    retries: 1
```

A complex synthesis may be a combined **synthesizer + validator** subtask run at tier4/tier5, rather than a separate step. Decide per objective.

---

## Where validation lives under deepagents

A subagent returns only its final message, so validation is an **output check on each subagent result**, not an inspection of the subagent's intermediate work. Do not try to thread a validator into the subagent's internal loop.

- **L0 mechanical** runs in the event adapter as each subagent result lands (pure function, no model).
- **L1/L2 judge** is a graded check. Use deepagents' `RubricMiddleware` (a grader subagent that emits structured per-criterion verdicts) as the native home, or a lightweight grader call via `ModelFactory.for_tier(validator_tier)`. Verify `RubricMiddleware`'s API against the installed 0.6.10 reference.
- **Retry-on-critique** is a bounded re-dispatch of the failed subtask, capped by `retries` and the run's `max_tool_hops` budget. On exhaustion, mark the step degraded and surface it to synthesis and the run record. Never silently pass a failed validation.

---

## Synthesis specifically

Synthesis is the highest-variance step: it reconciles K independent subtask outputs and is where contradictions, dropped requirements, and confabulated joins occur. A per-step worker validator does not protect this; it needs an **output-level grader on the final artifact** against the original objective and the subtask results. This is the single most important validator in the pipeline. In deepagents the synthesis is the main agent's composition step, so attach the synthesis grader as an output check on the final result (RubricMiddleware or an explicit grader subtask).

---

## Data model additions

Per-step validation record (extend the calls/steps schema):
```
validation(step_id, level, validator_tier, rubric_id, verdict, score, retries_used, escalated, ts)
```
Roll up **validation cost separately from generation cost** so the two are distinguishable in the summary and the UI.

---

## Instrumentation and UI

Emit validation events on the WS stream (`validate_start`, `validate_result`) so the existing UI shows per-step validation status (green/amber/red) and the validator tier used. Add to `MetricsHUD`: validation pass rate, validation-tier distribution, and validation cost vs generation cost.

---

## Env additions

```
ADL_VALIDATION_DEFAULT_LEVEL=judge        # mechanical | judge | frontier
ADL_DEFAULT_VALIDATOR_TIER=tier1
ADL_SYNTHESIS_VALIDATOR_TIER=tier4
ADL_MAX_VALIDATION_RETRIES=2
```

---

## Build-order changes (do not retrofit)

Fold validation into the existing stages instead of adding it after a green run:

- **Stage 2 (event adapter):** implement L0 mechanical validation in the adapter as results land; emit validation events and write validation records. This is free and catches most repeating-workload failures from day one.
- **Stage 3/4:** add L1 judge validators via the gateway, the per-role `validation` config, and the bounded retry loop.
- **Stage 5:** add the L2 frontier synthesis grader (RubricMiddleware), and include validation-retry caps in the budget middleware (`max_validation_retries` alongside `max_tool_hops`).

`evaluator.py` and the eval rubrics stay in-tree; the per-step path supersedes the old wholesale per-step frontier validator but reuses its mechanical checks and rubric definitions.

---

## The cost narrative (now real)

The earlier "route the workers cheap" story was thin because the pinned planner dominates token spend. Tiered validation is the real one: across 15 to 20 steps times hundreds of requests, validation is a large, repetitive volume, and routing each validator to the cheapest sufficient level (mechanical free, tier1 judge next, frontier only for hard joins) is a defensible savings curve. The claim is "we validate every step without paying frontier prices to do it," which is stronger and true.

---

## Guardrails

- **Bound every retry.** A flaky validator on a 20-step run is a latency cliff. Cap retries and degrade gracefully.
- **App DB is the system of record** for validation results, not the checkpointer.
- **Output checks only** on subagent results; do not poke subagent internals.
- **Mechanical first, always.** Never spend a token where a schema check suffices.
