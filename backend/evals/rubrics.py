"""
Mechanical eval rubrics per deliverable_format.

These run without an LLM — fast, deterministic structural checks on what a step
actually produced. Each rubric returns an EvalScore (0.0–1.0 + findings). The
async eval runner combines these with an optional LLM-judge pass.

Scoring philosophy: a rubric measures whether the output MATCHES ITS CONTRACT
(the deliverable_format the orchestrator promised), not whether the content is
correct — correctness is the LLM judge's job. A finding_list with no citations
scores low even if the findings are true, because the contract said "with
citations".
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field


@dataclass
class EvalScore:
    score: float                       # 0.0–1.0
    passed: bool
    findings: list[str] = field(default_factory=list)
    rubric: str = "mechanical"

    def as_dict(self) -> dict:
        return {
            "score": round(self.score, 3),
            "passed": self.passed,
            "findings": self.findings,
            "rubric": self.rubric,
        }


_URL_RE = re.compile(r"https?://\S+")
_CITATION_RE = re.compile(r"\[[^\]]+\]|\(\s*source\s*:|\bcit(?:ation|ed)\b", re.I)


def _artifacts_of(result: dict, *types: str) -> list[dict]:
    arts = result.get("artifacts") or []
    if not types:
        return arts
    return [a for a in arts if a.get("type") in types]


def _text_of(result: dict) -> str:
    return str(result.get("text") or "")


# ── Per-format rubrics ────────────────────────────────────────────────────────

def _eval_citations(result: dict) -> EvalScore:
    text = _text_of(result)
    arts = _artifacts_of(result)
    blob = text + " " + " ".join(str(a.get("content", "")) for a in arts)
    has_url = bool(_URL_RE.search(blob))
    has_cite = bool(_CITATION_RE.search(blob))
    if has_url and has_cite:
        return EvalScore(1.0, True, ["citations + URLs present"])
    if has_url or has_cite:
        return EvalScore(0.6, True, ["partial: some citation signal"])
    return EvalScore(0.2, False, ["no citations or source URLs found"])


def _eval_numeric(result: dict) -> EvalScore:
    blob = _text_of(result) + " " + " ".join(
        str(a.get("content", "")) for a in _artifacts_of(result))
    nums = re.findall(r"\b\d[\d,.]*\s*(?:%|GB|TB|TOPS|TFLOPS|W|MWh|ms|x|×)?\b", blob)
    if len(nums) >= 3:
        return EvalScore(1.0, True, [f"{len(nums)} numeric values present"])
    if nums:
        return EvalScore(0.6, True, [f"only {len(nums)} numeric value(s)"])
    return EvalScore(0.2, False, ["no specific numeric values found"])


def _eval_table(result: dict) -> EvalScore:
    tables = _artifacts_of(result, "table")
    if not tables:
        return EvalScore(0.1, False, ["no table artifact produced"])
    t = tables[0].get("content", {}) or {}
    headers = t.get("headers") or t.get("columns") or []
    rows = t.get("rows") or []
    if len(headers) >= 2 and len(rows) >= 2:
        # penalize empty/na cells
        flat = [str(c).strip().lower() for r in rows for c in (r if isinstance(r, list) else [])]
        na = sum(1 for c in flat if c in ("", "n/a", "na", "-", "tbd"))
        ratio = na / max(len(flat), 1)
        if ratio > 0.3:
            return EvalScore(0.6, True, [f"table OK but {int(ratio*100)}% empty/na cells"])
        return EvalScore(1.0, True, [f"{len(headers)}×{len(rows)} table, dense"])
    return EvalScore(0.4, False, [f"table too small ({len(headers)} cols × {len(rows)} rows)"])


def _eval_code(result: dict) -> EvalScore:
    codes = _artifacts_of(result, "code")
    blob = ""
    if codes:
        blob = str(codes[0].get("content", "") or "")
        if isinstance(codes[0].get("content"), dict):
            blob = str(codes[0]["content"].get("code", ""))
    if not blob:
        # fall back to fenced block in text
        m = re.search(r"```(?:python)?\n(.+?)```", _text_of(result), re.S)
        blob = m.group(1) if m else ""
    if not blob.strip():
        return EvalScore(0.1, False, ["no code produced"])
    try:
        ast.parse(blob)
        return EvalScore(1.0, True, ["python parses (AST valid)"])
    except SyntaxError as exc:
        return EvalScore(0.3, False, [f"python syntax error: {exc.msg} (line {exc.lineno})"])


def _eval_mermaid(result: dict) -> EvalScore:
    arts = _artifacts_of(result, "diagram")
    blob = ""
    if arts:
        c = arts[0].get("content", {})
        blob = c.get("mermaid", "") if isinstance(c, dict) else str(c)
    if not blob:
        blob = _text_of(result)
    if re.search(r"\b(graph|flowchart|sequenceDiagram|classDiagram)\b", blob):
        return EvalScore(1.0, True, ["valid mermaid prefix"])
    return EvalScore(0.2, False, ["no mermaid diagram keyword found"])


def _eval_claim_verdicts(result: dict) -> EvalScore:
    arts = _artifacts_of(result, "claim_verdict")
    if not arts:
        # maybe inline in text
        verdicts = re.findall(r"\b(supported|unsupported|uncertain|contradicted|partially_supported)\b",
                              _text_of(result), re.I)
        if verdicts:
            return EvalScore(0.6, True, [f"{len(verdicts)} inline verdict(s), not structured"])
        return EvalScore(0.1, False, ["no claim verdicts produced"])
    with_source = sum(1 for a in arts
                      if (a.get("content", {}) or {}).get("source")
                      or _URL_RE.search(str(a.get("content", ""))))
    ratio = with_source / max(len(arts), 1)
    if ratio >= 0.8:
        return EvalScore(1.0, True, [f"{len(arts)} verdicts, {int(ratio*100)}% sourced"])
    return EvalScore(0.6, True, [f"{len(arts)} verdicts, only {int(ratio*100)}% sourced"])


def _eval_extracted_data(result: dict) -> EvalScore:
    arts = _artifacts_of(result, "extracted_data")
    if not arts:
        return EvalScore(0.2, False, ["no extracted_data artifact"])
    pts = (arts[0].get("content", {}) or {}).get("data_points") \
        or (arts[0].get("content", {}) or {}).get("components") or []
    if len(pts) >= 2:
        return EvalScore(1.0, True, [f"{len(pts)} data points extracted"])
    return EvalScore(0.5, False, ["too few data points extracted"])


def _eval_prose(result: dict) -> EvalScore:
    text = _text_of(result)
    arts = _artifacts_of(result, "prose")
    if arts:
        text = str(arts[0].get("content", "")) or text
    words = len(text.split())
    has_cite = bool(_URL_RE.search(text) or _CITATION_RE.search(text))
    if words >= 80 and has_cite:
        return EvalScore(1.0, True, [f"{words} words with citations"])
    if words >= 80:
        return EvalScore(0.6, True, [f"{words} words but no inline citations"])
    return EvalScore(0.3, False, [f"too short ({words} words)"])


def _eval_document(result: dict) -> EvalScore:
    # document_result steps usually carry the final doc in the reducer, not here.
    text = _text_of(result)
    if len(text.split()) >= 50:
        return EvalScore(0.8, True, ["substantive section text"])
    return EvalScore(0.4, False, ["section text thin"])


_RUBRICS = {
    "finding_list_with_citations": _eval_citations,
    "finding_list_with_numeric_values": _eval_numeric,
    "comparison_table": _eval_table,
    "mermaid_diagram": _eval_mermaid,
    "code_block_python": _eval_code,
    "extracted_chart_data": _eval_extracted_data,
    "component_diagram_description": _eval_extracted_data,
    "claim_verdicts": _eval_claim_verdicts,
    "prose_section_with_citations": _eval_prose,
    "document_result": _eval_document,
}


def evaluate_mechanical(deliverable_format: str | None, result: dict | None) -> EvalScore:
    """Run the rubric for a deliverable_format against a step's result dict."""
    if not result:
        return EvalScore(0.0, False, ["no result to evaluate"])
    rubric = _RUBRICS.get(deliverable_format or "")
    if rubric is None:
        return EvalScore(0.5, True, [f"no rubric for '{deliverable_format}' — neutral"])
    try:
        return rubric(result)
    except Exception as exc:  # a rubric bug shouldn't fail the eval
        return EvalScore(0.5, True, [f"rubric error: {exc}"])
