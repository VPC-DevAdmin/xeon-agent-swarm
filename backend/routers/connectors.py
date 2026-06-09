"""
/connectors — tool/credential integrations.

Security: request bodies may CARRY secret values (encrypted on save), but
responses NEVER include them — only the names of the configured secret fields.
See docs/standards.md §3 and backend/security/secrets.py.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.base import get_session
from backend.repositories import connectors as conn_repo
from backend.schemas.api import (
    ConnectorCreate,
    ConnectorOut,
    ConnectorUpdate,
    SecretSet,
)
from backend.security.secrets import EncryptionError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/connectors", tags=["connectors"])


def _out(connector) -> ConnectorOut:
    return ConnectorOut.from_orm_connector(
        connector, conn_repo.secret_field_names(connector)
    )


@router.post("", response_model=ConnectorOut)
async def create_connector(
    body: ConnectorCreate, session: AsyncSession = Depends(get_session)
):
    existing = await conn_repo.get_connector_by_name(session, body.name)
    if existing:
        raise HTTPException(409, f"connector '{body.name}' already exists")
    try:
        connector = await conn_repo.create_connector(
            session, name=body.name, kind=body.kind,
            config=body.config, secrets=body.secrets,
        )
    except EncryptionError as exc:
        raise HTTPException(500, f"encryption not configured: {exc}")
    connector = await conn_repo.get_connector(session, connector.id)
    return _out(connector)


@router.get("", response_model=list[ConnectorOut])
async def list_connectors(
    kind: str | None = Query(None),
    status: str | None = Query(None),
    session: AsyncSession = Depends(get_session),
):
    connectors = await conn_repo.list_connectors(session, kind=kind, status=status)
    # Eager-load secrets per connector for field-name exposure.
    out = []
    for c in connectors:
        full = await conn_repo.get_connector(session, c.id)
        out.append(_out(full))
    return out


@router.get("/{connector_id}", response_model=ConnectorOut)
async def get_connector(connector_id: str, session: AsyncSession = Depends(get_session)):
    connector = await conn_repo.get_connector(session, connector_id)
    if connector is None:
        raise HTTPException(404, "connector not found")
    return _out(connector)


@router.patch("/{connector_id}", response_model=ConnectorOut)
async def update_connector(
    connector_id: str, body: ConnectorUpdate,
    session: AsyncSession = Depends(get_session),
):
    connector = await conn_repo.update_connector_config(
        session, connector_id, config=body.config, status=body.status,
    )
    if connector is None:
        raise HTTPException(404, "connector not found")
    connector = await conn_repo.get_connector(session, connector_id)
    return _out(connector)


@router.put("/{connector_id}/secrets/{field}", response_model=ConnectorOut)
async def set_secret(
    connector_id: str, field: str, body: SecretSet,
    session: AsyncSession = Depends(get_session),
):
    connector = await conn_repo.get_connector(session, connector_id)
    if connector is None:
        raise HTTPException(404, "connector not found")
    try:
        await conn_repo.set_secret(session, connector_id, field, body.value)
    except EncryptionError as exc:
        raise HTTPException(500, f"encryption not configured: {exc}")
    connector = await conn_repo.get_connector(session, connector_id)
    return _out(connector)


@router.delete("/{connector_id}", response_model=ConnectorOut)
async def revoke_connector(connector_id: str, session: AsyncSession = Depends(get_session)):
    connector = await conn_repo.revoke_connector(session, connector_id)
    if connector is None:
        raise HTTPException(404, "connector not found")
    connector = await conn_repo.get_connector(session, connector_id)
    return _out(connector)
