from __future__ import annotations

import ast
import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, model_validator


# ── Task graph ────────────────────────────────────────────────────────────────

class TaskType(str, Enum):
    research      = "research"
    analysis      = "analysis"
    code          = "code"
    summarization = "summarization"
    vision        = "vision"
    fact_check    = "fact_check"
    writing       = "writing"
    general       = "general"


class TaskStatus(str, Enum):
    pending   = "pending"
    running   = "running"
    completed = "completed"
    failed    = "failed"
    killed    = "killed"          # user-triggered via Kill button


# Known deliverable format tokens — validator checks the output shape matches
DELIVERABLE_FORMATS = {
    "finding_list_with_citations":     "list of findings, each with source citation",
    "finding_list_with_numeric_values":"list of findings including specific numeric values",
    "comparison_table":                "Artifact with type=table, headers, rows",
    "mermaid_diagram":                 "Artifact with type=diagram, mermaid content",
    "code_block_python":               "Artifact with type=code, language=python, syntax-validated",
    "extracted_chart_data":            "extracted_data artifact with data_points from a chart",
    "component_diagram_description":   "extracted_data artifact listing architecture components",
    "claim_verdicts":                  "list of claim_verdict artifacts",
    "prose_section_with_citations":    "Artifact with type=prose, inline citations",
    "document_result":                 "Full DocumentResult with all sections",
}


class SourceConstraint(BaseModel):
    use_web: bool = False
    use_corpus: bool = True
    corpus_filter: str | None = None    # e.g. "ai_hardware"
    min_sources: int = 1


class TaskSpec(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    type: TaskType
    dependencies: list[str] = []
    priority: int = 1

    # Contract fields — produced by orchestrator, checked by validator
    objective: str = Field(
        default="",
        description="One sentence starting with an action verb",
    )
    scope: list[str] = Field(
        default_factory=list,
        description="Specific questions this task must answer (2-5 items)",
    )
    deliverable_format: str = Field(
        default="finding_list_with_citations",
        description="Known format token — see DELIVERABLE_FORMATS",
    )
    success_criteria: list[str] = Field(
        default_factory=list,
        description="Things that must be true of the output (2-4 items)",
    )
    source_constraints: SourceConstraint = Field(default_factory=SourceConstraint)
    max_retries: int = 3               # overrideable per-role default

    # Legacy field — kept for backward compat with existing orchestrator output
    description: str = ""

    # Vision-task extras
    expected_image_types: list[str] = []    # e.g. ["benchmark_chart"]
    fallback_behavior: Literal["skip", "retrieval_only", "describe"] = "retrieval_only"

    @property
    def display_description(self) -> str:
        """Return objective if set, fall back to description for backward compat."""
        return self.objective or self.description


class TaskGraph(BaseModel):
    query: str
    tasks: list[TaskSpec]
    reasoning: str


# ── Typed artifact system ─────────────────────────────────────────────────────
#
# Workers no longer return freeform prose. Each worker produces one or more
# typed Artifact objects. The frontend renders each type with a dedicated
# component (TableArtifact, DiagramArtifact, CodeArtifact, etc.).
#
# Only TaskType.writing workers produce ArtifactType.prose.

class ArtifactType(str, Enum):
    prose          = "prose"           # executive summary / section text (writing only)
    table          = "table"           # comparison or data table (analysis)
    diagram        = "diagram"         # Mermaid.js source (code)
    chart          = "chart"           # Recharts-compatible data series (vision/analysis)
    code           = "code"            # syntax-highlighted snippet (code)
    claim_verdict  = "claim_verdict"   # fact-check verdict on a specific claim
    citation_set   = "citation_set"    # grounded sources from research/rag
    extracted_data = "extracted_data"  # numeric data extracted from an image (vision)


class Artifact(BaseModel):
    """
    Typed output from a single worker.

    content shape by ArtifactType:

    prose:          {"text": "...", "section_title": "..."}
    table:          {"headers": [...], "rows": [[...], ...], "caption": "..."}
    diagram:        {"mermaid": "graph TD\\n  A --> B", "caption": "..."}
    chart:          {"series": [{"name": "...", "data": [{"x": ..., "y": ...}]}],
                     "x_label": "...", "y_label": "...", "chart_type": "bar|line",
                     "caption": "..."}
    code:           {"language": "python", "code": "...", "description": "...",
                     "syntax_valid": true|false}
    claim_verdict:  {"claim": "...", "verdict": "supported|unsupported|uncertain",
                     "evidence": "...", "source_url": "..."}
    citation_set:   {"citations": [{"title": "...", "url": "...", "snippet": "..."}]}
    extracted_data: {"description": "...",
                     "data_points": [{"label": "...", "value": "...", "unit": "..."}],
                     "source_image": "path/to/image.jpg"}
    """
    type: ArtifactType
    content: dict[str, Any]
    worker_id: str = ""
    confidence: float = 0.8
    source_chunks: list[str] = []
    render_targets: list[str] = ["html"]   # can include "audio", "download"


def validate_code_syntax(code: str, language: str) -> bool:
    """
    Validate code syntax server-side before delivering the artifact.
    Python: uses ast.parse(). Other languages pass through as True.
    """
    if language.lower() in ("python", "py"):
        try:
            ast.parse(code)
            return True
        except SyntaxError:
            return False
    return True


# ── Per-role structured output schemas ───────────────────────────────────────

class Finding(BaseModel):
    claim: str = Field(..., min_length=10)
    source_url: str | None = None
    source_corpus_id: str | None = None
    specific_numbers: list[str] = []


class ResearchResult(BaseModel):
    findings: list[Finding] = Field(default_factory=list)
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)


