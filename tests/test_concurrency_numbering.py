"""Concurrency tests for number sequence generation under high contention.

Verifies that ticket/return/survey numbers remain unique when multiple threads
create records simultaneously.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import database
import seed_data
import service_layer as svc
from models import Order
from numbering import reset_for_tests
from orchestrator_runtime import reset_conversation_states_for_tests
from security import Actor, load_verification, request_otp, verify_otp


class ConcurrencyNumberingTest(unittest.TestCase):
    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(prefix="conc-num-", suffix=".db")
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

    def test_concurrent_ticket_numbers_unique_high_contention(self):
        """16 threads x 8 creates = 128 tickets; all numbers must be unique."""
        threads = 16
        per_thread = 8

        def create_tickets(worker_id: int) -> list[str]:
            numbers = []
            for i in range(per_thread):
                with database.session_scope() as session:
                    ticket = svc.create_ticket(
                        session,
                        Actor(f"worker-{worker_id}", "work_order", {}),
                        f"Concurrent ticket {worker_id}-{i}",
                        "load test",
                        "incident",
                        "P3",
                        self.customer_id,
                        self.order_id,
                        self.verification,
                        f"conc-ticket-{worker_id}-{i}",
                    )
                    numbers.append(ticket["ticket_number"])
            return numbers

        with ThreadPoolExecutor(max_workers=threads) as pool:
            results = list(pool.map(create_tickets, range(threads)))

        all_numbers = [n for sublist in results for n in sublist]
        self.assertEqual(len(all_numbers), threads * per_thread)
        self.assertEqual(len(all_numbers), len(set(all_numbers)))

    def test_concurrent_return_numbers_unique(self):
        """Concurrent return creation; RMA numbers must be unique."""
        count = 16

        def create_return(idx: int) -> str:
            with database.session_scope() as session:
                ret = svc.create_return(
                    session,
                    Actor(f"worker-{idx}", "after_sales", {}),
                    self.order_id,
                    "return",
                    "quality issue",
                    "product defective",
                    self.customer_id,
                    self.verification,
                    f"conc-return-{idx}",
                )
                return ret["return_number"]

        with ThreadPoolExecutor(max_workers=8) as pool:
            numbers = list(pool.map(create_return, range(count)))

        self.assertEqual(len(numbers), len(set(numbers)))

    def test_concurrent_survey_numbers_unique(self):
        """Concurrent survey submission; survey numbers must be unique."""
        count = 12

        def submit_survey(idx: int) -> str:
            with database.session_scope() as session:
                survey = svc.submit_survey(
                    session,
                    Actor(f"worker-{idx}", "work_order", {}),
                    5,
                    "great service",
                    self.customer_id,
                    self.order_id,
                    self.verification,
                    f"conc-survey-{idx}",
                )
                return survey["survey_number"]

        with ThreadPoolExecutor(max_workers=6) as pool:
            numbers = list(pool.map(submit_survey, range(count)))

        self.assertEqual(len(numbers), len(set(numbers)))

    def test_concurrent_mixed_type_numbers_no_cross_collision(self):
        """Ticket/return/survey created simultaneously; no cross-type collision."""

        def create_ticket(idx: int) -> str:
            with database.session_scope() as session:
                t = svc.create_ticket(
                    session,
                    Actor(f"t-{idx}", "work_order", {}),
                    f"Mixed ticket {idx}",
                    "test",
                    "incident",
                    "P3",
                    self.customer_id,
                    self.order_id,
                    self.verification,
                    f"mix-t-{idx}",
                )
                return t["ticket_number"]

        def create_return(idx: int) -> str:
            with database.session_scope() as session:
                r = svc.create_return(
                    session,
                    Actor(f"r-{idx}", "after_sales", {}),
                    self.order_id,
                    "return",
                    "test",
                    "defective",
                    self.customer_id,
                    self.verification,
                    f"mix-r-{idx}",
                )
                return r["return_number"]

        def create_survey(idx: int) -> str:
            with database.session_scope() as session:
                s = svc.submit_survey(
                    session,
                    Actor(f"s-{idx}", "work_order", {}),
                    4,
                    "ok",
                    self.customer_id,
                    self.order_id,
                    self.verification,
                    f"mix-s-{idx}",
                )
                return s["survey_number"]

        with ThreadPoolExecutor(max_workers=6) as pool:
            t_futures = [pool.submit(create_ticket, i) for i in range(6)]
            r_futures = [pool.submit(create_return, i) for i in range(6)]
            s_futures = [pool.submit(create_survey, i) for i in range(6)]
            t_numbers = [f.result() for f in t_futures]
            r_numbers = [f.result() for f in r_futures]
            s_numbers = [f.result() for f in s_futures]

        # Within-type uniqueness
        self.assertEqual(len(t_numbers), len(set(t_numbers)))
        self.assertEqual(len(r_numbers), len(set(r_numbers)))
        self.assertEqual(len(s_numbers), len(set(s_numbers)))
        # Cross-type: TK/RMA/SAT prefixes differ, so no overlap by design
        all_numbers = t_numbers + r_numbers + s_numbers
        self.assertEqual(len(all_numbers), len(set(all_numbers)))

    def test_local_seq_survives_db_reset(self):
        """After reset_for_tests, numbering continues from DB max+1."""
        # Create one ticket to seed the DB
        with database.session_scope() as session:
            svc.create_ticket(
                session,
                Actor("seed", "work_order", {}),
                "Seed ticket",
                "test",
                "incident",
                "P3",
                self.customer_id,
                self.order_id,
                self.verification,
                "seed-ticket-1",
            )

        # Reset local sequence cache
        reset_for_tests()

        # Concurrent creates should still produce unique numbers continuing from DB max
        def create(idx: int) -> str:
            with database.session_scope() as session:
                t = svc.create_ticket(
                    session,
                    Actor(f"post-{idx}", "work_order", {}),
                    f"Post-reset {idx}",
                    "test",
                    "incident",
                    "P3",
                    self.customer_id,
                    self.order_id,
                    self.verification,
                    f"post-reset-{idx}",
                )
                return t["ticket_number"]

        with ThreadPoolExecutor(max_workers=4) as pool:
            numbers = list(pool.map(create, range(8)))

        self.assertEqual(len(numbers), len(set(numbers)))


if __name__ == "__main__":
    unittest.main()
