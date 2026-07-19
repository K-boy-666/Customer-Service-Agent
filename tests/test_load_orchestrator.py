"""Load tests for orchestrator response time and throughput under mixed load.

Marked with ``@pytest.mark.load`` — not run by default in CI gate.
Run explicitly: ``pytest tests/test_load_orchestrator.py -m load``
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import pytest

import database
import profit_engine_hooks
import seed_data
from models import Order
from numbering import reset_for_tests
from orchestrator_api import respond_to_customer_message
from orchestrator_runtime import _CONVERSATION_STATES, MAX_CONVERSATION_STATES, reset_conversation_states_for_tests
from security import Actor, load_verification, request_otp, verify_otp


class LoadOrchestratorTest(unittest.TestCase):
    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(prefix="load-test-", suffix=".db")
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
        self.orders = self._load_orders()

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
                session.query(Order).filter_by(status="delivered").order_by(Order.created_at.desc()).limit(20).all()
            )
            return [(o.id, o.customer_id) for o in orders]
        finally:
            session.close()

    def _make_verifications(self, n: int) -> list[tuple[str, int, object]]:
        results = []
        for order_id, customer_id in self.orders[:n]:
            session = database.get_session()
            try:
                challenge = request_otp(
                    session, "customer_identity", "email", "test@example.com", customer_id, order_id
                )
                verified = verify_otp(session, challenge["challenge_id"], challenge["dev_code"])
                session.commit()
                v = load_verification(session, verified["verification_token"])
            finally:
                session.close()
            results.append((order_id, customer_id, v))
        return results

    @pytest.mark.load
    def test_p95_response_time_under_mixed_load(self):
        """50 concurrent mixed requests; P95 < 2000ms (SQLite local baseline)."""
        verifications = self._make_verifications(10)
        n = 50

        messages = [
            "订单物流到哪里了?",
            "我要退货",
            "你好,请问有什么服务?",
            "我要投诉 体验很差 转人工",
        ]

        latencies: list[float] = []
        errors: list[Exception] = []

        def worker(idx: int) -> float:
            order_id, customer_id, verification = verifications[idx % len(verifications)]
            msg = messages[idx % len(messages)]
            conv_id = f"load-conv-{idx}"
            start = time.perf_counter()
            try:
                respond_to_customer_message(
                    {"message": msg, "customer_id": customer_id, "order_id": order_id, "conversation_id": conv_id},
                    actor=Actor("load-test", "orchestrator", {}),
                    verification=verification,
                )
            except Exception as exc:
                errors.append(exc)
            return time.perf_counter() - start

        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = [pool.submit(worker, i) for i in range(n)]
            for f in as_completed(futures):
                latencies.append(f.result())

        latencies.sort()
        p95 = latencies[int(len(latencies) * 0.95)]
        self.assertEqual(errors, [])
        self.assertLess(p95, 2.0)

    @pytest.mark.load
    def test_throughput_sustained_burst(self):
        """Sustained burst; verify QPS is positive and no state leak."""
        verifications = self._make_verifications(5)
        duration = 3  # seconds
        count = 0
        errors: list[Exception] = []

        def worker(idx: int) -> bool:
            order_id, customer_id, verification = verifications[idx % len(verifications)]
            try:
                respond_to_customer_message(
                    {"message": f"订单 {order_id} 物流", "customer_id": customer_id, "conversation_id": f"burst-{idx}"},
                    actor=Actor("burst", "orchestrator", {}),
                    verification=verification,
                )
                return True
            except Exception as exc:
                errors.append(exc)
                return False

        start = time.perf_counter()
        idx = 0
        while time.perf_counter() - start < duration:
            with ThreadPoolExecutor(max_workers=5) as pool:
                futures = [pool.submit(worker, idx + i) for i in range(5)]
                for f in as_completed(futures):
                    if f.result():
                        count += 1
            idx += 5

        elapsed = time.perf_counter() - start
        qps = count / elapsed
        self.assertGreater(qps, 0)
        self.assertEqual(errors, [])
        self.assertLessEqual(len(_CONVERSATION_STATES), MAX_CONVERSATION_STATES)

    @pytest.mark.load
    def test_tail_latency_under_write_contention(self):
        """20 concurrent ticket creations; max latency < 5s."""
        verifications = self._make_verifications(1)
        order_id, customer_id, verification = verifications[0]
        n = 20

        latencies: list[float] = []
        errors: list[Exception] = []

        from service_layer import create_ticket

        def worker(idx: int) -> float:
            start = time.perf_counter()
            try:
                with database.session_scope() as session:
                    create_ticket(
                        session,
                        Actor(f"load-{idx}", "work_order", {}),
                        f"Load ticket {idx}",
                        "load test",
                        "incident",
                        "P3",
                        customer_id,
                        order_id,
                        verification,
                        f"load-ticket-{idx}",
                    )
                    session.commit()
            except Exception as exc:
                errors.append(exc)
            return time.perf_counter() - start

        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = [pool.submit(worker, i) for i in range(n)]
            for f in as_completed(futures):
                latencies.append(f.result())

        self.assertEqual(errors, [])
        self.assertLess(max(latencies), 5.0)

    @pytest.mark.load
    def test_no_state_leak_between_load_waves(self):
        """Two waves of load; _CONVERSATION_STATES stays bounded between waves."""
        verifications = self._make_verifications(5)

        def wave(wave_id: int) -> None:
            for i in range(20):
                order_id, customer_id, verification = verifications[i % len(verifications)]
                respond_to_customer_message(
                    {"message": f"订单 {order_id} 物流", "conversation_id": f"wave-{wave_id}-{i}"},
                    actor=Actor("wave", "orchestrator", {}),
                    verification=verification,
                )

        wave(1)
        size_after_wave1 = len(_CONVERSATION_STATES)
        self.assertLessEqual(size_after_wave1, MAX_CONVERSATION_STATES)

        wave(2)
        size_after_wave2 = len(_CONVERSATION_STATES)
        self.assertLessEqual(size_after_wave2, MAX_CONVERSATION_STATES)


if __name__ == "__main__":
    unittest.main()
