from pydantic import BaseModel, Field
from typing import Literal, Optional
from enum import Enum
import uuid
from datetime import datetime


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


class TaskSpec(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    description: str
    type: TaskType
    dependencies: list[str] = []   # task IDs that must complete first
    priority: int = 1              # 1 = normal, 2 = high


class TaskGraph(BaseModel):
    query: str
    tasks: list[TaskSpec]
    reasoning: str                 # orchestrator's explanation of decomposition


class AgentResult(BaseModel):
    task_id: str
    status: TaskStatus
    result: str
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


class DocumentResult(BaseModel):
    """Structured intelligence report produced by the writing worker."""
    title: str
    executive_summary: str
    sections: list[DocumentSection] = []
    code_snippets: list[CodeSnippet] = []
    key_findings: list[str] = []
    sources: list[str] = []
    diagram_mermaid: Optional[str] = None   # Mermaid.js diagram syntax
    tts_audio_url: Optional[str] = None     # served at /audio/{run_id}.mp3


class SwarmState(BaseModel):
    run_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    query: str
    task_graph: Optional[TaskGraph] = None
    results: dict[str, AgentResult] = {}
    final_answer: Optional[str] = None
    status: TaskStatus = TaskStatus.pending
    started_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None


class SingleModelResult(BaseModel):
    run_id: str
    query: str
    answer: str
    model_used: str
    hardware: str
    latency_ms: float
    status: TaskStatus
    # Context-rot metrics — populated when corpus RAG is enabled
    context_chunks_retrieved: int = 0   # chunks fetched from corpus
    context_chunks_included: int = 0    # chunks that fit in context window
    context_chunks_cited: int = 0       # sources the model referenced in its answer
    context_token_estimate: int = 0     # estimated tokens of context injected
    context_rot_score: float = 0.0      # 1 - (cited / included); 1.0 = total waste


class RunResult(BaseModel):
    """Top-level result returned to frontend for the A/B view."""
    run_id: str
    swarm: SwarmState
    single_model: Optional[SingleModelResult] = None
    document: Optional[DocumentResult] = None


# ── WebSocket event envelope ──────────────────────────────────────────────────

class EventType(str, Enum):
    run_started       = "run_started"
    graph_ready       = "graph_ready"        # orchestrator finished decomposing
    task_started      = "task_started"
    task_completed    = "task_completed"
    task_failed       = "task_failed"
    synthesis_started = "synthesis_started"
    run_completed     = "run_completed"
    single_started    = "single_started"     # A/B single-model events
    single_token      = "single_token"       # streaming token
    single_retrying   = "single_retrying"    # context overflow → retry with fewer chunks
    single_completed  = "single_completed"
    error             = "error"


class SwarmEvent(BaseModel):
    event: EventType
    run_id: str
    payload: dict                            # event-specific data
    timestamp: datetime = Field(default_factory=datetime.utcnow)


# ── HTTP request/response models ──────────────────────────────────────────────

class RunRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)
