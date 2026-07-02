"""Database engine and session management.

Production uses MySQL via ``DATABASE_URL=mysql+pymysql://...``. Tests and local
development may use SQLite through the same SQLAlchemy ORM models.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import create_engine, event, inspect
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from models import Base
from numbering import NumberSequencer
from numbering import get_number_sequencer as _make_sequencer

DEFAULT_SQLITE_URL = "sqlite+pysqlite:///data/orders.db"


def _legacy_sqlite_url() -> str:
    legacy_db_path = os.getenv("DB_PATH")
    if legacy_db_path:
        return f"sqlite+pysqlite:///{legacy_db_path}"
    return DEFAULT_SQLITE_URL


DATABASE_URL = os.getenv("DATABASE_URL") or _legacy_sqlite_url()

_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def get_database_url() -> str:
    return os.getenv("DATABASE_URL") or _legacy_sqlite_url()


def get_engine() -> Engine:
    global _engine, _SessionLocal, DATABASE_URL
    url = get_database_url()
    if _engine is None or url != DATABASE_URL:
        DATABASE_URL = url
        kwargs: dict[str, Any] = {"future": True, "pool_pre_ping": True}
        if url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False}
            _engine = create_engine(url, **kwargs)

            @event.listens_for(_engine, "connect")
            def _set_sqlite_pragma(dbapi_connection, _connection_record):
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA busy_timeout=5000")
                cursor.execute("PRAGMA synchronous=NORMAL")
                cursor.close()
        else:
            kwargs["pool_size"] = int(os.getenv("DB_POOL_SIZE", "10"))
            kwargs["max_overflow"] = int(os.getenv("DB_MAX_OVERFLOW", "20"))
            kwargs["pool_recycle"] = int(os.getenv("DB_POOL_RECYCLE", "3600"))
            kwargs["pool_timeout"] = int(os.getenv("DB_POOL_TIMEOUT", "30"))
            _engine = create_engine(url, **kwargs)
        _SessionLocal = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False, future=True)
    return _engine


def get_session() -> Session:
    if _SessionLocal is None:
        get_engine()
    assert _SessionLocal is not None
    return _SessionLocal()


def get_number_sequencer() -> NumberSequencer:
    """Return the appropriate number sequencer based on the current DATABASE_URL."""
    return _make_sequencer(get_database_url())


@contextmanager
def session_scope() -> Iterator[Session]:
    session = get_session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db() -> None:
    Base.metadata.create_all(get_engine())


def drop_db() -> None:
    Base.metadata.drop_all(get_engine())


def reset_engine_for_tests(database_url: str | None = None) -> None:
    """Reset cached engine/session. Tests use this after changing DATABASE_URL."""
    global _engine, _SessionLocal, DATABASE_URL
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None
    if database_url is not None:
        os.environ["DATABASE_URL"] = database_url
    DATABASE_URL = get_database_url()


def is_db_empty() -> bool:
    engine = get_engine()
    inspector = inspect(engine)
    if "orders" not in inspector.get_table_names():
        return True
    with get_session() as session:
        from models import Order

        return session.query(Order).count() == 0


# Backwards-compatible alias for new code that still imports get_db.
def get_db() -> Session:
    return get_session()
