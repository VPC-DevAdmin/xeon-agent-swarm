"""
backend/observability/validation_judge.py

L1/L2 judge validation — the graded tiers above L0 mechanical
(docs/validation_directive.md). After L0 passes, a role configured for `judge`
(tier1/2) or `frontier` (tier4/5) validation gets a grader call via the gateway:
the grader reads the subtask + the worker's output and emits a structured verdict
(pass | degraded | fail). On reject, the failed subtask is re-dispatched to the
worker with the critique, bounded by the role's `retries`; on exhaustion the step
is marked degraded and surfaced — never silently passed (directive guardrail).

The judge runs through ModelFactory like any other call, so it routes to its
declared tier; its token spend is captured on the Validation row, kept separate
from generation. A validator failure must not break the run, so any grader error
degrades to a non-blocking pass with the error noted.

These are pure functions over a ModelFactory; the event adapter is handed already
-bound callables (make_judge / make_redispatch) so it stays decoupled from the
model layer and easy to fake in tests.
"""
from __future__ import annotations

import json
import os
import re

from backend.observability.callbacks import to_internal_tier
from backend.agents.profiles import load_roles

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)

_GRADER_SYSTEM = """You are a strict output validator. You are given a SUBTASK and a
WORKER OUTPUT. Judge ONLY whether the output is coherent and faithfully fulfills the
subtask instruction. Do NOT re-do the task. Be terse.

Respond with ONLY this JSON object, no markdown fences:
{"verdict": "pass" | "degraded" | "fail", "score": 0.0-1.0, "critique": "one specific sentence; empty if pass"}

- pass:     fully addresses the subtask, coherent, on-instruction.
- degraded: usable but incomplete, partially off-instruction, or thin.
- fail:     does not address the subtask, incoherent, or empty.""".strip()

_VALID_VERDICTS = {"pass", "degraded", "fail"}


def _parse(text: str) -> dict | None:
    try:
        obj = json.loads(_FENCE.sub("", text or "").strip())
        return obj if isinstance(obj, dict) else None
    except (json.JSONDecodeError, ValueError):
        return None


def _headers(resp) -> dict:
    meta = getattr(resp, "response_metadata", {}) or {}
    return {k.lower(): v for k, v in (meta.get("headers", {}) or {}).items()}


def _usage(resp) -> tuple[int, int]:
    u = getattr(resp, "usage_metadata", None) or {}
    return int(u.get("input_tokens") or 0), int(u.get("output_tokens") or 0)


async def judge_result(*, subtask: str, result_text: str, role: str, cfg: dict, mf) -> dict:
    """Grade one worker result. Returns a verdict dict ready for record_validation.

    Never raises: a grader error degrades to a non-blocking pass (guardrail).
    """
    level = cfg.get("level", "judge")
    tier = cfg.get("tier", "tier1")
    rubric = cfg.get("rubric")
    prompt = (
        f"SUBTASK (role={role}):\n{subtask}\n\n"
        f"WORKER OUTPUT:\n{(result_text or '')[:4000]}\n\n"
        f"Rubric: {rubric or 'general coherence and on-instruction'}"
    )
    messages = [{"role": "system", "content": _GRADER_SYSTEM},
                {"role": "user", "content": prompt}]

    verdict, score, critique, tier_obs = "pass", 1.0, "", tier
    tin = tout = 0
    try:
        resp = await mf.for_tier(tier).ainvoke(messages)
        tin, tout = _usage(resp)
        raw = _headers(resp).get("x-vsr-selected-model")
        tier_obs = to_internal_tier(raw) if raw else tier
        parsed = _parse(resp.content if isinstance(resp.content, str) else str(resp.content))
        if parsed:
            v = str(parsed.get("verdict", "pass")).lower()
            verdict = v if v in _VALID_VERDICTS else "degraded"
            try:
                score = float(parsed.get("score", 1.0 if verdict == "pass" else 0.5))
            except (TypeError, ValueError):
                score = 0.5
            critique = str(parsed.get("critique", "") or "")
        # else: grader returned non-JSON — leave as a non-blocking pass.
    except Exception as exc:  # noqa: BLE001 — a flaky validator must not fail the run
        verdict, score, critique, tier_obs = "pass", 1.0, f"validator error: {exc}", None

    return {
        "level": level, "verdict": verdict, "score": round(score, 3),
        "critique": critique, "validator_tier": tier_obs, "rubric_id": rubric,
        "tokens_in": tin, "tokens_out": tout,
    }


