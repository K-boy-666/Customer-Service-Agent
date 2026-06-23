"""Integration smoke tests for the productionized service layer."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database
import seed_data
import service_layer as svc
from models import AuditEvent, Order, ReturnRequest, SatisfactionSurvey, Ticket
from security import Actor, load_verification, request_otp, verify_otp


class SmokeTest(unittest.TestCase):
    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(prefix="customer-smoke-", suffix=".db")
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

    def _verification(self, customer_id: int, order_id: str | None = None):
        session = database.get_session()
        try:
            challenge = request_otp(session, "customer_identity", "email", "test@example.com", customer_id, order_id)
            verified = verify_otp(session, challenge["challenge_id"], challenge["dev_code"])
            session.commit()
            return verified["verification_token"]
        finally:
            session.close()

    def test_order_to_logistics(self):
        session = database.get_session()
        try:
            order = session.query(Order).filter_by(status="delivered").first()
            token = self._verification(order.customer_id, order.id)
            verification = load_verification(session, token)
            shipment = svc.get_shipment(session, Actor("agent", "order_inquiry", {}), order.id, verification)
            self.assertEqual(shipment["order_id"], order.id)
            self.assertGreater(len(shipment["events"]), 0)
        finally:
            session.close()

    def test_return_flow(self):
        session = database.get_session()
        try:
            order = session.query(Order).filter_by(status="delivered").first()
            token = self._verification(order.customer_id, order.id)
            verification = load_verification(session, token)
            ret = svc.create_return(
                session,
                Actor("agent", "after_sales", {}),
                order.id,
                "refund",
                "烟雾测试-退款",
                "自动化测试创建的退款申请",
                order.customer_id,
                verification,
            )
            updated = svc.update_return_status(session, Actor("agent", "after_sales", {}), ret["id"], "approved", verification=verification)
            self.assertEqual(updated["status"], "approved")
            updated = svc.update_return_status(session, Actor("agent", "after_sales", {}), ret["id"], "refunded", verification=verification)
            self.assertEqual(updated["status"], "refunded")
        finally:
            session.close()

    def test_complaint_to_escalation_ticket(self):
        session = database.get_session()
        try:
            order = session.query(Order).filter_by(status="shipped").first()
            token = self._verification(order.customer_id, order.id)
            verification = load_verification(session, token)
            ticket = svc.create_ticket(
                session,
                Actor("agent", "work_order", {}),
                "客户投诉：物流延迟",
                "客户威胁向315投诉，需主管跟进。",
                "incident",
                "P1",
                order.customer_id,
                order.id,
                verification,
            )
            self.assertEqual(ticket["priority"], "P1")
            self.assertEqual(ticket["status"], "new")
        finally:
            session.close()

    def test_seeded_knowledge_base_still_available(self):
        import json

        with open(os.path.join(os.path.dirname(os.path.dirname(__file__)), "faq.json"), encoding="utf-8") as f:
            faq = json.load(f)
        self.assertGreaterEqual(len(faq), 50)
        self.assertIn("退货政策", {entry["category"] for entry in faq})

    def test_satisfaction_low_score_followup(self):
        session = database.get_session()
        try:
            order = session.query(Order).filter_by(status="delivered").first()
            token = self._verification(order.customer_id, order.id)
            verification = load_verification(session, token)
            survey = svc.submit_survey(
                session,
                Actor("agent", "work_order", {}),
                1,
                "处理太慢",
                order.customer_id,
                order.id,
                verification,
            )
            ticket = svc.create_ticket(
                session,
                Actor("agent", "work_order", {}),
                "低分回访工单 -- 客户满意度1星",
                "客户反馈处理太慢。",
                "service_request",
                "P2",
                order.customer_id,
                order.id,
                verification,
            )
            self.assertTrue(survey["survey_number"].startswith("SAT-"))
            self.assertIn("低分回访", ticket["title"])
            self.assertGreaterEqual(session.query(AuditEvent).count(), 2)
        finally:
            session.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
