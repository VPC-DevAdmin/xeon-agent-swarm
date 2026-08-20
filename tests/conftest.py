"""Shared test guards.

Capacity unit tests must never write benchmark history into a developer's real
orchestrator.db — DB persistence is off by default in tests; the dedicated
persistence test re-enables it against a throwaway database.
"""
import pytest


@pytest.fixture(autouse=True)
def _no_capacity_db_persist(monkeypatch):
    from backend.capacity import controller as ctl
    monkeypatch.setattr(ctl, "PERSIST_TO_DB", False)
