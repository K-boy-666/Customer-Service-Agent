"""Unit tests for auth, RBAC, OTP, PII, idempotency, and state machines."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

from fastapi import HTTPException

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database
import seed_data
import service_layer as svc
from models import AuditEvent, Order
from security import (
    Actor,
    create_dev_jwt,
    decode_jwt_token,
    load_verification,
    request_otp,
    run_idempotent,
    verify_otp,
)


class SecurityControlsTest(unittest.TestCase):
    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(prefix="customer-security-", suffix=".db")
        os.close(fd)
        os.environ["DATABASE_URL"] = "sqlite+pysqlite:///" + self.db_path.replace("\\", "/")
        os.environ["AUTH_DEV_SECRET"] = "test-secret"
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

    def _verified(self, session, order):
        challenge = request_otp(session, "customer_identity", "email", "test@example.com", order.customer_id, order.id)
        result = verify_otp(session, challenge["challenge_id"], challenge["dev_code"])
        return load_verification(session, result["verification_token"])

    def test_jwt_valid_and_invalid_role(self):
        token = create_dev_jwt("agent-1", "order_inquiry")
        actor = decode_jwt_token(token)
        self.assertEqual(actor.subject, "agent-1")
        self.assertEqual(actor.role, "order_inquiry")
        bad = create_dev_jwt("agent-2", "unknown")
        with self.assertRaises(HTTPException):
            decode_jwt_token(bad)

    def test_rbac_l0_cannot_create_return(self):
        session = database.get_session()
        try:
            order = session.query(Order).filter_by(status="delivered").first()
            verification = self._verified(session, order)
            with self.assertRaises(HTTPException) as cm:
                svc.create_return(
                    session,
                    Actor("agent", "order_inquiry", {}),
                    order.id,
                    "refund",
                    "越权退款",
                    "",
                    order.customer_id,
                    verification,
                )
            self.assertEqual(cm.exception.status_code, 403)
        finally:
            session.close()

    def test_pii_masking_and_verified_full_detail(self):
        session = database.get_session()
        try:
            actor = Actor("agent", "order_inquiry", {})
            masked = svc.search_orders(session, actor, "张三", 1)["data"][0]
            self.assertIn("***", masked["customer_email"])
            order = session.query(Order).filter_by(id=masked["id"]).one()
            verified = self._verified(session, order)
            full = svc.get_order(session, actor, order.id, verified)
            self.assertIn("@", full["customer_email"])
            self.assertNotIn("***", full["shipping_address"])
        finally:
            session.close()

    def test_otp_reuse_is_rejected(self):
        session = database.get_session()
        try:
            challenge = request_otp(session, "customer_identity", "email", "test@example.com", 1, None)
            verify_otp(session, challenge["challenge_id"], challenge["dev_code"])
            with self.assertRaises(HTTPException) as cm:
                verify_otp(session, challenge["challenge_id"], challenge["dev_code"])
            self.assertEqual(cm.exception.status_code, 409)
        finally:
            session.close()

    def test_illegal_return_transition_rejected(self):
        session = database.get_session()
        try:
            order = session.query(Order).filter_by(status="delivered").first()
            verification = self._verified(session, order)
            ret = svc.create_return(session, Actor("agent", "after_sales", {}), order.id, "return", "测试", "", order.customer_id, verification)
            with self.assertRaises(HTTPException) as cm:
                svc.update_return_status(session, Actor("agent", "after_sales", {}), ret["id"], "completed", verification=verification)
            self.assertEqual(cm.exception.status_code, 409)
        finally:
            session.close()

    def test_idempotency_replay_and_conflict(self):
        session = database.get_session()
        try:
            actor = Actor("agent", "work_order", {})
            payload = {"x": 1}
            first = run_idempotent(session, actor, "POST /unit", "same-key", payload, lambda: ({"ok": True}, 201))
            second = run_idempotent(session, actor, "POST /unit", "same-key", payload, lambda: ({"ok": False}, 201))
            self.assertFalse(first[2])
            self.assertTrue(second[2])
            self.assertEqual(second[0], {"ok": True})
            with self.assertRaises(HTTPException) as cm:
                run_idempotent(session, actor, "POST /unit", "same-key", {"x": 2}, lambda: ({"ok": False}, 201))
            self.assertEqual(cm.exception.status_code, 409)
        finally:
            session.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
