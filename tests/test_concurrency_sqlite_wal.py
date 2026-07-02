"""Concurrency tests for SQLite WAL mode and concurrent write/read safety.

Verifies that WAL mode is enabled and that concurrent reads/writes do not
trigger SQLITE_BUSY or deadlocks.
"""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import database
import seed_data
import service_layer as svc
from models import Order
from numbering import reset_for_tests
from orchestrator_runtime import reset_conversation_states_for_tests
from security import Actor, load_verification, request_otp, verify_otp


class ConcurrencySqliteWalTest(unittest.TestCase):
    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(prefix="conc-wal-", suffix=".db")
        os.close(fd)
        os.environ["DATABASE_URL"] = "sqlite+pysqlite:///" + self.db_path.replace("\\", "/")
        os.environ["AUTH_DEV_SECRET"] = "customer-service-test-secret-min-32-bytes"
        database.reset_engine_for_tests()
        reset_for_tests()
        reset_conversation_states_for_tests()
        database.init_db()
        session = database.get_session()
        try:
            seed_data.seed(session)
        finally:
            session.close()
        self.order_id, self.customer_id = self._first_order()
        self.verification = self._load_verification()

    def tearDown(self) -> None:
        database.reset_engine_for_tests("sqlite+pysqlite:///:memory:")
        for suffix in ("", "-wal", "-shm"):
            path = f"{self.db_path}{suffix}"
            if os.path.exists(path):
                os.remove(path)

    def _first_order(self) -> tuple[str, int]:
        session = database.get_session()
        try:
            order = session.query(Order).filter_by(status="delivered").order_by(Order.created_at.desc()).first()
            assert order is not None
            challenge = request_otp(
                session, "customer_identity", "email", "test@example.com", order.customer_id, order.id
            )
            verify_otp(session, challenge["challenge_id"], challenge["dev_code"])
            session.commit()
            return order.id, order.customer_id
        finally:
            session.close()

    def _load_verification(self):
        session = database.get_session()
        try:
            challenge = request_otp(
                session, "customer_identity", "email", "test@example.com", self.customer_id, self.order_id
            )
            verified = verify_otp(session, challenge["challenge_id"], challenge["dev_code"])
            session.commit()
            return load_verification(session, verified["verification_token"])
        finally:
            session.close()

    def test_wal_mode_enabled_on_test_database(self):
        """The test database must have journal_mode=wal."""
        from sqlalchemy import text

        with database.session_scope() as session:
            result = session.execute(text("PRAGMA journal_mode")).scalar()
        self.assertEqual(result.lower(), "wal")

    def test_concurrent_writes_no_database_locked(self):
        """8 threads x 10 writes; no SQLITE_BUSY / database is locked."""
        errors: list[Exception] = []

        def writer(thread_id: int) -> int:
            count = 0
            for i in range(10):
                try:
                    with database.session_scope() as session:
                        svc.create_ticket(
                            session,
                            Actor(f"w-{thread_id}", "work_order", {}),
                            f"WAL test {thread_id}-{i}",
                            "concurrent write test",
                            "incident",
                            "P3",
                            self.customer_id,
                            self.order_id,
                            self.verification,
                            f"wal-{thread_id}-{i}",
                        )
                        session.commit()
                    count += 1
                except Exception as exc:
                    errors.append(exc)
            return count

        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(writer, i) for i in range(8)]
            counts = [f.result() for f in as_completed(futures)]

        self.assertEqual(errors, [])
        self.assertEqual(sum(counts), 80)

    def test_concurrent_read_during_write_not_blocked(self):
        """Reads must not be blocked by concurrent writes (WAL property)."""
        stop = threading.Event()
        read_errors: list[Exception] = []

        def continuous_write():
            i = 0
            while not stop.is_set():
                try:
                    with database.session_scope() as session:
                        svc.create_ticket(
                            session,
                            Actor("writer", "work_order", {}),
                            f"Read-during-write {i}",
                            "test",
                            "incident",
                            "P3",
                            self.customer_id,
                            self.order_id,
                            self.verification,
                            f"rdw-{i}",
                        )
                        session.commit()
                    i += 1
                except Exception:
                    pass

        def continuous_read():
            try:
                while not stop.is_set():
                    with database.session_scope() as session:
                        session.query(Order).limit(5).all()
            except Exception as exc:
                read_errors.append(exc)

        write_thread = threading.Thread(target=continuous_write)
        read_threads = [threading.Thread(target=continuous_read) for _ in range(4)]

        write_thread.start()
        for t in read_threads:
            t.start()

        time.sleep(2)
        stop.set()

        write_thread.join(timeout=5)
        for t in read_threads:
            t.join(timeout=5)

        self.assertEqual(read_errors, [])

    def test_wal_checkpoint_safe_under_load(self):
        """Sustained writes trigger WAL checkpoint; no deadlock."""
        errors: list[Exception] = []

        def writer(idx: int) -> int:
            count = 0
            for i in range(20):
                try:
                    with database.session_scope() as session:
                        svc.create_ticket(
                            session,
                            Actor(f"ckpt-{idx}", "work_order", {}),
                            f"Checkpoint {idx}-{i}",
                            "checkpoint test",
                            "incident",
                            "P3",
                            self.customer_id,
                            self.order_id,
                            self.verification,
                            f"ckpt-{idx}-{i}",
                        )
                        session.commit()
                    count += 1
                except Exception as exc:
                    errors.append(exc)
            return count

        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(writer, i) for i in range(4)]
            counts = [f.result() for f in as_completed(futures)]

        self.assertEqual(errors, [])
        self.assertEqual(sum(counts), 80)


if __name__ == "__main__":
    unittest.main()
