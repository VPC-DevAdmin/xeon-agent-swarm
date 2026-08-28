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


@pytest.fixture(autouse=True)
def _isolate_machine_profile(tmp_path, monkeypatch):
    """The machine profile persists a real characterization to disk. Tests
    must never read or write the operator's file — a cached tier leaking into
    an unrelated test silently changes which deadline it is asserting."""
    from backend.capacity import machine_profile as mp
    monkeypatch.setattr(mp, "PROFILE_PATH", tmp_path / "machine_profiles.json")
