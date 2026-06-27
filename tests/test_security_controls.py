"""Unit tests for auth, RBAC, OTP, PII, idempotency, and state machines."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

from starlette.exceptions import HTTPException

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

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
        os.environ["AUTH_DEV_SECRET"] = "customer-service-test-secret-min-32-bytes"
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

    def _verification_for(self, session, customer_id=None, order_id=None):
        challenge = request_otp(session, "customer_identity", "email", "test@example.com", customer_id, order_id)
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

    def test_unscoped_verification_cannot_access_protected_customer_or_order_resources(self):
        session = database.get_session()
        try:
            actor = Actor("agent", "order_inquiry", {})
            after_sales = Actor("agent", "after_sales", {})
            order = session.query(Order).filter_by(status="delivered").first()
            verification = self._verification_for(session)

            for action in (
                lambda: svc.get_order(session, actor, order.id, verification),
                lambda: svc.get_customer(session, actor, order.customer_id, verification),
                lambda: svc.create_return(session, after_sales, order.id, "refund", "quality", "", order.customer_id, verification),
            ):
                with self.assertRaises(HTTPException) as cm:
                    action()
                self.assertEqual(cm.exception.status_code, 403)
        finally:
            session.close()

    def test_customer_scoped_verification_allows_owned_orders_and_rejects_other_customers(self):
        session = database.get_session()
        try:
            actor = Actor("agent", "order_inquiry", {})
            owned = session.query(Order).filter_by(status="delivered").first()
            other = session.query(Order).filter(Order.customer_id != owned.customer_id).first()
            verification = self._verification_for(session, customer_id=owned.customer_id)

            full = svc.get_order(session, actor, owned.id, verification)
            self.assertEqual(full["id"], owned.id)

            with self.assertRaises(HTTPException) as cm:
                svc.get_order(session, actor, other.id, verification)
            self.assertEqual(cm.exception.status_code, 403)
        finally:
            session.close()

    def test_order_scoped_verification_rejects_wrong_order(self):
        session = database.get_session()
        try:
            actor = Actor("agent", "order_inquiry", {})
            owned = session.query(Order).filter_by(status="delivered").first()
            other = session.query(Order).filter(Order.id != owned.id).first()
            verification = self._verification_for(session, order_id=owned.id)

            with self.assertRaises(HTTPException) as cm:
                svc.get_order(session, actor, other.id, verification)
            self.assertEqual(cm.exception.status_code, 403)
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
