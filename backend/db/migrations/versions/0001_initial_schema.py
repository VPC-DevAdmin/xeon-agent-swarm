"""initial orchestration schema

Revision ID: 0001
Revises:
Create Date: 2026-04-24

Creates the full Phase 2 entity hierarchy:
  jobs, runs, steps, step_attempts, connectors, connector_secrets,
  job_connectors, audit_log
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── jobs ──────────────────────────────────────────────────────────────
    op.create_table(
        "jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("config", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("schedule_cron", sa.String(120)),
        sa.Column("schedule_tz", sa.String(64), nullable=False, server_default="UTC"),
        sa.Column("overlap_policy", sa.String(16), nullable=False, server_default="skip"),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("next_fire_at", sa.DateTime(timezone=True)),
        sa.Column("last_run_id", sa.String(36)),
        sa.Column("owner", sa.String(120)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("status IN ('active','paused','archived')", name="ck_jobs_status"),
        sa.CheckConstraint("overlap_policy IN ('skip','queue','parallel')", name="ck_jobs_overlap"),
    )
    op.create_index(
        "idx_jobs_active_due", "jobs", ["next_fire_at"],
        postgresql_where=sa.text("status = 'active'"),
    )

    # ── runs ──────────────────────────────────────────────────────────────
    op.create_table(
        "runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("jobs.id", ondelete="SET NULL")),
        sa.Column("trigger", sa.String(16), nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("config", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("task_graph", postgresql.JSONB()),
        sa.Column("document_result", postgresql.JSONB()),
        sa.Column("metrics", postgresql.JSONB()),
        sa.Column("langfuse_trace_id", sa.String(64)),
        sa.Column("error", sa.Text()),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("trigger IN ('schedule','manual','api','retry')", name="ck_runs_trigger"),
        sa.CheckConstraint(
            "status IN ('pending','orchestrating','running','reducing',"
            "'completed','failed','killed')",
            name="ck_runs_status",
        ),
    )
    op.create_index("idx_runs_job_started", "runs", ["job_id", "started_at"])
    op.create_index("idx_runs_status", "runs", ["status"])

    # ── steps ─────────────────────────────────────────────────────────────
    op.create_table(
        "steps",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("step_key", sa.String(32), nullable=False),
        sa.Column("type", sa.String(24), nullable=False),
        sa.Column("objective", sa.Text()),
        sa.Column("scope", sa.Text()),
        sa.Column("success_criteria", postgresql.JSONB()),
        sa.Column("deliverable_format", sa.String(48)),
        sa.Column("source_constraints", postgresql.JSONB()),
        sa.Column("dependencies", postgresql.ARRAY(sa.String(32)), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("result", postgresql.JSONB()),
        sa.Column("confidence", sa.Double()),
        sa.Column("total_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.Double()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("run_id", "step_key", name="uq_steps_run_key"),
        sa.CheckConstraint(
            "status IN ('pending','running','validating','retrying',"
            "'completed','failed','killed','rejected_final')",
            name="ck_steps_status",
        ),
    )
    op.create_index("idx_steps_run", "steps", ["run_id"])

    # ── step_attempts ─────────────────────────────────────────────────────
    op.create_table(
        "step_attempts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("step_id", sa.String(36), sa.ForeignKey("steps.id", ondelete="CASCADE"), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("result", postgresql.JSONB()),
        sa.Column("validator_verdict", postgresql.JSONB()),
        sa.Column("correction_hint", sa.Text()),
        sa.Column("tokens_in", sa.Integer()),
        sa.Column("tokens_out", sa.Integer()),
        sa.Column("model_id", sa.String(120)),
        sa.Column("latency_ms", sa.Double()),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("step_id", "attempt_no", name="uq_attempts_step_no"),
        sa.CheckConstraint(
            "status IN ('completed','failed','validator_rejected')",
            name="ck_attempts_status",
        ),
    )

    # ── connectors ────────────────────────────────────────────────────────
    op.create_table(
        "connectors",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False, unique=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("config", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("last_health_at", sa.DateTime(timezone=True)),
        sa.Column("last_health_ok", sa.Boolean()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint("status IN ('active','revoked','expired')", name="ck_connectors_status"),
        sa.CheckConstraint(
            "kind IN ('slack','github','gmail','mcp_server','http_webhook',"
            "'router','search_endpoint')",
            name="ck_connectors_kind",
        ),
    )

    # ── connector_secrets ─────────────────────────────────────────────────
    op.create_table(
        "connector_secrets",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("connector_id", sa.String(36), sa.ForeignKey("connectors.id", ondelete="CASCADE"), nullable=False),
        sa.Column("field_name", sa.String(64), nullable=False),
        sa.Column("ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("key_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("connector_id", "field_name", name="uq_secret_field"),
    )

    # ── job_connectors ────────────────────────────────────────────────────
    op.create_table(
        "job_connectors",
        sa.Column("job_id", sa.String(36), sa.ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("connector_id", sa.String(36), sa.ForeignKey("connectors.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("alias", sa.String(64)),
    )

    # ── audit_log ─────────────────────────────────────────────────────────
    op.create_table(
        "audit_log",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("action", sa.String(48), nullable=False),
        sa.Column("connector_id", sa.String(36)),
        sa.Column("run_id", sa.String(36)),
        sa.Column("step_id", sa.String(36)),
        sa.Column("detail", postgresql.JSONB()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("idx_audit_created", "audit_log", ["created_at"])
    op.create_index("idx_audit_connector", "audit_log", ["connector_id"])


def downgrade() -> None:
    op.drop_table("audit_log")
    op.drop_table("job_connectors")
    op.drop_table("connector_secrets")
    op.drop_table("connectors")
    op.drop_table("step_attempts")
    op.drop_table("steps")
    op.drop_table("runs")
    op.drop_table("jobs")
