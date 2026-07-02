"""Concurrency tests for idempotency key deduplication under race conditions.

Verifies that concurrent requests with the same Idempotency-Key are deduplicated
correctly via the IntegrityError fallback path.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import database
import seed_data
import service_layer as svc
from models import Order, Ticket
from numbering import reset_for_tests
from orchestrator_runtime import reset_conversation_states_for_tests
from security import Actor, load_verification, request_otp, run_idempotent, verify_otp


class ConcurrencyIdempotencyTest(unittest.TestCase):
    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(prefix="conc-idem-", suffix=".db")
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

    def test_concurrent_duplicate_idempotency_key_dedupes(self):
        """8 threads with same key+payload; only 1 ticket created, all get cached response."""
        key = "shared-idempotency-key-001"
        actor = Actor("test", "work_order", {})

        def worker(_idx: int) -> dict:
            with database.session_scope() as session:
                result, status_code, cached = run_idempotent(
                    session,
                    actor,
                    "create_ticket",
                    key,
                    {"title": "Duplicated ticket"},
                    lambda: (
                        svc.create_ticket(
                            session,
                            actor,
                            "Duplicated ticket",
                            "concurrent idempotency test",
                            "incident",
                            "P3",
                            self.customer_id,
                            self.order_id,
                            self.verification,
                            None,
                        ),
                        201,
                    ),
                )
                session.commit()
                return {"status_code": status_code, "cached": cached, "result": result}

        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(worker, i) for i in range(8)]
            results = [f.result() for f in as_completed(futures)]

        self.assertEqual(len(results), 8)
        # Exactly one should be non-cached (the winner), rest should be cached
        non_cached = [r for r in results if not r["cached"]]
        cached = [r for r in results if r["cached"]]
        self.assertEqual(len(non_cached), 1)
        self.assertGreaterEqual(len(cached), 7)

        # Only 1 ticket in DB
        with database.session_scope() as session:
            count = session.query(Ticket).filter_by(title="Duplicated ticket").count()
        self.assertEqual(count, 1)

    def test_concurrent_same_key_different_payload_raises_conflict(self):
        """Same key but different payload; second request after first commits should get 409."""
        key = "conflict-key-001"
        actor = Actor("test", "work_order", {})
        payload_a = {"title": "Payload A"}
        payload_b = {"title": "Payload B"}

        # First request commits successfully
        with database.session_scope() as session:
            run_idempotent(
                session,
                actor,
                "create_ticket",
                key,
                payload_a,
                lambda: (
                    svc.create_ticket(
                        session,
                        actor,
                        "Payload A",
                        "conflict test",
                        "incident",
                        "P3",
                        self.customer_id,
                        self.order_id,
                        self.verification,
                        None,
                    ),
                    201,
                ),
            )
            session.commit()

        # Second request with same key but different payload should get 409
        with database.session_scope() as session:
            with self.assertRaises(Exception) as cm:
                run_idempotent(
                    session,
                    actor,
                    "create_ticket",
                    key,
                    payload_b,
                    lambda: (
                        svc.create_ticket(
                            session,
                            actor,
                            "Payload B",
                            "conflict test",
                            "incident",
                            "P3",
                            self.customer_id,
                            self.order_id,
                            self.verification,
                            None,
                        ),
                        201,
                    ),
                )
        self.assertIn("409", str(cm.exception))

    def test_idempotency_replay_after_business_write_rollback(self):
        """Concurrent loser's business writes roll back; DB has exactly 1 record."""
        key = "rollback-key-001"
        actor = Actor("test", "work_order", {})
        payload = {"title": "Rollback test"}

        def worker(_idx: int) -> bool:
            with database.session_scope() as session:
                try:
                    run_idempotent(
                        session,
                        actor,
                        "create_ticket",
                        key,
                        payload,
                        lambda: (
                            svc.create_ticket(
                                session,
                                actor,
                                "Rollback test",
                                "rollback test",
                                "incident",
                                "P3",
                                self.customer_id,
                                self.order_id,
                                self.verification,
                                None,
                            ),
                            201,
                        ),
                    )
                    session.commit()
                    return True
                except Exception:
                    session.rollback()
                    return False

        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = [pool.submit(worker, i) for i in range(6)]
            outcomes = [f.result() for f in as_completed(futures)]

        # At least one succeeded
        self.assertTrue(any(outcomes))
        # DB should have exactly 1 ticket with this title
        with database.session_scope() as session:
            count = session.query(Ticket).filter_by(title="Rollback test").count()
        self.assertEqual(count, 1)

    def test_orchestrator_write_fanout_idempotency_keys_no_collision(self):
        """Fan-out (survey + ticket) with same caller key; derived keys don't collide."""
        from orchestrator_api import respond_to_customer_message

        key = "fanout-key-001"
        actor = Actor("test", "orchestrator", {})

        # First: low-rating survey triggers fan-out (survey + low-score ticket)
        result1 = respond_to_customer_message(
            {
                "message": f"我要评价 1分 体验很差 订单 {self.order_id}",
                "customer_id": self.customer_id,
                "order_id": self.order_id,
                "conversation_id": "fanout-conv-001",
            },
            actor=actor,
            verification=self.verification,
            idempotency_key=key,
        )

        # Replay with same key
        result2 = respond_to_customer_message(
            {
                "message": f"我要评价 1分 体验很差 订单 {self.order_id}",
                "customer_id": self.customer_id,
                "order_id": self.order_id,
                "conversation_id": "fanout-conv-002",
            },
            actor=actor,
            verification=self.verification,
            idempotency_key=key,
        )

        # Both should succeed without IntegrityError
        self.assertIn(result1["status"], {"success", "needs-info", "needs-human"})
        self.assertIn(result2["status"], {"success", "needs-info", "needs-human"})


if __name__ == "__main__":
    unittest.main()
