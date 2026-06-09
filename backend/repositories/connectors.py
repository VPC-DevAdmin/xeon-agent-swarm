"""
Connector + secret persistence.

Connectors are tool/credential integrations. Their secret fields are encrypted
at rest (backend/security/secrets.py) and only ever decrypted inside the
backend, with every decryption written to audit_log.
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.db.ids import uuid7_str
from backend.db.models import AuditLog, Connector, ConnectorSecret
from backend.security.secrets import get_cipher

logger = logging.getLogger(__name__)


# ── Connector CRUD ────────────────────────────────────────────────────────────

async def create_connector(
    session: AsyncSession,
    *,
    name: str,
    kind: str,
    config: dict | None = None,
    secrets: dict[str, str] | None = None,
) -> Connector:
    connector = Connector(
        id=uuid7_str(),
        name=name,
        kind=kind,
        config=config or {},
        status="active",
    )
    session.add(connector)
    await session.flush()

    cipher = get_cipher()
    for field, value in (secrets or {}).items():
        ciphertext, version = cipher.encrypt(value)
        session.add(ConnectorSecret(
            id=uuid7_str(),
            connector_id=connector.id,
            field_name=field,
            ciphertext=ciphertext,
            key_version=version,
        ))
    await session.flush()
    return connector


async def get_connector(session: AsyncSession, connector_id: str) -> Connector | None:
    res = await session.execute(
        select(Connector)
        .where(Connector.id == connector_id)
        .options(selectinload(Connector.secrets))
    )
    return res.scalar_one_or_none()


async def get_connector_by_name(session: AsyncSession, name: str) -> Connector | None:
    res = await session.execute(
        select(Connector)
        .where(Connector.name == name)
        .options(selectinload(Connector.secrets))
    )
    return res.scalar_one_or_none()


async def list_connectors(
    session: AsyncSession, *, kind: str | None = None, status: str | None = None
) -> list[Connector]:
    q = select(Connector).order_by(Connector.created_at.desc())
    if kind is not None:
        q = q.where(Connector.kind == kind)
    if status is not None:
        q = q.where(Connector.status == status)
    res = await session.execute(q)
    return list(res.scalars().all())


async def update_connector_config(
    session: AsyncSession, connector_id: str, *, config: dict | None = None,
    status: str | None = None,
) -> Connector | None:
    connector = await session.get(Connector, connector_id)
    if connector is None:
        return None
    if config is not None:
        connector.config = config
    if status is not None:
        connector.status = status
    await session.flush()
    return connector


async def set_secret(
    session: AsyncSession, connector_id: str, field_name: str, value: str
) -> None:
    """Create or replace one encrypted secret field."""
    cipher = get_cipher()
    ciphertext, version = cipher.encrypt(value)
    res = await session.execute(
        select(ConnectorSecret).where(
            ConnectorSecret.connector_id == connector_id,
            ConnectorSecret.field_name == field_name,
        )
    )
    existing = res.scalar_one_or_none()
    if existing:
        existing.ciphertext = ciphertext
        existing.key_version = version
    else:
        session.add(ConnectorSecret(
            id=uuid7_str(),
            connector_id=connector_id,
            field_name=field_name,
            ciphertext=ciphertext,
            key_version=version,
        ))
    await session.flush()


async def revoke_connector(session: AsyncSession, connector_id: str) -> Connector | None:
    connector = await session.get(Connector, connector_id)
    if connector is None:
        return None
    connector.status = "revoked"
    await session.flush()
    return connector


# ── Secret decryption (audit-logged) ──────────────────────────────────────────

async def resolve_secrets(
    session: AsyncSession,
    connector_id: str,
    *,
    run_id: str | None = None,
    step_id: str | None = None,
) -> dict[str, str]:
    """
    Decrypt all secret fields for a connector. EVERY call writes an audit_log
    row. Use only inside the backend at the moment a tool call needs the creds —
    never expose the result over the API.
    """
    res = await session.execute(
        select(ConnectorSecret).where(ConnectorSecret.connector_id == connector_id)
    )
    rows = list(res.scalars().all())
    cipher = get_cipher()
    out: dict[str, str] = {}
    for row in rows:
        out[row.field_name] = cipher.decrypt(row.ciphertext)

    session.add(AuditLog(
        id=uuid7_str(),
        action="secret.decrypt",
        connector_id=connector_id,
        run_id=run_id,
        step_id=step_id,
        detail={"fields": [r.field_name for r in rows]},
    ))
    await session.flush()
    logger.info(
        "Decrypted %d secret field(s) for connector %s (run=%s step=%s)",
        len(rows), connector_id, run_id, step_id,
    )
    return out


def secret_field_names(connector: Connector) -> list[str]:
    """Field names only — safe to expose over the API (no values)."""
    return sorted(s.field_name for s in connector.secrets)