async def redispatch_worker(*, role: str, subtask: str, critique: str, mf,
                            roles: dict | None = None) -> dict:
    """Re-run a worker on its failed subtask with the validator's critique.

    Subagents are stateless single-handoff (plan §4.2), so a re-dispatch is a fresh
    worker call on mf.auto() using the role's system prompt + the critique. Returns
    a telemetry dict; an empty result on error lets L0 fail it on the next pass.
    """
    roles = roles if roles is not None else load_roles()
    cfg = roles.get(role) or roles.get("general") or {}
    system = (cfg.get("system_prompt") or "").strip()
    user = (f"Subtask: {subtask}\n\nYour previous attempt was rejected by validation: "
            f"{critique}\n\nProduce a corrected result that fixes this.")
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]

    text, tin, tout, tier_obs, cache_hit = "", 0, 0, None, False
    try:
        resp = await mf.auto().ainvoke(messages)
        text = resp.content if isinstance(resp.content, str) else str(resp.content)
        tin, tout = _usage(resp)
        hdrs = _headers(resp)
        cache_hit = "x-vsr-selected-model" not in hdrs
        raw = hdrs.get("x-vsr-selected-model")
        tier_obs = to_internal_tier(raw) if raw else None
    except Exception:  # noqa: BLE001 — empty result degrades cleanly downstream
        text = ""

    return {"result": text, "tokens_in": tin, "tokens_out": tout,
            "tier_observed": tier_obs, "cache_hit": cache_hit}


_SYNTH_GRADER_SYSTEM = """You are the final-output validator for a multi-agent run. You
are given the original OBJECTIVE, the SUBTASK RESULTS the workers produced, and the
SYNTHESIZED ANSWER that combined them. Judge ONLY the synthesized answer:

- Does it actually answer the objective?
- Is it faithful to the subtask results (no contradictions, no confabulated joins)?
- Did it drop a requirement the subtasks covered?

Judge SUBSTANCE, not phrasing: do not penalize incidental preamble, tone, or minor
formatting if the answer's content is correct and complete. Reserve `fail` for a genuine
substance failure (wrong, missing the objective, or contradicting/confabulating results),
not for a stylistic blemish.

Do NOT re-do the work. Respond with ONLY this JSON, no fences:
{"verdict": "pass" | "degraded" | "fail", "score": 0.0-1.0, "critique": "one specific sentence; empty if pass"}
- pass:     answers the objective, faithful, complete.
- degraded: usable but drops a requirement, thin, or partially unsupported.
- fail:     does not answer the objective, or contradicts/confabulates the results.""".strip()


async def grade_synthesis(*, objective: str, final_answer: str, results: list[dict],
                          mf, tier: str | None = None) -> dict:
    """L2 frontier grade of the final synthesized answer (directive §Synthesis) —
    the single most important validator. Compares the answer against the objective
    and the subtask results. Never raises (same guardrail as judge_result)."""
    tier = tier or os.environ.get("ADL_SYNTHESIS_VALIDATOR_TIER", "tier4")
    digest = "\n\n".join(
        f"[{r.get('step_key', i)} · {r.get('terminal', '?')}]: {(r.get('result') or '')[:800]}"
        for i, r in enumerate(results)
    ) or "(no subtask results)"
    prompt = (f"OBJECTIVE:\n{objective}\n\nSUBTASK RESULTS:\n{digest}\n\n"
              f"SYNTHESIZED ANSWER:\n{(final_answer or '')[:6000]}")
    messages = [{"role": "system", "content": _SYNTH_GRADER_SYSTEM},
                {"role": "user", "content": prompt}]

    verdict, score, critique, tier_obs = "pass", 1.0, "", tier
    tin = tout = 0
    try:
        resp = await mf.for_tier(tier).ainvoke(messages)
        tin, tout = _usage(resp)
        raw = _headers(resp).get("x-vsr-selected-model")
        tier_obs = to_internal_tier(raw) if raw else tier
        parsed = _parse(resp.content if isinstance(resp.content, str) else str(resp.content))
        if parsed:
            v = str(parsed.get("verdict", "pass")).lower()
            verdict = v if v in _VALID_VERDICTS else "degraded"
            try:
                score = float(parsed.get("score", 1.0 if verdict == "pass" else 0.5))
            except (TypeError, ValueError):
                score = 0.5
            critique = str(parsed.get("critique", "") or "")
    except Exception as exc:  # noqa: BLE001
        verdict, score, critique, tier_obs = "pass", 1.0, f"validator error: {exc}", None

    return {"level": "frontier", "verdict": verdict, "score": round(score, 3),
            "critique": critique, "validator_tier": tier_obs, "rubric_id": "synthesis_v1",
            "tokens_in": tin, "tokens_out": tout}