class ClaimVerdict(BaseModel):
    claim: str
    verdict: Literal["supported", "partially_supported", "unsupported", "contradicted"]
    evidence_quote: str | None = None


class FactCheckResult(BaseModel):
    verdicts: list[ClaimVerdict] = Field(default_factory=list)
    overall_confidence: float = Field(default=0.8, ge=0.0, le=1.0)


class VisionResult(BaseModel):
    image_found: bool
    image_id: str | None = None
    detected_type: Literal[
        "benchmark_chart", "architecture_diagram", "table", "photo", "other"
    ] | None = None
    extracted_data: dict = {}
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


# ── Validator models ──────────────────────────────────────────────────────────

class ValidationVerdict(BaseModel):
    compliant: bool
    failed_criteria: list[str] = []
    correction_hint: str = ""
    severity: Literal["minor", "major", "unfixable"] = "minor"


class WorkerAttempt(BaseModel):
    attempt_number: int
    result: "AgentResult"
    validation: ValidationVerdict | None = None


# ── Agent result ──────────────────────────────────────────────────────────────

class AgentResult(BaseModel):
    task_id: str
    status: TaskStatus
    result: str = ""               # plain-text summary (backward compat)
    artifacts: list[Artifact] = [] # typed structured outputs (new)
    confidence: float = 0.0
    model_used: str
    hardware: str
    latency_ms: float
    tool_calls: list[str] = []
    total_tokens: int = 0          # for metrics tracking


class AgentResultWithRetries(BaseModel):
    """Extends AgentResult to track the full retry history."""
    task_id: str
    final_result: AgentResult
    attempts: list[WorkerAttempt]
    status: Literal["approved", "approved_with_warnings", "rejected_committed", "skipped"]
    total_tokens: int


class RunMetrics(BaseModel):
    """Tracked per-run for the validator ON/OFF comparison."""
    run_id: str
    validator_enabled: bool
    total_tasks: int = 0
    total_attempts: int = 0
    total_retries: int = 0
    validations_run: int = 0
    validations_passed: int = 0
    validations_failed: int = 0
    workers_rejected_committed: int = 0
    total_tokens_in: int = 0
    total_tokens_out: int = 0
    total_tokens_validator: int = 0
    wall_clock_ms: float = 0.0


# ── Structured document output ────────────────────────────────────────────────

class DocumentSection(BaseModel):
    title: str
    content: str
    sources: list[str] = []
    render_targets: list[str] = ["html"]   # can include "audio"
    audio_url: str | None = None           # populated by TTS pass in reducer


class CodeSnippet(BaseModel):
    language: str
    description: str
    code: str
    syntax_valid: bool = True


class DocumentResult(BaseModel):
    """Structured intelligence report assembled by the writing worker."""
    title: str
    executive_summary: str
    sections: list[DocumentSection] = []
    code_snippets: list[CodeSnippet] = []
    key_findings: list[str] = []
    sources: list[str] = []
    diagram_mermaid: Optional[str] = None
    tts_audio_url: Optional[str] = None
    executive_summary_audio_url: Optional[str] = None
    # Collected typed artifacts from all workers — powers the output panel
    artifacts: list[Artifact] = []


class SwarmState(BaseModel):
    run_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    query: str
    task_graph: Optional[TaskGraph] = None
    results: dict[str, AgentResult] = {}
    final_answer: Optional[str] = None
    status: TaskStatus = TaskStatus.pending
    started_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None
    # Validator state
    validator_enabled: bool = True
    orchestrator_retries: int = 0
    run_metrics: Optional[RunMetrics] = None


class RunResult(BaseModel):
    """Top-level result returned to the frontend."""
    run_id: str
    swarm: SwarmState
    document: Optional[DocumentResult] = None


# ── WebSocket event envelope ──────────────────────────────────────────────────

