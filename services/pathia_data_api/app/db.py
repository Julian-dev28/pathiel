"""Persistence layer for the Pathia Data API.

SQLAlchemy 2.x (typed, declarative) persistence for a FastAPI SaaS platform.
Runs on sqlite out of the box and is Postgres-ready: every column type used here
(String, Integer, Boolean, Float, JSON, timezone-aware DateTime) maps cleanly onto
both backends, so switching `PATHIA_DATABASE_URL` to a `postgresql://...` DSN needs
no code change.

Three tables:
  * ApiKey       — tenant credentials (hashed token, plan, scopes, rate limit).
  * UsageRecord  — per-request metering / observability (path, status, latency).
  * SignalCache  — cache of upstream reads keyed by (resource, ticker, as_of).

Import is side-effect free apart from building `engine` / `SessionLocal`; the schema
is created only when `init_db()` is called explicitly.
"""
from __future__ import annotations

import hashlib
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    Integer,
    String,
    create_engine,
    select,
)
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    sessionmaker,
)

from .config import get_settings


# --------------------------------------------------------------------------- #
# Engine + session factory
# --------------------------------------------------------------------------- #
def _make_engine(database_url: str) -> Engine:
    """Build an Engine from a DSN, applying the sqlite-specific connect args.

    `check_same_thread=False` is required because FastAPI serves requests from a
    thread pool and a single sqlite connection would otherwise reject cross-thread
    use. The flag is inert (and correctly skipped) for Postgres and other backends.
    """
    url = make_url(database_url)
    connect_args: dict[str, Any] = {}
    if url.get_backend_name() == "sqlite":
        connect_args["check_same_thread"] = False
    return create_engine(database_url, future=True, connect_args=connect_args)


engine: Engine = _make_engine(get_settings().database_url)
SessionLocal: sessionmaker[Session] = sessionmaker(
    bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
class ApiKey(Base):
    """A tenant API credential. The raw token is never stored — only its
    SHA-256 hex digest (`token_hash`), which is what `auth.hash_token` produces."""

    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key_id: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    plan: Mapped[str] = mapped_column(String(32), nullable=False, default="standard")
    scopes: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    rate_per_min: Mapped[int] = mapped_column(Integer, nullable=False, default=500)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    def as_principal_dict(self) -> dict[str, Any]:
        """The shape `ApiKeyStore.lookup` / `app.auth` expect."""
        return {
            "key_id": self.key_id,
            "plan": self.plan,
            "scopes": list(self.scopes or []),
            "rate_per_min": int(self.rate_per_min),
            "active": bool(self.active),
        }


class UsageRecord(Base):
    """One row per served request — the raw material for metering and dashboards."""

    __tablename__ = "usage_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    path: Mapped[str] = mapped_column(String(255), nullable=False)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True, nullable=False, default=_utcnow
    )
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    latency_ms: Mapped[float] = mapped_column(Float, nullable=False)


class SignalCache(Base):
    """Cached upstream read. One row per (resource, ticker, as_of) snapshot."""

    __tablename__ = "signal_cache"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    resource: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    ticker: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


# --------------------------------------------------------------------------- #
# Schema lifecycle + session helpers
# --------------------------------------------------------------------------- #
def init_db() -> None:
    """Create all tables. Idempotent — `create_all` skips existing tables."""
    Base.metadata.create_all(bind=engine)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional session: commit on success, roll back on error, always close."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Iterator[Session]:
    """FastAPI dependency: yields a session and guarantees it is closed."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


# --------------------------------------------------------------------------- #
# API-key store — the read/seed surface `app.auth` depends on
# --------------------------------------------------------------------------- #
class ApiKeyStore:
    """Data-access object for API keys. Static methods so callers need no state."""

    @staticmethod
    def lookup(token_hash: str) -> Optional[dict[str, Any]]:
        """Resolve a SHA-256 token hash to its principal dict, or None if unknown."""
        with SessionLocal() as session:
            row = session.scalar(select(ApiKey).where(ApiKey.token_hash == token_hash))
            return row.as_principal_dict() if row is not None else None

    @staticmethod
    def seed_demo_key(
        raw_token: str,
        plan: str = "standard",
        scopes: Sequence[str] = ("*",),
        rate_per_min: int = 500,
    ) -> str:
        """Insert an API key for `raw_token`, hashing it the same way `auth.hash_token`
        does (`sha256(raw.encode()).hexdigest()`) so lookups line up. Idempotent: a
        second call with the same token is a no-op. Returns the row's `key_id`.
        """
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        key_id = f"key_{token_hash[:16]}"
        with session_scope() as session:
            existing = session.scalar(select(ApiKey).where(ApiKey.token_hash == token_hash))
            if existing is not None:
                return existing.key_id
            session.add(
                ApiKey(
                    key_id=key_id,
                    token_hash=token_hash,
                    plan=plan,
                    scopes=list(scopes),
                    rate_per_min=int(rate_per_min),
                    active=True,
                )
            )
        return key_id


# --------------------------------------------------------------------------- #
# Metering
# --------------------------------------------------------------------------- #
def record_usage(key_id: str, path: str, status_code: int, latency_ms: float) -> None:
    """Append one usage row. Swallows nothing — a metering failure should surface,
    but it runs in its own transaction so it never poisons the request's session."""
    with session_scope() as session:
        session.add(
            UsageRecord(
                key_id=key_id,
                path=path,
                status_code=int(status_code),
                latency_ms=float(latency_ms),
            )
        )


__all__ = [
    "Base",
    "engine",
    "SessionLocal",
    "ApiKey",
    "UsageRecord",
    "SignalCache",
    "init_db",
    "session_scope",
    "get_db",
    "ApiKeyStore",
    "record_usage",
]