_PARTIAL_SYNTH_SYSTEM = """You are composing the FINAL answer for a multi-agent run that was
stopped early (a budget ceiling was hit) before the orchestrator could write its own
synthesis. Using the OBJECTIVE and the SUBTASK RESULTS gathered so far, write the best single
coherent answer the available material supports — use the workers' specific findings, and note
briefly and honestly if it is partial. Output ONLY the answer: no preamble, no meta-commentary.""".strip()


async def synthesize_partial(*, objective: str, results: list[dict], mf,
                             tier: str | None = None) -> dict:
    """Compose a fallback final answer from partial subtask results after a budget stop.

    The orchestrator graph is abandoned on breach before it synthesizes, so without this
    the run would finalize with an empty answer. Uses the planner tier (plan quality can't
    tolerate a downgrade). Never raises: on failure returns an empty answer so finalize
    proceeds. Returns telemetry for the routing rollup alongside `final_answer`."""
    tier = tier or os.environ.get("ADL_PLANNER_TIER", "T5")
    digest = "\n\n".join(
        f"[{r.get('step_key', i)} · {r.get('terminal', '?')}]: {(r.get('result') or '')[:1200]}"
        for i, r in enumerate(results)
    ) or "(no subtask results)"
    messages = [{"role": "system", "content": _PARTIAL_SYNTH_SYSTEM},
                {"role": "user", "content": f"OBJECTIVE:\n{objective}\n\nSUBTASK RESULTS:\n{digest}"}]

    text, tin, tout, tier_obs = "", 0, 0, tier
    try:
        resp = await mf.for_tier(tier).ainvoke(messages)
        text = resp.content if isinstance(resp.content, str) else str(resp.content)
        tin, tout = _usage(resp)
        raw = _headers(resp).get("x-vsr-selected-model")
        tier_obs = to_internal_tier(raw) if raw else tier
    except Exception:  # noqa: BLE001 — empty answer lets finalize proceed cleanly
        text = ""

    return {"final_answer": text, "tokens_in": tin, "tokens_out": tout,
            "tier_observed": tier_obs}


def make_judge(mf):
    """Bind a ModelFactory into a judge callable for the event adapter."""
    async def _judge(subtask: str, result_text: str, role: str, cfg: dict) -> dict:
        return await judge_result(subtask=subtask, result_text=result_text,
                                  role=role, cfg=cfg, mf=mf)
    return _judge


def make_redispatch(mf):
    """Bind a ModelFactory into a re-dispatch callable for the event adapter."""
    roles = load_roles()
    async def _redispatch(role: str, subtask: str, critique: str) -> dict:
        return await redispatch_worker(role=role, subtask=subtask, critique=critique,
                                       mf=mf, roles=roles)
    return _redispatch


def make_synthesis_grader(mf):
    """Bind a ModelFactory into a synthesis-grader callable for the event adapter."""
    async def _grade(objective: str, final_answer: str, results: list[dict]) -> dict:
        return await grade_synthesis(objective=objective, final_answer=final_answer,
                                     results=results, mf=mf)
    return _grade


def make_partial_synthesizer(mf):
    """Bind a ModelFactory into a partial-synthesis callable for the event adapter."""
    async def _synth(objective: str, results: list[dict]) -> dict:
        return await synthesize_partial(objective=objective, results=results, mf=mf)
    return _synth
