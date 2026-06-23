"""Scenario 3: after-sales refund request through secured service layer."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database
import seed_data
import service_layer as svc
from models import Order
from security import Actor, load_verification, request_otp, verify_otp


class Scenario3ReturnTest(unittest.TestCase):
    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(prefix="scenario3-", suffix=".db")
        os.close(fd)
        os.environ["DATABASE_URL"] = "sqlite+pysqlite:///" + self.db_path.replace("\\", "/")
        database.reset_engine_for_tests()
        database.init_db()
        session = database.get_session()
        try:
            seed_data.seed(session)
        finally:
            session.close()

    def tearDown(self) -> None:
        database.reset_engine_for_tests("sqlite+pysqlite:///:memory:")
        for suffix in ("", "-wal", "-shm"):
            path = f"{self.db_path}{suffix}"
            if os.path.exists(path):
                os.remove(path)

    def test_refund_request_created_with_rma_number(self):
        session = database.get_session()
        try:
            order = session.query(Order).filter_by(status="delivered").first()
            challenge = request_otp(session, "customer_identity", "email", "test@example.com", order.customer_id, order.id)
            verified = verify_otp(session, challenge["challenge_id"], challenge["dev_code"])
            verification = load_verification(session, verified["verification_token"])
            ret = svc.create_return(
                session,
                Actor("after-sales-test", "after_sales", {}),
                order.id,
                "refund",
                "无线鼠标右键不灵敏",
                "客户反馈商品右键不灵敏，申请仅退款。",
                order.customer_id,
                verification,
            )
            self.assertTrue(ret["return_number"].startswith("RMA-"))
            self.assertEqual(ret["type"], "refund")
            self.assertEqual(ret["status"], "pending")
        finally:
            session.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
