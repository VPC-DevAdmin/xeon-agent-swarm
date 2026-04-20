from __future__ import annotations

import ast
import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


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


class TaskSpec(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    description: str
    type: TaskType
    dependencies: list[str] = []
    priority: int = 1


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


# ── Structured document output ────────────────────────────────────────────────

class DocumentSection(BaseModel):
    title: str
    content: str
    sources: list[str] = []


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


class RunResult(BaseModel):
    """Top-level result returned to the frontend."""
    run_id: str
    swarm: SwarmState
    document: Optional[DocumentResult] = None
    single_model: Optional["SingleModelResult"] = None


# ── WebSocket event envelope ──────────────────────────────────────────────────

class EventType(str, Enum):
    run_started       = "run_started"
    graph_ready       = "graph_ready"
    task_started      = "task_started"
    task_token        = "task_token"       # streaming token from a worker (writing task)
    task_completed    = "task_completed"
    task_failed       = "task_failed"
    task_killed       = "task_killed"
    synthesis_started = "synthesis_started"
    run_completed     = "run_completed"
    error             = "error"


class SwarmEvent(BaseModel):
    event: EventType
    run_id: str
    payload: dict
    timestamp: datetime = Field(default_factory=datetime.utcnow)


# ── A/B single-model result (used when ENABLE_AB_COMPARISON=1) ───────────────

class SingleModelResult(BaseModel):
    run_id: str
    query: str
    answer: str
    model_used: str
    hardware: str
    latency_ms: float
    status: TaskStatus
    context_chunks_retrieved: int = 0
    context_chunks_included: int = 0
    context_chunks_cited: int = 0
    context_token_estimate: int = 0
    context_rot_score: float = 0.0


# ── HTTP request/response models ──────────────────────────────────────────────

class RunRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)


class KillTaskRequest(BaseModel):
    task_id: str


# Resolve forward reference in RunResult.single_model
RunResult.model_rebuild()
