"""Tests for prometheus_client-based /api/metrics endpoint output format."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from fastapi.testclient import TestClient
from prometheus_client import CONTENT_TYPE_LATEST

import database
import seed_data
from security import create_dev_jwt


class MetricsPrometheusFormatTest(unittest.TestCase):
    """Test that /api/metrics outputs standard Prometheus exposition format."""

    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(prefix="prom-metrics-", suffix=".db")
        os.close(fd)
        os.environ["DATABASE_URL"] = "sqlite+pysqlite:///" + self.db_path.replace("\\", "/")
        os.environ["AUTH_DEV_SECRET"] = "customer-service-test-secret-min-32-bytes"
        os.environ["RATE_LIMIT_ENABLED"] = "false"
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

    def test_metrics_content_type(self):
        """Metrics endpoint returns Prometheus-standard content type."""
        import order_api

        with TestClient(order_api.app) as client:
            resp = client.get("/api/metrics")
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.headers["content-type"], CONTENT_TYPE_LATEST)

    def test_metrics_includes_all_expected_metric_families(self):
        """All expected metric families appear in the /api/metrics output."""
        import order_api

        jwt_token = create_dev_jwt("test-orchestrator", "orchestrator")
        with TestClient(order_api.app) as client:
            # Generate some traffic for histogram data
            client.get("/api/orders", headers={"Authorization": f"Bearer {jwt_token}"})
            resp = client.get("/api/metrics")
            text = resp.text

            # Histogram metric
            self.assertIn("# HELP http_request_duration_seconds", text)
            self.assertIn("# TYPE http_request_duration_seconds histogram", text)
            self.assertIn("http_request_duration_seconds_bucket", text)
            self.assertIn("http_request_duration_seconds_sum", text)
            self.assertIn("http_request_duration_seconds_count", text)

            # DB-count gauge metrics (all should be gauge type now)
            for metric_name in (
                "customer_service_conversations_total",
                "customer_service_handoffs_total",
                "customer_service_tickets_total",
                "customer_service_returns_total",
                "customer_service_surveys_total",
            ):
                self.assertIn(metric_name, text)
                self.assertIn(f"# TYPE {metric_name} gauge", text)

    def test_metrics_histogram_uses_standard_buckets(self):
        """Histogram uses the expected bucket boundaries."""
        import order_api

        with TestClient(order_api.app) as client:
            client.get("/api/health")
            resp = client.get("/api/metrics")
            text = resp.text
            for le in ("0.05", "0.1", "0.25", "0.5", "1.0", "2.5", "5.0", "10.0", "+Inf"):
                self.assertIn(f'le="{le}"', text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
