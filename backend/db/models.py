"""
ORM models for the orchestration platform's persistence layer.

Entity hierarchy (see docs/standards.md §2.1 for A2A alignment):

    Job   ──< Run ──< Step ──< StepAttempt
     │
     └──< JobConnector >── Connector ──< ConnectorSecret

  Job          a user-defined orchestration unit (query + schedule + config)
  Run          one execution of a Job (or an ad-hoc one-shot)
  Step         a sub-task within a Run (A2A Task: research/analysis/writing/…)
  StepAttempt  one execution try of a Step (validator retries → multiple)
  Connector    a tool/credential integration (Slack, GitHub, MCP, router, …)
  ConnectorSecret  Fernet-encrypted credential value
  JobConnector grants a Job permission to use a Connector
  AuditLog     append-only record of sensitive operations (secret decryption)

Timestamps are timezone-aware (TIMESTAMPTZ). Primary keys are UUIDv7 strings
(time-ordered — see backend/db/ids.py).
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Double,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

# SQLite-backed: generic JSON holds dicts AND lists (stored as JSON text). JSONB
# was Postgres-specific; JSON covers it. List columns (e.g. step dependencies)
# are plain JSON arrays. Repo code reads/writes Python lists/dicts unchanged.
JSONB = JSON

from backend.db.base import Base
from backend.db.ids import uuid7_str


def _pk() -> Mapped[str]:
    return mapped_column(String(36), primary_key=True, default=uuid7_str)


def _now_col(**kw) -> Mapped[datetime]:
    return mapped_column(
        DateTime(timezone=True), server_default=func.now(), **kw
    )


# ── Jobs ──────────────────────────────────────────────────────────────────────

class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = _pk()
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    query: Mapped[str] = mapped_column(Text, nullable=False)
    config: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    schedule_cron: Mapped[str | None] = mapped_column(String(120))
    schedule_tz: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC")
    overlap_policy: Mapped[str] = mapped_column(String(16), nullable=False, default="skip")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")

    next_fire_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_run_id: Mapped[str | None] = mapped_column(String(36))
    owner: Mapped[str | None] = mapped_column(String(120))

    created_at: Mapped[datetime] = _now_col()
    updated_at: Mapped[datetime] = _now_col(onupdate=func.now())
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    runs: Mapped[list["Run"]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )
    connectors: Mapped[list["JobConnector"]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('active','paused','archived')", name="ck_jobs_status"
        ),
        CheckConstraint(
            "overlap_policy IN ('skip','queue','parallel')",
            name="ck_jobs_overlap",
        ),
        Index("idx_jobs_active_due", "next_fire_at"),
    )


# ── Runs ──────────────────────────────────────────────────────────────────────

class Run(Base):
    __tablename__ = "runs"

    id: Mapped[str] = _pk()
    job_id: Mapped[str | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="SET NULL")
    )
    trigger: Mapped[str] = mapped_column(String(16), nullable=False)
    query: Mapped[str] = mapped_column(Text, nullable=False)
    config: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")

    task_graph: Mapped[dict | None] = mapped_column(JSONB)
    document_result: Mapped[dict | None] = mapped_column(JSONB)
    metrics: Mapped[dict | None] = mapped_column(JSONB)
    langfuse_trace_id: Mapped[str | None] = mapped_column(String(64))
    error: Mapped[str | None] = mapped_column(Text)

    started_at: Mapped[datetime] = _now_col()
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    job: Mapped["Job | None"] = relationship(back_populates="runs")
    steps: Mapped[list["Step"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint(
            "trigger IN ('schedule','manual','api','retry')",
            name="ck_runs_trigger",
        ),
        CheckConstraint(
            "status IN ('pending','orchestrating','running','reducing',"
            "'completed','failed','killed')",
            name="ck_runs_status",
        ),
        Index("idx_runs_job_started", "job_id", "started_at"),
        Index("idx_runs_status", "status"),
    )


# ── Steps ─────────────────────────────────────────────────────────────────────

class Step(Base):
    __tablename__ = "steps"

    id: Mapped[str] = _pk()
    run_id: Mapped[str] = mapped_column(
        ForeignKey("runs.id", ondelete="CASCADE"), nullable=False
    )
    step_key: Mapped[str] = mapped_column(String(32), nullable=False)  # 't1', 't2'
    type: Mapped[str] = mapped_column(String(24), nullable=False)
    objective: Mapped[str | None] = mapped_column(Text)
    scope: Mapped[str | None] = mapped_column(Text)
    success_criteria: Mapped[dict | None] = mapped_column(JSONB)
    deliverable_format: Mapped[str | None] = mapped_column(String(48))
    source_constraints: Mapped[dict | None] = mapped_column(JSONB)
    dependencies: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list   # JSON array of step_keys
    )

    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    result: Mapped[dict | None] = mapped_column(JSONB)
    confidence: Mapped[float | None] = mapped_column(Double)
    total_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    latency_ms: Mapped[float | None] = mapped_column(Double)

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    run: Mapped["Run"] = relationship(back_populates="steps")
    attempts: Mapped[list["StepAttempt"]] = relationship(
        back_populates="step", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("run_id", "step_key", name="uq_steps_run_key"),
        CheckConstraint(
            "status IN ('pending','running','validating','retrying',"
            "'completed','failed','killed','rejected_final')",
            name="ck_steps_status",
        ),
        Index("idx_steps_run", "run_id"),
    )


# ── Step attempts ─────────────────────────────────────────────────────────────

class StepAttempt(Base):
    __tablename__ = "step_attempts"

    id: Mapped[str] = _pk()
    step_id: Mapped[str] = mapped_column(
        ForeignKey("steps.id", ondelete="CASCADE"), nullable=False
    )
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    result: Mapped[dict | None] = mapped_column(JSONB)
    validator_verdict: Mapped[dict | None] = mapped_column(JSONB)
    correction_hint: Mapped[str | None] = mapped_column(Text)
    tokens_in: Mapped[int | None] = mapped_column(Integer)
    tokens_out: Mapped[int | None] = mapped_column(Integer)
    model_id: Mapped[str | None] = mapped_column(String(120))  # x-llm-model-served
    latency_ms: Mapped[float | None] = mapped_column(Double)

    started_at: Mapped[datetime] = _now_col()
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    step: Mapped["Step"] = relationship(back_populates="attempts")

    __table_args__ = (
        UniqueConstraint("step_id", "attempt_no", name="uq_attempts_step_no"),
        CheckConstraint(
            "status IN ('completed','failed','validator_rejected')",
            name="ck_attempts_status",
        ),
    )


# ── Connectors ────────────────────────────────────────────────────────────────

class Connector(Base):
    __tablename__ = "connectors"

    id: Mapped[str] = _pk()
    name: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    config: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")

    last_health_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_health_ok: Mapped[bool | None] = mapped_column(Boolean)

    created_at: Mapped[datetime] = _now_col()
    updated_at: Mapped[datetime] = _now_col(onupdate=func.now())

    secrets: Mapped[list["ConnectorSecret"]] = relationship(
        back_populates="connector", cascade="all, delete-orphan"
    )
    jobs: Mapped[list["JobConnector"]] = relationship(
        back_populates="connector", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('active','revoked','expired')", name="ck_connectors_status"
        ),
        CheckConstraint(
            "kind IN ('slack','github','gmail','mcp_server','http_webhook',"
            "'router','search_endpoint')",
            name="ck_connectors_kind",
        ),
    )


class ConnectorSecret(Base):
    __tablename__ = "connector_secrets"

    id: Mapped[str] = _pk()
    connector_id: Mapped[str] = mapped_column(
        ForeignKey("connectors.id", ondelete="CASCADE"), nullable=False
    )
    field_name: Mapped[str] = mapped_column(String(64), nullable=False)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    key_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = _now_col()

    connector: Mapped["Connector"] = relationship(back_populates="secrets")

    __table_args__ = (
        UniqueConstraint("connector_id", "field_name", name="uq_secret_field"),
    )


class JobConnector(Base):
    __tablename__ = "job_connectors"

    job_id: Mapped[str] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True
    )
    connector_id: Mapped[str] = mapped_column(
        ForeignKey("connectors.id", ondelete="CASCADE"), primary_key=True
    )
    alias: Mapped[str | None] = mapped_column(String(64))

    job: Mapped["Job"] = relationship(back_populates="connectors")
    connector: Mapped["Connector"] = relationship(back_populates="jobs")


# ── Audit log ─────────────────────────────────────────────────────────────────

class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[str] = _pk()
    action: Mapped[str] = mapped_column(String(48), nullable=False)  # 'secret.decrypt'
    connector_id: Mapped[str | None] = mapped_column(String(36))
    run_id: Mapped[str | None] = mapped_column(String(36))
    step_id: Mapped[str | None] = mapped_column(String(36))
    detail: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _now_col()

    __table_args__ = (
        Index("idx_audit_created", "created_at"),
        Index("idx_audit_connector", "connector_id"),
    )
