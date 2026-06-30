"""
backend/observability/validation_l0.py

L0 mechanical validation — the zero-token tier of the validation stack
(docs/validation_directive.md). Runs first, always, on every subagent result as
it lands in the event adapter. No model call: schema/required-field presence,
length, JSON-validates, and cheap artifact-shape checks (table dimensions, Python
syntax) ported from the old agents/validator.py mechanical path.

A subagent returns only its final message (a string), so L0 inspects that string.
The worker_roles.yaml prompts ask for a JSON envelope ({"result", "confidence",
"artifact(s)"}); when the worker complies we validate its fields and artifacts,
and when it returns prose we fall back to length/non-empty checks. L0 never
fabricates a pass: an empty or malformed result is reported as fail/degraded and
surfaced to synthesis and the run record.

Verdicts: 'pass' (clean), 'degraded' (usable but below bar — short, prose instead
of JSON, minor shape issue), 'fail' (unusable — empty). The cheap-judge (L1) and
frontier (L2) tiers handle what mechanical checks can't, per the directive.
"""
from __future__ import annotations

import ast
import json
import re

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)

# Roles whose prompts specify a JSON envelope (worker_roles.yaml). Others return
# prose and only get the length/non-empty checks.
_JSON_ROLES = {"research", "analysis", "code", "fact_check", "summarization"}


def _strip_fences(text: str) -> str:
    return _FENCE.sub("", text).strip()


def _try_json(text: str) -> dict | list | None:
    try:
        return json.loads(_strip_fences(text))
    except (json.JSONDecodeError, ValueError):
        return None


def validate_python_syntax(code: str) -> bool:
    try:
        ast.parse(code)
        return True
    except SyntaxError:
        return False


def _check_artifacts(obj: dict, checks: list[dict]) -> str:
    """Cheap shape checks on any artifacts the worker returned. Returns worst
    severity contributed ('' | 'degraded' | 'fail')."""
    arts = obj.get("artifacts")
    if arts is None and isinstance(obj.get("artifact"), dict):
        arts = [obj["artifact"]]
    if not isinstance(arts, list):
        return ""
    worst = ""
    for art in arts:
        if not isinstance(art, dict):
            continue
        kind = art.get("type")
        content = art.get("content") or {}
        if kind == "table":
            rows = content.get("rows") or []
            headers = content.get("headers") or []
            ok = len(rows) >= 1 and len(headers) >= 2
            checks.append({"check": "table_shape", "ok": ok,
                           "detail": f"{len(rows)} rows x {len(headers)} cols"})
            if not ok:
                worst = "degraded"
        elif kind == "code":
            src = content.get("code") or content.get("source") or ""
            lang = (content.get("language") or "python").lower()
            ok = True if lang != "python" else validate_python_syntax(src)
            checks.append({"check": "code_syntax", "ok": ok, "detail": lang})
            if not ok:
                worst = "degraded"
    return worst


def validate_l0(text: str | None, *, role: str | None = None,
                min_length: int = 50) -> dict:
    """Run the mechanical checks on a subagent result string.

    Returns: {level, verdict, score, checks: [...], detail}.
    """
    checks: list[dict] = []
    body = (text or "").strip()

    # 1. Non-empty — a hard failure.
    non_empty = bool(body)
    checks.append({"check": "non_empty", "ok": non_empty})
    if not non_empty:
        return {"level": "mechanical", "verdict": "fail", "score": 0.0,
                "checks": checks, "detail": "empty result"}

    verdict = "pass"

    # 2. Minimum length — short results are usable but below bar.
    long_enough = len(body) >= min_length
    checks.append({"check": "min_length", "ok": long_enough,
                   "detail": f"{len(body)} chars"})
    if not long_enough:
        verdict = "degraded"

    # 3. JSON envelope checks for roles whose prompt specifies one.
    if role in _JSON_ROLES:
        obj = _try_json(body)
        parsed = isinstance(obj, (dict, list))
        checks.append({"check": "json_parses", "ok": parsed})
        if not parsed:
            verdict = "degraded"  # worker returned prose; usable but off-contract
        elif isinstance(obj, dict):
            has_result = bool(str(obj.get("result", "")).strip())
            checks.append({"check": "result_field", "ok": has_result})
            if not has_result:
                verdict = "degraded"
            conf = obj.get("confidence")
            if conf is not None:
                in_range = isinstance(conf, (int, float)) and 0.0 <= conf <= 1.0
                checks.append({"check": "confidence_range", "ok": in_range,
                               "detail": conf})
                if not in_range:
                    verdict = "degraded"
            if _check_artifacts(obj, checks) == "degraded" and verdict == "pass":
                verdict = "degraded"

    passed = sum(1 for c in checks if c.get("ok"))
    score = round(passed / len(checks), 3) if checks else 1.0
    detail = "; ".join(
        c["check"] + ("" if c.get("ok") else " FAILED") for c in checks
    )
    return {"level": "mechanical", "verdict": verdict, "score": score,
            "checks": checks, "detail": detail}
