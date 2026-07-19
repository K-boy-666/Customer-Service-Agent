"""Concurrency tests for conversation state isolation and thread safety.

L1 tests call ``respond_to_customer_message`` directly via ``ThreadPoolExecutor``.
L2 test uses per-thread ``TestClient`` to cover the FastAPI thread-pool path.
"""

from __future__ import annotations

import os
import sys
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import database
import profit_engine_hooks
import seed_data
from models import Order
from numbering import reset_for_tests
from orchestrator_api import respond_to_customer_message
from orchestrator_runtime import _CONVERSATION_STATES, MAX_CONVERSATION_STATES, reset_conversation_states_for_tests
from security import Actor, create_dev_jwt, load_verification, request_otp, verify_otp


class ConcurrencyIsolationTest(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile

        fd, self.db_path = tempfile.mkstemp(prefix="conc-iso-", suffix=".db")
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
        self._orders = self._load_orders()

    def tearDown(self) -> None:
        # Shut down profit_engine_hooks' ThreadPoolExecutor before resetting
        # the DB engine. Worker threads hold SQLite file handles via
        # SingletonThreadPool; without this, os.remove fails with WinError 32.
        profit_engine_hooks.shutdown_executor_for_tests()
        database.reset_engine_for_tests("sqlite+pysqlite:///:memory:")
        for suffix in ("", "-wal", "-shm"):
            path = f"{self.db_path}{suffix}"
            if os.path.exists(path):
                os.remove(path)

    def _load_orders(self) -> list[tuple[str, int]]:
        session = database.get_session()
        try:
            orders = (
                session.query(Order).filter_by(status="delivered").order_by(Order.created_at.desc()).limit(10).all()
            )
            return [(o.id, o.customer_id) for o in orders]
        finally:
            session.close()

    def _make_verification(self, customer_id: int, order_id: str) -> str:
        session = database.get_session()
        try:
            challenge = request_otp(session, "customer_identity", "email", "test@example.com", customer_id, order_id)
            verified = verify_otp(session, challenge["challenge_id"], challenge["dev_code"])
            session.commit()
            return verified["verification_token"]
        finally:
            session.close()

    def _load_verification_obj(self, token: str):
        session = database.get_session()
        try:
            return load_verification(session, token)
        finally:
            session.close()

    def test_conversation_states_isolated_across_concurrent_sessions(self):
        """N threads each with a unique conversation_id; follow-up order_id must not cross-contaminate."""
        n = 8
        verifications = []
        for order_id, customer_id in self._orders[:n]:
            token = self._make_verification(customer_id, order_id)
            verifications.append((order_id, customer_id, self._load_verification_obj(token)))

        def worker(idx: int) -> dict:
            order_id, customer_id, verification = verifications[idx]
            conv_id = f"conv-iso-{idx}"
            result = respond_to_customer_message(
                {"message": f"订单 {order_id} 物流到哪里了?", "customer_id": customer_id, "conversation_id": conv_id},
                actor=Actor("test", "orchestrator", {}),
                verification=verification,
            )
            return {"idx": idx, "conv_id": conv_id, "order_id": order_id, "result": result}

        with ThreadPoolExecutor(max_workers=n) as pool:
            futures = [pool.submit(worker, i) for i in range(n)]
            results = [f.result() for f in as_completed(futures)]

        self.assertEqual(len(results), n)
        for r in results:
            self.assertEqual(r["result"]["status"], "success")
            self.assertIn(r["order_id"], r["result"]["customer_reply"])
            # Verify the conversation state cached the correct order_id

            state = _CONVERSATION_STATES.get(r["conv_id"])
            self.assertIsNotNone(state)
            self.assertEqual(state.order_id, r["order_id"])

    def test_same_conversation_followup_preserves_order_context_under_concurrency(self):
        """Multiple follow-ups on the same conversation_id must not lose updates."""
        order_id, customer_id = self._orders[0]
        token = self._make_verification(customer_id, order_id)
        verification = self._load_verification_obj(token)
        conv_id = "conv-followup-shared"

        # First message establishes the context
        respond_to_customer_message(
            {"message": f"订单 {order_id} 物流到哪里了?", "customer_id": customer_id, "conversation_id": conv_id},
            actor=Actor("test", "orchestrator", {}),
            verification=verification,
        )

        # Concurrent follow-ups with same conversation_id
        followups = 6

        def followup_worker(_i: int) -> str:
            result = respond_to_customer_message(
                {"message": "我要退货", "customer_id": customer_id, "conversation_id": conv_id},
                actor=Actor("test", "orchestrator", {}),
                verification=verification,
            )
            return result["status"]

        with ThreadPoolExecutor(max_workers=followups) as pool:
            futures = [pool.submit(followup_worker, i) for i in range(followups)]
            statuses = [f.result() for f in as_completed(futures)]

        # All should complete without RuntimeError/KeyError
        self.assertEqual(len(statuses), followups)
        for s in statuses:
            self.assertIn(s, {"success", "needs-info", "needs-escalation"})

        # Final state should be consistent

        state = _CONVERSATION_STATES.get(conv_id)
        self.assertIsNotNone(state)
        self.assertEqual(state.customer_id, customer_id)

    def test_conversation_states_lru_eviction_safe_under_pressure(self):
        """Write > MAX_CONVERSATION_STATES entries concurrently; no KeyError/RuntimeError."""
        count = MAX_CONVERSATION_STATES + 50

        def worker(i: int) -> int:
            respond_to_customer_message(
                {"message": "你好", "conversation_id": f"conv-pressure-{i}"},
                actor=Actor("test", "orchestrator", {}),
            )
            return i

        with ThreadPoolExecutor(max_workers=16) as pool:
            futures = [pool.submit(worker, i) for i in range(count)]
            indices = [f.result() for f in as_completed(futures)]

        self.assertEqual(len(indices), count)

        self.assertLessEqual(len(_CONVERSATION_STATES), MAX_CONVERSATION_STATES)

    def test_conversation_state_cache_hit_does_not_block_other_threads(self):
        """One thread hits cache repeatedly while another does DB query; no deadlock."""
        order_id, customer_id = self._orders[0]
        token = self._make_verification(customer_id, order_id)
        verification = self._load_verification_obj(token)
        conv_cached = "conv-cached-hit"
        conv_db = "conv-db-query"

        # Prime the cache
        respond_to_customer_message(
            {"message": f"订单 {order_id} 物流", "customer_id": customer_id, "conversation_id": conv_cached},
            actor=Actor("test", "orchestrator", {}),
            verification=verification,
        )

        errors: list[Exception] = []

        def cache_hammer():
            try:
                for _ in range(50):
                    respond_to_customer_message(
                        {"message": "还要退货", "customer_id": customer_id, "conversation_id": conv_cached},
                        actor=Actor("test", "orchestrator", {}),
                        verification=verification,
                    )
            except Exception as exc:
                errors.append(exc)

        def db_query():
            try:
                result = respond_to_customer_message(
                    {"message": f"订单 {order_id} 物流", "customer_id": customer_id, "conversation_id": conv_db},
                    actor=Actor("test", "orchestrator", {}),
                    verification=verification,
                )
                return result["status"]
            except Exception as exc:
                errors.append(exc)
                return "error"

        with ThreadPoolExecutor(max_workers=2) as pool:
            f1 = pool.submit(cache_hammer)
            f2 = pool.submit(db_query)
            f1.result()
            status = f2.result()

        self.assertEqual(errors, [])
        self.assertIn(status, {"success", "needs-info"})

    @unittest.skipIf(
        os.environ.get("SKIP_API_CONCURRENCY", ""),
        "Set SKIP_API_CONCURRENCY=1 to skip TestClient-based tests in CI without server deps.",
    )
    def test_api_endpoint_concurrent_isolation_via_per_thread_testclient(self):
        """L2: 4 threads each with independent TestClient; state isolation via API."""
        from fastapi.testclient import TestClient

        import order_api

        n = 4
        jwt_token = create_dev_jwt("test-orchestrator", "orchestrator")
        auth_header = f"Bearer {jwt_token}"
        verifications = []
        for order_id, customer_id in self._orders[:n]:
            token = self._make_verification(customer_id, order_id)
            verifications.append((order_id, customer_id, token))

        results: list[dict] = []
        results_lock = threading.Lock()

        def worker(idx: int):
            client = TestClient(order_api.app)
            order_id, customer_id, token = verifications[idx]
            conv_id = f"conv-api-{idx}"
            resp = client.post(
                "/api/orchestrator/respond",
                json={"message": f"订单 {order_id} 物流", "customer_id": customer_id, "conversation_id": conv_id},
                headers={"X-Identity-Verification": token, "Authorization": auth_header},
            )
            with results_lock:
                results.append({"idx": idx, "status_code": resp.status_code, "conv_id": conv_id, "body": resp.json()})

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        self.assertEqual(len(results), n)
        for r in results:
            self.assertEqual(r["status_code"], 200)
            self.assertEqual(r["body"]["conversation_id"], r["conv_id"])


if __name__ == "__main__":
    unittest.main()
