"""Optional Tiger Data / PostgreSQL. App starts even when DATABASE_URL is unset."""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from typing import Generator, Optional

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

logger = logging.getLogger(__name__)

_engine: Optional[Engine] = None
_SessionLocal: Optional[sessionmaker] = None
db_available: bool = False


def init_db() -> bool:
    """Initialize engine/tables when DATABASE_URL is set. Never raises to crash the app."""
    global _engine, _SessionLocal, db_available
    url = (os.getenv("DATABASE_URL") or "").strip()
    if not url:
        logger.info("DATABASE_URL not set — using in-memory run state only")
        db_available = False
        return False

    # Accept plain postgresql:// and normalize for SQLAlchemy + psycopg3
    if url.startswith("postgres://"):
        url = "postgresql+psycopg://" + url[len("postgres://") :]
    elif url.startswith("postgresql://") and "+psycopg" not in url:
        url = "postgresql+psycopg://" + url[len("postgresql://") :]

    try:
        _engine = create_engine(url, pool_pre_ping=True, pool_size=3, max_overflow=2)
        _SessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False)
        from app.db_models import Base

        Base.metadata.create_all(_engine)
        _maybe_create_hypertable(_engine)
        db_available = True
        logger.info("PostgreSQL connected and tables ensured")
        return True
    except Exception as exc:
        logger.warning("Database init failed — continuing without persistence: %s", exc)
        _engine = None
        _SessionLocal = None
        db_available = False
        return False


def _maybe_create_hypertable(engine: Engine) -> None:
    try:
        with engine.begin() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE"))
            conn.execute(
                text(
                    "SELECT create_hypertable('task_events', 'timestamp', "
                    "if_not_exists => TRUE, migrate_data => TRUE)"
                )
            )
        logger.info("task_events hypertable ready (or already existed)")
    except Exception as exc:
        logger.info("Hypertable setup skipped: %s", exc)


@contextmanager
def session_scope() -> Generator[Optional[Session], None, None]:
    if not db_available or _SessionLocal is None:
        yield None
        return
    session = _SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_engine() -> Optional[Engine]:
    return _engine
