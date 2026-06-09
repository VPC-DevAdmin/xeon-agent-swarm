"""
Programmatic Alembic migration runner.

Called on backend startup when RUN_MIGRATIONS=1 (see backend/main.py lifespan).
Equivalent to `alembic upgrade head` but invokable in-process so we don't need
a separate migration container or entrypoint script for the demo.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from alembic import command
from alembic.config import Config

logger = logging.getLogger(__name__)

# repo root = three levels up from this file (backend/db/migrate.py)
_REPO_ROOT = Path(__file__).resolve().parents[2]


def _alembic_config() -> Config:
    cfg = Config(str(_REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_REPO_ROOT / "backend" / "db" / "migrations"))
    return cfg


def upgrade_to_head() -> None:
    """Run `alembic upgrade head`. Idempotent — safe to call every startup."""
    if os.getenv("RUN_MIGRATIONS", "1").lower() not in ("1", "true", "yes"):
        logger.info("RUN_MIGRATIONS disabled — skipping migrations")
        return
    logger.info("Running database migrations (upgrade head)…")
    command.upgrade(_alembic_config(), "head")
    logger.info("Database migrations complete")