class EventType(str, Enum):
    run_started           = "run_started"
    graph_ready           = "graph_ready"
    task_started          = "task_started"
    task_token            = "task_token"           # streaming token (writing task)
    task_completed        = "task_completed"
    task_failed           = "task_failed"
    task_killed           = "task_killed"
    validator_started     = "validator_started"    # validator checking output
    validator_approved    = "validator_approved"   # passed validation
    validator_rejected    = "validator_rejected"   # failed validation
    worker_retrying       = "worker_retrying"      # retrying with correction hint
    worker_rejected_final = "worker_rejected_final"# exceeded retry budget
    synthesis_started     = "synthesis_started"
    tts_started           = "tts_started"
    tts_completed         = "tts_completed"
    run_completed         = "run_completed"
    run_metrics           = "run_metrics"          # final metrics packet
    eval_completed        = "eval_completed"       # async quality eval finished
    error                 = "error"


# CloudEvents 1.0 type registry — maps our internal EventType to reverse-DNS
# CloudEvents `type` strings. See docs/standards.md §2.2. Reverse-DNS prefix
# (io.xeon.swarm.*) lets these events ride a shared CloudEvents bus later.
CE_TYPE: dict[EventType, str] = {
    EventType.run_started:           "io.xeon.swarm.run.started",
    EventType.graph_ready:           "io.xeon.swarm.plan.ready",
    EventType.task_started:          "io.xeon.swarm.step.started",
    EventType.task_token:            "io.xeon.swarm.step.token",
    EventType.task_completed:        "io.xeon.swarm.step.completed",
    EventType.task_failed:           "io.xeon.swarm.step.failed",
    EventType.task_killed:           "io.xeon.swarm.step.killed",
    EventType.validator_started:     "io.xeon.swarm.validator.started",
    EventType.validator_approved:    "io.xeon.swarm.validator.approved",
    EventType.validator_rejected:    "io.xeon.swarm.validator.rejected",
    EventType.worker_retrying:       "io.xeon.swarm.step.retrying",
    EventType.worker_rejected_final: "io.xeon.swarm.step.rejected_final",
    EventType.synthesis_started:     "io.xeon.swarm.reduce.started",
    EventType.tts_started:           "io.xeon.swarm.tts.started",
    EventType.tts_completed:         "io.xeon.swarm.tts.completed",
    EventType.run_completed:         "io.xeon.swarm.run.completed",
    EventType.run_metrics:           "io.xeon.swarm.run.metrics",
    EventType.eval_completed:        "io.xeon.swarm.eval.completed",
    EventType.error:                 "io.xeon.swarm.error",
}


class SwarmEvent(BaseModel):
    event: EventType
    run_id: str
    payload: dict
    timestamp: datetime = Field(default_factory=datetime.utcnow)

    def to_cloudevent(self) -> dict:
        """Render as a CloudEvents 1.0 structured-mode JSON envelope.

        The original short event name is preserved in `data._event` so consumers
        that don't want to reverse-map `type` can switch on it directly.
        See docs/standards.md §2.2.
        """
        # subject: point at the step when the payload identifies one
        subject = None
        task_id = self.payload.get("task_id") if isinstance(self.payload, dict) else None
        if task_id:
            subject = f"step:{task_id}"

        return {
            "specversion": "1.0",
            "type": CE_TYPE.get(self.event, f"io.xeon.swarm.{self.event.value}"),
            "source": f"/runs/{self.run_id}",
            "id": str(uuid.uuid4()),
            "time": self.timestamp.isoformat() + ("Z" if self.timestamp.tzinfo is None else ""),
            "subject": subject,
            "datacontenttype": "application/json",
            "data": {
                "_event": self.event.value,   # short name for easy dispatch
                "run_id": self.run_id,
                **(self.payload if isinstance(self.payload, dict) else {"value": self.payload}),
            },
        }


# ── HTTP request/response models ──────────────────────────────────────────────

class RunRequest(BaseModel):
    # 10 000 chars (~2 500 tokens) fits multi-paragraph research briefs comfortably.
    query: str = Field(..., min_length=1, max_length=10_000)
    validator_enabled: bool = True      # toggle contract enforcement + retry loop
    max_worker_retries: int = 3         # overall cap; per-role overrides in worker_roles.yaml


class KillTaskRequest(BaseModel):
    task_id: str


# Resolve forward references
WorkerAttempt.model_rebuild()
RunResult.model_rebuild()


# ── A2A (Agent-to-Agent) naming aliases ──────────────────────────────────────
#
# We align our vocabulary to Google's A2A protocol (see docs/standards.md §2.1).
# These aliases expose the A2A-canonical names so new code and external
# consumers can use them. They point at the existing models — the FULL A2A
# Task lifecycle (state machine + history + artifacts as first-class fields)
# lands with the persistence layer in Phase 2, where Job/Run/Step/Attempt
# become real DB entities.
#
#   A2A name     →  current model
#   ──────────       ─────────────
#   Task         →  TaskSpec      (a unit of work with its contract)
#   TaskResult   →  AgentResult   (the output + artifacts of running a Task)
#   Plan         →  TaskGraph     (the orchestrator's decomposition)
#
# Prefer the A2A names in new code.
Task = TaskSpec
TaskResult = AgentResult
Plan = TaskGraph
