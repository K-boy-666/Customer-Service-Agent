"""End-to-end tests for Agent runtime, REST API, and MCP entrypoint."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database
import seed_data
from models import CustomerServiceUsageEvent, Order, ReturnRequest, Ticket
from orchestrator_runtime import CustomerServiceOrchestrator
from security import Actor, create_dev_jwt, load_verification, request_otp, verify_otp


class OrchestratorE2ETest(unittest.TestCase):
    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(prefix="customer-agent-", suffix=".db")
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

    def _first_order(self, order_status: str = "delivered") -> tuple[str, int, str]:
        session = database.get_session()
        try:
            order = session.query(Order).filter_by(status=order_status).order_by(Order.created_at.desc()).first()
            self.assertIsNotNone(order)
            token = self._verification(order.customer_id, order.id)
            return order.id, order.customer_id, token
        finally:
            session.close()

    def _verification(self, customer_id: int, order_id: str | None) -> str:
        session = database.get_session()
        try:
            challenge = request_otp(session, "customer_identity", "email", "test@example.com", customer_id, order_id)
            verified = verify_otp(session, challenge["challenge_id"], challenge["dev_code"])
            session.commit()
            return verified["verification_token"]
        finally:
            session.close()

    def _count(self, model) -> int:
        session = database.get_session()
        try:
            return session.query(model).count()
        finally:
            session.close()

    def test_agent_runtime_routes_order_inquiry_and_preserves_protocol_output(self):
        order_id, customer_id, verification_token = self._first_order("delivered")
        before_usage_events = self._count(CustomerServiceUsageEvent)
        session = database.get_session()
        try:
            verification = load_verification(session, verification_token)
        finally:
            session.close()
        runtime = CustomerServiceOrchestrator(actor=Actor("api-user", "orchestrator", {}), verification=verification)

        result = runtime.handle_message(
            message=f"\u8ba2\u5355 {order_id} \u7269\u6d41\u5230\u54ea\u91cc\u4e86?",
            customer_id=customer_id,
            conversation_id="agent-e2e-order",
            actor=Actor("api-user", "orchestrator", {}),
            verification=verification,
        )

        self.assertEqual(result["status"], "success")
        self.assertIn("order-inquiry-agent", result["dispatched_agents"])
        self.assertIn(order_id, result["customer_reply"])
        self.assertTrue({"get_order", "get_shipment"}.issubset({call["tool"] for call in result["tool_calls"]}))
        self.assertIn("protocol_output", result["agent_results"][0])
        self.assertEqual(self._count(CustomerServiceUsageEvent), before_usage_events + 1)

    def test_api_endpoint_creates_after_sales_refund_request(self):
        from fastapi.testclient import TestClient
        import order_api

        order_id, customer_id, verification_token = self._first_order("delivered")
        before = self._count(ReturnRequest)
        headers = {
            "Authorization": f"Bearer {create_dev_jwt('orchestrator-user', 'orchestrator')}",
            "X-Identity-Verification": verification_token,
            "Idempotency-Key": "api-refund-key",
        }
        with TestClient(order_api.app) as client:
            response = client.post(
                "/api/orchestrator/respond",
                json={
                    "message": f"\u8ba2\u5355 {order_id} \u7684\u5546\u54c1\u6709\u8d28\u91cf\u95ee\u9898\uff0c\u6211\u8981\u7533\u8bf7\u9000\u6b3e",
                    "customer_id": customer_id,
                    "conversation_id": "api-e2e-refund",
                },
                headers=headers,
            )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertIn("after-sales-agent", body["dispatched_agents"])
        self.assertIn("RMA-", body["customer_reply"])
        self.assertIn("create_return", {call["tool"] for call in body["tool_calls"]})
        self.assertEqual(self._count(ReturnRequest), before + 1)

    def test_mcp_tool_records_complaint_and_work_order(self):
        import server_customer

        order_id, customer_id, verification_token = self._first_order("shipped")
        before = self._count(Ticket)
        raw = asyncio.run(
            server_customer.handle_customer_message(
                message=f"\u6211\u8981\u6295\u8bc9\uff0c\u8ba2\u5355 {order_id} \u4e00\u76f4\u6ca1\u5230\uff0c\u518d\u4e0d\u5904\u7406\u6211\u5c31\u627e315\u66dd\u5149",
                customer_id=customer_id,
                conversation_id="mcp-e2e-complaint",
                actor_subject="mcp-user",
                actor_role="orchestrator",
                verification_token=verification_token,
                idempotency_key="mcp-complaint-key",
            )
        )
        body = json.loads(raw)

        self.assertEqual(body["status"], "needs-human")
        self.assertEqual(body["emotional_level"], "L2")
        self.assertIn("complaint-agent", body["dispatched_agents"])
        self.assertIn("work-order-agent", body["dispatched_agents"])
        self.assertIn("create_ticket", {call["tool"] for call in body["tool_calls"]})
        self.assertEqual(self._count(Ticket), before + 1)

    def test_mcp_tool_denies_non_orchestrator_role_for_customer_flow(self):
        import server_customer

        order_id, customer_id, verification_token = self._first_order("delivered")
        before = self._count(ReturnRequest)
        raw = asyncio.run(
            server_customer.handle_customer_message(
                message=f"\u8ba2\u5355 {order_id} \u7684\u5546\u54c1\u574f\u4e86\uff0c\u6211\u8981\u9000\u6b3e",
                customer_id=customer_id,
                actor_subject="complaint-only",
                actor_role="complaint",
                verification_token=verification_token,
                idempotency_key="mcp-deny-key",
            )
        )
        body = json.loads(raw)
        self.assertEqual(body["status"], "denied")
        self.assertEqual(self._count(ReturnRequest), before)


if __name__ == "__main__":
    unittest.main(verbosity=2)

