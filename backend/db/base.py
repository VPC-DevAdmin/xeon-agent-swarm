"""
Async SQLAlchemy engine, session factory, and declarative base.

Backed by SQLite (aiosqlite) — a single file, no database server. The path comes
from DATABASE_URL, default:
  sqlite+aiosqlite:///./data/orchestrator.db

Schema is created on startup with create_all() (no migration tooling). For a
schema change, delete the .db file and let it recreate — this is a status store,
not a system of record.

The engine is created lazily so importing this module doesn't require a live DB.
"""
from __future__ import annotations

import os

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase


DEFAULT_DATABASE_URL = "sqlite+aiosqlite:///./data/orchestrator.db"


def database_url() -> str:
    return os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def _ensure_sqlite_dir(url: str) -> None:
    """Create the parent directory of a SQLite file URL if it doesn't exist, so
    the first connection doesn't fail with 'unable to open database file'."""
    marker = ":///"
    if marker not in url:
        return
    path = url.split(marker, 1)[1]
    # strip a leading slash that belongs to an absolute path form (////abs)
    if path.startswith("/"):
        fs_path = path
    else:
        fs_path = path  # relative to cwd
    directory = os.path.dirname(fs_path)
    if directory:
        os.makedirs(directory, exist_ok=True)


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        url = database_url()
        echo = os.getenv("SQL_ECHO", "").lower() in ("1", "true")
        if url.startswith("sqlite"):
            _ensure_sqlite_dir(url)
            # SQLite uses a single connection model; the server-grade pool args
            # don't apply. check_same_thread=False lets the async pool hand the
            # connection across the event-loop's threads safely.
            _engine = create_async_engine(
                url, echo=echo,
                connect_args={"check_same_thread": False},
            )
            # WAL: readers stop blocking the writer and commits get cheaper —
            # the single-writer rollback journal was the measured ceiling on
            # concurrent agent workflows (agent-host capacity certified at 9
            # sessions with the CPU at 2%). busy_timeout absorbs the residual
            # writer-vs-writer collisions instead of raising SQLITE_BUSY.
            from sqlalchemy import event

            @event.listens_for(_engine.sync_engine, "connect")
            def _sqlite_pragmas(dbapi_conn, _record):
                cur = dbapi_conn.cursor()
                cur.execute("PRAGMA journal_mode=WAL")
                cur.execute("PRAGMA synchronous=NORMAL")
                cur.execute("PRAGMA busy_timeout=5000")
                cur.close()
        else:
            _engine = create_async_engine(
                url, echo=echo, pool_pre_ping=True,
                pool_size=int(os.getenv("DB_POOL_SIZE", "10")),
                max_overflow=int(os.getenv("DB_MAX_OVERFLOW", "20")),
            )
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(
            bind=get_engine(),
            expire_on_commit=False,
            class_=AsyncSession,
        )
    return _sessionmaker


async def get_session() -> AsyncSession:
    """FastAPI dependency: yields a session, commits on success, rolls back on error."""
    sm = get_sessionmaker()
    async with sm() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def create_schema() -> None:
    """Create all tables if they don't exist (replaces Alembic for the SQLite
    status store). Idempotent — safe to call on every startup."""
    import backend.db.models  # noqa: F401 — register models on Base.metadata
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def dispose_engine() -> None:
    """Close all pooled connections (call on app shutdown). Also drops the
    cached sessionmaker — it is bound to the disposed engine, and keeping it
    would silently pin the OLD DATABASE_URL (bit a test fixture first)."""
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
        _engine = None
    _sessionmaker = None
