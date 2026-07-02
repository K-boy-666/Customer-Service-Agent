"""FastAPI and Alembic migration end-to-end tests."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

from alembic import command
from alembic.config import Config
from starlette.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import database
import seed_data
from models import Order
from security import create_dev_jwt


class ApiAndMigrationE2ETest(unittest.TestCase):
    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(prefix="customer-api-", suffix=".db")
        os.close(fd)
        os.environ["DATABASE_URL"] = "sqlite+pysqlite:///" + self.db_path.replace("\\", "/")
        os.environ["AUTH_DEV_SECRET"] = "customer-service-test-secret-min-32-bytes"
        database.reset_engine_for_tests()
        database.init_db()
        session = database.get_session()
        try:
            seed_data.seed(session)
            self.order = session.query(Order).filter_by(status="delivered").first()
            self.order_id = self.order.id
            self.customer_id = self.order.customer_id
        finally:
            session.close()

    def tearDown(self) -> None:
        database.reset_engine_for_tests("sqlite+pysqlite:///:memory:")
        for suffix in ("", "-wal", "-shm"):
            path = f"{self.db_path}{suffix}"
            if os.path.exists(path):
                os.remove(path)

    def _headers(self, role: str, verification: str | None = None, idem: str | None = None):
        headers = {"Authorization": f"Bearer {create_dev_jwt(role + '-user', role)}"}
        if verification:
            headers["X-Identity-Verification"] = verification
        if idem:
            headers["Idempotency-Key"] = idem
        return headers

    def _verification(self, client: TestClient) -> str:
        req = client.post(
            "/api/auth/otp/request",
            json={"customer_id": self.customer_id, "order_id": self.order_id, "channel": "email"},
        )
        self.assertEqual(req.status_code, 200, req.text)
        data = req.json()
        ver = client.post("/api/auth/otp/verify", json={"challenge_id": data["challenge_id"], "code": data["dev_code"]})
        self.assertEqual(ver.status_code, 200, ver.text)
        return ver.json()["verification_token"]

    def test_fastapi_order_return_ticket_and_survey_paths(self):
        import order_api

        with TestClient(order_api.app) as client:
            verification = self._verification(client)

            search = client.get(
                "/api/orders/search", params={"q": self.order_id}, headers=self._headers("order_inquiry")
            )
            self.assertEqual(search.status_code, 200, search.text)
            self.assertIn("***", search.json()["data"][0]["customer_email"])

            detail_missing = client.get(f"/api/orders/{self.order_id}", headers=self._headers("order_inquiry"))
            self.assertEqual(detail_missing.status_code, 401)

            detail = client.get(f"/api/orders/{self.order_id}", headers=self._headers("order_inquiry", verification))
            self.assertEqual(detail.status_code, 200, detail.text)
            self.assertNotIn("***", detail.json()["customer_email"])

            ret = client.post(
                "/api/returns",
                params={
                    "order_id": self.order_id,
                    "type": "refund",
                    "reason": "质量问题",
                    "customer_id": self.customer_id,
                },
                headers=self._headers("after_sales", verification, "return-api-key"),
            )
            self.assertEqual(ret.status_code, 201, ret.text)
            replay = client.post(
                "/api/returns",
                params={
                    "order_id": self.order_id,
                    "type": "refund",
                    "reason": "质量问题",
                    "customer_id": self.customer_id,
                },
                headers=self._headers("after_sales", verification, "return-api-key"),
            )
            self.assertEqual(replay.status_code, 201)
            self.assertEqual(replay.json()["id"], ret.json()["id"])

            illegal = client.patch(
                f"/api/returns/{ret.json()['id']}",
                params={"status": "completed"},
                headers=self._headers("after_sales", verification, "return-illegal-key"),
            )
            self.assertEqual(illegal.status_code, 409)

            ticket = client.post(
                "/api/tickets",
                params={
                    "title": "API工单",
                    "description": "测试",
                    "customer_id": self.customer_id,
                    "order_id": self.order_id,
                },
                headers=self._headers("work_order", verification, "ticket-api-key"),
            )
            self.assertEqual(ticket.status_code, 201, ticket.text)
            assigned = client.patch(
                f"/api/tickets/{ticket.json()['id']}",
                params={"status": "assigned", "assignee": "王主管"},
                headers=self._headers("work_order", verification, "ticket-assign-key"),
            )
            self.assertEqual(assigned.status_code, 200, assigned.text)
            self.assertEqual(assigned.json()["status"], "assigned")

            survey = client.post(
                "/api/surveys",
                params={
                    "rating": 2,
                    "feedback": "希望更快",
                    "customer_id": self.customer_id,
                    "order_id": self.order_id,
                },
                headers=self._headers("work_order", verification, "survey-api-key"),
            )
            self.assertEqual(survey.status_code, 201, survey.text)

    def test_ready_and_v2_json_write_paths(self):
        import order_api

        with TestClient(order_api.app) as client:
            ready = client.get("/api/ready")
            self.assertEqual(ready.status_code, 200, ready.text)
            checks = ready.json()["checks"]
            self.assertEqual(checks["database"]["status"], "ok")
            self.assertEqual(checks["configuration"]["status"], "ok")

            verification = self._verification(client)
            ticket = client.post(
                "/api/v2/tickets",
                json={
                    "title": "JSON API ticket",
                    "description": "created through body payload",
                    "customer_id": self.customer_id,
                    "order_id": self.order_id,
                },
                headers=self._headers("work_order", verification, "ticket-json-key"),
            )
            self.assertEqual(ticket.status_code, 201, ticket.text)
            self.assertEqual(ticket.json()["title"], "JSON API ticket")

            ret = client.post(
                "/api/v2/returns",
                json={
                    "order_id": self.order_id,
                    "type": "refund",
                    "reason": "quality",
                    "customer_id": self.customer_id,
                },
                headers=self._headers("after_sales", verification, "return-json-key"),
            )
            self.assertEqual(ret.status_code, 201, ret.text)
            self.assertTrue(ret.json()["return_number"].startswith("RMA-"))

    def test_alembic_upgrade_then_seed_on_sqlite(self):
        fd, path = tempfile.mkstemp(prefix="customer-alembic-", suffix=".db")
        os.close(fd)
        old_url = os.environ["DATABASE_URL"]
        try:
            os.environ["DATABASE_URL"] = "sqlite+pysqlite:///" + path.replace("\\", "/")
            cfg = Config(os.path.join(os.path.dirname(os.path.dirname(__file__)), "alembic.ini"))
            command.upgrade(cfg, "head")
            database.reset_engine_for_tests()
            session = database.get_session()
            try:
                seed_data.seed(session)
                self.assertGreater(session.query(Order).count(), 0)
            finally:
                session.close()
        finally:
            os.environ["DATABASE_URL"] = old_url
            database.reset_engine_for_tests()
            for suffix in ("", "-wal", "-shm"):
                p = f"{path}{suffix}"
                if os.path.exists(p):
                    os.remove(p)

    def test_ready_returns_503_when_db_down(self):
        """When DB is unreachable, /api/ready must return 503 (not 200)."""
        from unittest.mock import patch

        from sqlalchemy.exc import OperationalError

        import order_api

        with TestClient(order_api.app) as client:
            with patch("order_api.database.get_session") as mock_get:
                mock_session = mock_get.return_value
                mock_session.execute.side_effect = OperationalError("SELECT 1", {}, Exception("database is down"))
                resp = client.get("/api/ready")
                self.assertEqual(resp.status_code, 503)
                self.assertEqual(resp.json()["status"], "degraded")
                self.assertEqual(resp.json()["checks"]["database"]["status"], "failed")


if __name__ == "__main__":
    unittest.main(verbosity=2)
