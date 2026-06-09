"""
HTTP request/response DTOs for the jobs / runs / connectors REST surface.

Secrets are NEVER serialized — connector responses expose only the names of
the secret fields, never their values (rule 3). See docs/standards.md §3.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


# ── Jobs ──────────────────────────────────────────────────────────────────────

class JobCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    query: str = Field(..., min_length=1, max_length=10_000)
    description: Optional[str] = None
    config: dict = Field(default_factory=dict)
    schedule_cron: Optional[str] = None       # None = on-demand only
    schedule_tz: str = "UTC"
    overlap_policy: Literal["skip", "queue", "parallel"] = "skip"
    owner: Optional[str] = None
    connector_ids: list[str] = Field(default_factory=list)


class JobUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    query: Optional[str] = None
    config: Optional[dict] = None
    # Use the string "__unset__" sentinel handling in the router; here None is a
    # legal value meaning "clear the schedule".
    schedule_cron: Optional[str] = None
    clear_schedule: bool = False
    schedule_tz: Optional[str] = None
    overlap_policy: Optional[Literal["skip", "queue", "parallel"]] = None


class JobOut(BaseModel):
    id: str
    name: str
    description: Optional[str]
    query: str
    config: dict
    schedule_cron: Optional[str]
    schedule_tz: str
    overlap_policy: str
    status: str
    next_fire_at: Optional[datetime]
    last_run_id: Optional[str]
    owner: Optional[str]
    created_at: datetime
    updated_at: datetime
    connector_ids: list[str] = Field(default_factory=list)

    @classmethod
    def from_orm_job(cls, job: Any) -> "JobOut":
        return cls(
            id=job.id,
            name=job.name,
            description=job.description,
            query=job.query,
            config=job.config,
            schedule_cron=job.schedule_cron,
            schedule_tz=job.schedule_tz,
            overlap_policy=job.overlap_policy,
            status=job.status,
            next_fire_at=job.next_fire_at,
            last_run_id=job.last_run_id,
            owner=job.owner,
            created_at=job.created_at,
            updated_at=job.updated_at,
            connector_ids=[jc.connector_id for jc in getattr(job, "connectors", [])],
        )


# ── Runs ──────────────────────────────────────────────────────────────────────

class RunSummary(BaseModel):
    id: str
    job_id: Optional[str]
    trigger: str
    status: str
    query: str
    started_at: datetime
    completed_at: Optional[datetime]

    @classmethod
    def from_orm_run(cls, run: Any) -> "RunSummary":
        return cls(
            id=run.id,
            job_id=run.job_id,
            trigger=run.trigger,
            status=run.status,
            query=run.query,
            started_at=run.started_at,
            completed_at=run.completed_at,
        )


# ── Connectors ────────────────────────────────────────────────────────────────

class ConnectorCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    kind: Literal[
        "slack", "github", "gmail", "mcp_server",
        "http_webhook", "router", "search_endpoint",
    ]
    config: dict = Field(default_factory=dict)
    # Secret field values — encrypted on save, never echoed back.
    secrets: dict[str, str] = Field(default_factory=dict)


class ConnectorUpdate(BaseModel):
    config: Optional[dict] = None
    status: Optional[Literal["active", "revoked", "expired"]] = None


class SecretSet(BaseModel):
    value: str = Field(..., min_length=1)


class ConnectorOut(BaseModel):
    id: str
    name: str
    kind: str
    config: dict
    status: str
    last_health_at: Optional[datetime]
    last_health_ok: Optional[bool]
    created_at: datetime
    updated_at: datetime
    # Only the NAMES of secret fields — never the values.
    secret_fields: list[str] = Field(default_factory=list)

    @classmethod
    def from_orm_connector(cls, c: Any, secret_fields: list[str]) -> "ConnectorOut":
        return cls(
            id=c.id,
            name=c.name,
            kind=c.kind,
            config=c.config,
            status=c.status,
            last_health_at=c.last_health_at,
            last_health_ok=c.last_health_ok,
            created_at=c.created_at,
            updated_at=c.updated_at,
            secret_fields=secret_fields,
        )
