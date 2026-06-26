"""Tests for daily customer-service usage analytics."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime

from starlette.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import analytics_service
import database
import seed_data
from models import AuditEvent, ReturnRequest, SatisfactionSurvey, Ticket
from security import Actor, create_dev_jwt


class DailyAnalyticsTest(unittest.TestCase):
    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(prefix="customer-analytics-", suffix=".db")
        os.close(fd)
        os.environ["DATABASE_URL"] = "sqlite+pysqlite:///" + self.db_path.replace("\\", "/")
        os.environ["AUTH_DEV_SECRET"] = "customer-service-test-secret-min-32-bytes"
        os.environ["REPORT_TIMEZONE"] = "Asia/Shanghai"
        database.reset_engine_for_tests()
        database.init_db()
        session = database.get_session()
        try:
            seed_data.seed(session)
            session.commit()
        finally:
            session.close()

    def tearDown(self) -> None:
        database.reset_engine_for_tests("sqlite+pysqlite:///:memory:")
        os.environ.pop("REPORT_TIMEZONE", None)
        for suffix in ("", "-wal", "-shm"):
            path = f"{self.db_path}{suffix}"
            if os.path.exists(path):
                os.remove(path)

    def _headers(self, role: str):
        return {"Authorization": f"Bearer {create_dev_jwt(role + '-user', role)}"}

    def _seed_analytics_rows(self) -> None:
        ts = datetime(2026, 6, 23, 10, 30, 0)
        session = database.get_session()
        try:
            analytics_service.record_usage_event(
                session,
                conversation_id="conv-analytics-1",
                customer_id=1,
                order_id="ORD-20260601-001",
                message_length=18,
                status="success",
                emotional_level="L1",
                intents=[{"intent": "order_inquiry", "confidence": 0.9, "suggested_agent": "order-inquiry-agent"}],
                dispatched_agents=["order-inquiry-agent"],
                tool_calls=[{"tool": "get_order", "status": "success", "summary": "id=1"}],
                needs_human=False,
            )
            analytics_service.record_usage_event(
                session,
                conversation_id="conv-analytics-2",
                customer_id=2,
                order_id=None,
                message_length=24,
                status="needs-human",
                emotional_level="L2",
                intents=[{"intent": "complaint", "confidence": 0.95, "suggested_agent": "complaint-agent"}],
                dispatched_agents=["complaint-agent", "work-order-agent"],
                tool_calls=[{"tool": "create_ticket", "status": "failed", "summary": "validation error"}],
                needs_human=True,
                failure_reason="tool failure surfaced during routing",
            )
            for event in session.query(analytics_service.CustomerServiceUsageEvent).all():
                event.created_at = ts
            session.add(Ticket(ticket_number="TK-20260623-001", title="低分回访", type="service_request", priority="P2", status="new", description="低分 follow-up", customer_id=1, created_at=ts, updated_at=ts))
            session.add(ReturnRequest(return_number="RMA-20260623-001", order_id="ORD-20260601-001", customer_id=1, type="refund", reason="quality", status="pending", created_at=ts, updated_at=ts))
            session.add(SatisfactionSurvey(survey_number="SAT-20260623-001", customer_id=1, order_id="ORD-20260601-001", rating=2, feedback_text="slow", created_at=ts))
            session.add(AuditEvent(actor_subject="tester", actor_role="work_order", permission="ticket:create", endpoint="create_ticket", resource_type="ticket", result="failed", failure_reason="validation", created_at=ts))
            session.commit()
        finally:
            session.close()

    def test_aggregates_daily_usage_without_customer_message_content(self):
        self._seed_analytics_rows()
        session = database.get_session()
        try:
            data = analytics_service.get_usage_analytics(session, Actor("analyst", "data_analysis", {}), "2026-06-23")
        finally:
            session.close()

        self.assertEqual(data["timezone"], "Asia/Shanghai")
        self.assertEqual(data["window"]["start_utc"], "2026-06-22T16:00:00")
        self.assertEqual(data["window"]["end_utc"], "2026-06-23T16:00:00")
        self.assertEqual(data["usage"]["total_conversations"], 2)
        self.assertEqual(data["usage"]["unique_customers"], 2)
        self.assertEqual(data["routing"]["intent_counts"]["order_inquiry"], 1)
        self.assertEqual(data["routing"]["tool_status_counts"]["failed"], 1)
        self.assertEqual(data["operations"]["tickets_created"], 1)
        self.assertEqual(data["operations"]["returns_created"], 1)
        self.assertEqual(data["operations"]["low_rating_count"], 1)
        self.assertEqual(data["quality_signals"]["audit_failure_count"], 1)
        self.assertNotIn("customer_reply", str(data).lower())
        self.assertNotIn("raw_message", str(data).lower())
        self.assertTrue(data["recommendations"])

    def test_business_day_uses_report_timezone_for_utc_boundaries(self):
        session = database.get_session()
        try:
            analytics_service.record_usage_event(
                session,
                conversation_id="shanghai-day",
                customer_id=1,
                order_id=None,
                message_length=10,
                status="success",
                emotional_level="L1",
                intents=[{"intent": "consultation"}],
                dispatched_agents=["consultation-agent"],
                tool_calls=[],
                needs_human=False,
            )
            analytics_service.record_usage_event(
                session,
                conversation_id="next-shanghai-day",
                customer_id=2,
                order_id=None,
                message_length=10,
                status="success",
                emotional_level="L1",
                intents=[{"intent": "consultation"}],
                dispatched_agents=["consultation-agent"],
                tool_calls=[],
                needs_human=False,
            )
            rows = session.query(analytics_service.CustomerServiceUsageEvent).order_by(analytics_service.CustomerServiceUsageEvent.id).all()
            rows[-2].created_at = datetime(2026, 6, 22, 16, 30, 0)
            rows[-1].created_at = datetime(2026, 6, 23, 16, 30, 0)
            session.commit()
            data = analytics_service.get_usage_analytics(session, Actor("analyst", "data_analysis", {}), "2026-06-23")
        finally:
            session.close()

        self.assertEqual(data["timezone"], "Asia/Shanghai")
        self.assertEqual(data["window"]["start_utc"], "2026-06-22T16:00:00")
        self.assertEqual(data["window"]["end_utc"], "2026-06-23T16:00:00")
        self.assertEqual(data["usage"]["total_conversations"], 1)
        self.assertEqual(data["routing"]["intent_counts"], {"consultation": 1})

    def test_rest_endpoint_allows_data_analysis_and_denies_order_inquiry(self):
        self._seed_analytics_rows()
        import order_api

        with TestClient(order_api.app) as client:
            allowed = client.get("/api/analytics/usage", params={"date": "2026-06-23"}, headers=self._headers("data_analysis"))
            denied = client.get("/api/analytics/usage", params={"date": "2026-06-23"}, headers=self._headers("order_inquiry"))

        self.assertEqual(allowed.status_code, 200, allowed.text)
        self.assertEqual(allowed.json()["usage"]["total_conversations"], 2)
        self.assertEqual(denied.status_code, 403)

    def test_cli_writes_markdown_report(self):
        self._seed_analytics_rows()
        with tempfile.TemporaryDirectory(prefix="customer-report-") as output_dir:
            script = os.path.join(os.path.dirname(os.path.dirname(__file__)), "scripts", "generate_daily_usage_report.py")
            completed = subprocess.run(
                [sys.executable, script, "--date", "2026-06-23", "--output-dir", output_dir],
                check=True,
                capture_output=True,
                text=True,
                env=os.environ.copy(),
            )
            report_path = completed.stdout.strip()
            self.assertTrue(os.path.exists(report_path), report_path)
            with open(report_path, encoding="utf-8") as report_file:
                body = report_file.read()
            self.assertIn("Daily Overview", body)
            self.assertIn("Timezone: Asia/Shanghai", body)
            self.assertIn("Window UTC: 2026-06-22T16:00:00 to 2026-06-23T16:00:00", body)
            self.assertIn("Conversations: 2", body)


if __name__ == "__main__":
    unittest.main(verbosity=2)


