"""Database engine and session management.

Production uses MySQL via ``DATABASE_URL=mysql+pymysql://...``. Tests and local
development may use SQLite through the same SQLAlchemy ORM models.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from models import Base


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
        connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
        _engine = create_engine(url, future=True, pool_pre_ping=True, connect_args=connect_args)
        _SessionLocal = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False, future=True)
    return _engine


def get_session() -> Session:
    if _SessionLocal is None:
        get_engine()
    assert _SessionLocal is not None
    return _SessionLocal()


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
