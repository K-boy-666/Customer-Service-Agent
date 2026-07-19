"""End-to-end tests for Agent runtime, REST API, and MCP entrypoint."""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import sys
import tempfile
import unittest

from starlette.exceptions import HTTPException

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import database
import profit_engine_hooks
import seed_data
from models import CustomerServiceUsageEvent, Order, ReturnRequest, SatisfactionSurvey, Ticket
from orchestrator_runtime import _CONVERSATION_STATES, CustomerServiceOrchestrator
from security import Actor, create_dev_jwt, load_verification, request_otp, verify_otp


class OrchestratorE2ETest(unittest.TestCase):
    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(prefix="customer-agent-", suffix=".db")
        os.close(fd)
        os.environ["DATABASE_URL"] = "sqlite+pysqlite:///" + self.db_path.replace("\\", "/")
        os.environ["AUTH_DEV_SECRET"] = "customer-service-test-secret-min-32-bytes"
        database.reset_engine_for_tests()
        _CONVERSATION_STATES.clear()
        database.init_db()
        session = database.get_session()
        try:
            seed_data.seed(session)
        finally:
            session.close()

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

    def _import_src_order_api(self):
        src_order_api = os.path.abspath(
            os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src", "order_api.py")
        )
        spec = importlib.util.spec_from_file_location("order_api", src_order_api)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        sys.modules["order_api"] = module
        spec.loader.exec_module(module)
        return module

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

    def test_agent_runtime_records_failed_usage_event_when_tool_raises(self):
        order_id, customer_id, _verification_token = self._first_order("delivered")
        before_usage_events = self._count(CustomerServiceUsageEvent)
        runtime = CustomerServiceOrchestrator(actor=Actor("api-user", "orchestrator", {}))

        with self.assertRaises(HTTPException):
            runtime.handle_message(
                message=f"订单 {order_id} 物流到哪里了?",
                customer_id=customer_id,
                conversation_id="agent-e2e-failed-tool",
                actor=Actor("api-user", "orchestrator", {}),
            )

        self.assertEqual(self._count(CustomerServiceUsageEvent), before_usage_events + 1)
        session = database.get_session()
        try:
            event = session.query(CustomerServiceUsageEvent).filter_by(conversation_id="agent-e2e-failed-tool").one()
            self.assertEqual(event.status, "failed")
            self.assertEqual(event.customer_id, customer_id)
            self.assertEqual(event.order_id, order_id)
            self.assertIn("missing_identity_verification", event.failure_reason)
            self.assertIn("failed", {call["status"] for call in event.tool_calls})
            self.assertNotIn("物流到哪里", str(event.intents) + str(event.tool_calls) + event.failure_reason)
        finally:
            session.close()

    def test_api_endpoint_creates_after_sales_refund_request(self):
        from starlette.testclient import TestClient

        order_api = self._import_src_order_api()

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

    def test_mcp_tool_reports_partial_business_result_after_successful_writes(self):
        import server_customer

        order_id, customer_id, verification_token = self._first_order("cancelled")
        before_returns = self._count(ReturnRequest)
        before_surveys = self._count(SatisfactionSurvey)
        before_tickets = self._count(Ticket)

        raw = asyncio.run(
            server_customer.handle_customer_message(
                message=(
                    f"\u8ba2\u5355 {order_id} \u7269\u6d41\u5230\u54ea\u4e86\uff1f"
                    "\u6211\u8981\u9000\u8d27\uff0c\u6211\u8981\u6295\u8bc9\uff0c\u7ed9\u8fd9\u6b21\u670d\u52a1\u6253 3 stars"
                ),
                customer_id=customer_id,
                conversation_id="mcp-e2e-partial-after-writes",
                actor_subject="mcp-user",
                actor_role="orchestrator",
                verification_token=verification_token,
                idempotency_key="mcp-multi-intent-key",
            )
        )
        body = json.loads(raw)

        self.assertEqual(body["status"], "needs-human")
        self.assertNotEqual(body["status"], "denied")
        self.assertIn("order-inquiry-agent", body["dispatched_agents"])
        self.assertIn("after-sales-agent", body["dispatched_agents"])
        self.assertIn("work-order-agent", body["dispatched_agents"])
        self.assertIn("get_shipment", {call["tool"] for call in body["tool_calls"]})
        self.assertEqual(self._count(ReturnRequest), before_returns + 1)
        self.assertEqual(self._count(SatisfactionSurvey), before_surveys + 1)
        self.assertEqual(self._count(Ticket), before_tickets + 2)

        retry = asyncio.run(
            server_customer.handle_customer_message(
                message=(
                    f"\u8ba2\u5355 {order_id} \u7269\u6d41\u5230\u54ea\u4e86\uff1f"
                    "\u6211\u8981\u9000\u8d27\uff0c\u6211\u8981\u6295\u8bc9\uff0c\u7ed9\u8fd9\u6b21\u670d\u52a1\u6253 3 stars"
                ),
                customer_id=customer_id,
                conversation_id="mcp-e2e-partial-after-writes-retry",
                actor_subject="mcp-user",
                actor_role="orchestrator",
                verification_token=verification_token,
                idempotency_key="mcp-multi-intent-key",
            )
        )
        self.assertEqual(json.loads(retry)["status"], "needs-human")
        self.assertEqual(self._count(ReturnRequest), before_returns + 1)
        self.assertEqual(self._count(SatisfactionSurvey), before_surveys + 1)
        self.assertEqual(self._count(Ticket), before_tickets + 2)

    def test_conversation_state_reuses_order_context_for_follow_up(self):
        from orchestrator_api import respond_to_customer_message

        order_id, customer_id, verification_token = self._first_order("delivered")
        session = database.get_session()
        try:
            verification = load_verification(session, verification_token)
        finally:
            session.close()

        respond_to_customer_message(
            {
                "message": f"\u8ba2\u5355 {order_id} \u7269\u6d41\u5230\u54ea\u4e86?",
                "customer_id": customer_id,
                "conversation_id": "conversation-state-follow-up",
            },
            actor=Actor("api-user", "orchestrator", {}),
            verification=verification,
        )

        before = self._count(ReturnRequest)
        follow_up = respond_to_customer_message(
            {
                "message": "\u6211\u8981\u9000\u8d27",
                "conversation_id": "conversation-state-follow-up",
            },
            actor=Actor("api-user", "orchestrator", {}),
            verification=verification,
            idempotency_key="conversation-state-return",
        )

        self.assertEqual(follow_up["status"], "success")
        self.assertIn("after-sales-agent", follow_up["dispatched_agents"])
        self.assertIn(order_id, follow_up["customer_reply"])
        self.assertEqual(self._count(ReturnRequest), before + 1)

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
