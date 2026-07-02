"""Number sequence generation with dialect-aware adapters.

SQLite/development uses an in-process lock + DB query (single-process safe).
MySQL/production uses a counter table with ``LAST_INSERT_ID`` atomic increment
(connection-isolated, multi-process safe without explicit locks).
"""

from __future__ import annotations

import threading
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

_LOCKS: dict[str, threading.Lock] = {
    "ticket": threading.Lock(),
    "return": threading.Lock(),
    "survey": threading.Lock(),
}
_LOCAL_SEQ: dict[str, int] = {}


class NumberSequencer:
    """Unified number generation interface."""

    def next_number(self, session: Session, column: Any, prefix_name: str, lock_key: str) -> str:
        raise NotImplementedError


class InProcessSequencer(NumberSequencer):
    """SQLite / tests: in-process lock + DB max query (single-process safe)."""

    def next_number(self, session: Session, column: Any, prefix_name: str, lock_key: str) -> str:
        with _LOCKS[lock_key]:
            today = datetime.now().strftime("%Y%m%d")
            prefix = f"{prefix_name}-{today}-"
            row = session.query(column).filter(column.like(f"{prefix}%")).order_by(column.desc()).first()
            db_seq = int(row[0].rsplit("-", 1)[-1]) if row else 0
            seq_key = f"{prefix_name}-{today}"
            seq = max(db_seq, _LOCAL_SEQ.get(seq_key, 0)) + 1
            _LOCAL_SEQ[seq_key] = seq
            return f"{prefix}{seq:03d}"


class MysqlCounterSequencer(NumberSequencer):
    """Production MySQL: counter table + LAST_INSERT_ID atomic increment.

    ``LAST_INSERT_ID(expr)`` writes the value to the connection's session state,
    so each concurrent connection retrieves its own independently-incremented
    value without row locks or race conditions.
    """

    def next_number(self, session: Session, column: Any, prefix_name: str, lock_key: str) -> str:
        today = datetime.now().strftime("%Y%m%d")
        prefix = f"{prefix_name}-{today}-"
        session.execute(
            text(
                "INSERT INTO sequence_counters (prefix_name, counter_date, last_value) "
                "VALUES (:p, :d, 1) "
                "ON DUPLICATE KEY UPDATE last_value = LAST_INSERT_ID(last_value + 1)"
            ),
            {"p": prefix_name, "d": today},
        )
        seq = session.execute(text("SELECT LAST_INSERT_ID()")).scalar()
        return f"{prefix}{seq:03d}"


def get_number_sequencer(database_url: str) -> NumberSequencer:
    """Factory: return the appropriate sequencer based on the database URL scheme."""
    if database_url.startswith("mysql"):
        return MysqlCounterSequencer()
    return InProcessSequencer()


def reset_for_tests() -> None:
    """Clear in-process sequence state between tests."""
    _LOCAL_SEQ.clear()
