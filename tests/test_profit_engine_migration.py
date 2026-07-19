"""Idempotency tests for the cs-profit-engine Alembic migration (0005).

Uses a shared-cache in-memory SQLite database so no files are written to disk.
A sentinel connection is held open throughout each test to keep the in-memory
database alive across Alembic's NullPool connect/disconnect cycles inside
``alembic/env.py``.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

ROOT = Path(__file__).resolve().parents[1]

# Nine tables added by migration 0005_profit_engine_schema.
PROFIT_ENGINE_TABLES = (
    "user_profile",
    "user_identity",
    "user_intent_tag",
    "user_value_score",
    "recommendation",
    "funnel_event",
    "touch_point",
    "attribution_record",
    "agent_assist_event",
)

# Shared-cache in-memory SQLite URI. ``mode=memory`` keeps the database in RAM
# (no file), ``cache=shared`` lets multiple connections reach the same DB, and
# ``uri=true`` tells pysqlite to interpret the path as a URI.
IN_MEMORY_URL = "sqlite+pysqlite:///file:profit_engine_test?mode=memory&cache=shared&uri=true"


class ProfitEngineMigrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self._prev_db_url = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = IN_MEMORY_URL
        # Persistent connection keeps the shared in-memory DB alive while
        # Alembic opens and closes its own connections.
        self._sentinel_engine = create_engine(IN_MEMORY_URL)
        self._sentinel_conn = self._sentinel_engine.connect()
        self.cfg = Config(str(ROOT / "alembic.ini"))
        # customers / orders / sequence_counters must exist before 0005 because
        # the new tables have foreign keys targeting them.
        command.upgrade(self.cfg, "0004_sequence_counters")

    def tearDown(self) -> None:
        self._sentinel_conn.close()
        self._sentinel_engine.dispose()
        if self._prev_db_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = self._prev_db_url

    def _table_names(self) -> set[str]:
        engine = create_engine(IN_MEMORY_URL)
        try:
            return set(inspect(engine).get_table_names())
        finally:
            engine.dispose()

    def test_upgrade_creates_all_profit_engine_tables(self) -> None:
        command.upgrade(self.cfg, "0005_profit_engine_schema")
        tables = self._table_names()
        for table in PROFIT_ENGINE_TABLES:
            self.assertIn(table, tables, f"missing table after upgrade: {table}")

    def test_downgrade_removes_all_profit_engine_tables(self) -> None:
        command.upgrade(self.cfg, "0005_profit_engine_schema")
        command.downgrade(self.cfg, "0004_sequence_counters")
        tables = self._table_names()
        for table in PROFIT_ENGINE_TABLES:
            self.assertNotIn(table, tables, f"table still present after downgrade: {table}")

    def test_repeated_upgrade_is_idempotent(self) -> None:
        # Upgrade -> downgrade -> upgrade must not raise.
        command.upgrade(self.cfg, "0005_profit_engine_schema")
        command.downgrade(self.cfg, "0004_sequence_counters")
        command.upgrade(self.cfg, "0005_profit_engine_schema")
        tables = self._table_names()
        for table in PROFIT_ENGINE_TABLES:
            self.assertIn(table, tables, f"missing table after re-upgrade: {table}")

    def test_existing_tables_preserved_after_upgrade(self) -> None:
        # The migration must not touch pre-existing tables.
        command.upgrade(self.cfg, "0005_profit_engine_schema")
        tables = self._table_names()
        for required in ("customers", "orders", "sequence_counters"):
            self.assertIn(required, tables, f"pre-existing table missing after upgrade: {required}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
