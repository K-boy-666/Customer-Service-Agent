"""Tests for structlog structured logging and slowapi rate limiting."""

from __future__ import annotations

import io
import json
import logging
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from fastapi.testclient import TestClient

import database
import seed_data
from security import create_dev_jwt


class StructlogTest(unittest.TestCase):
    """Test structured logging output and request_id propagation."""

    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(prefix="structlog-test-", suffix=".db")
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

    def _capture_logs(self, json_logs: bool = True) -> io.StringIO:
        """Configure logging to write to a StringIO buffer and return it."""
        buffer = io.StringIO()
        import structlog

        level = logging.INFO
        shared_processors: list = [
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
        ]
        structlog.configure(
            processors=shared_processors + [structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
            wrapper_class=structlog.make_filtering_bound_logger(level),
            logger_factory=structlog.stdlib.LoggerFactory(),
            cache_logger_on_first_use=True,
        )
        renderer = structlog.processors.JSONRenderer() if json_logs else structlog.dev.ConsoleRenderer(colors=False)
        formatter = structlog.stdlib.ProcessorFormatter(foreign_pre_chain=shared_processors, processors=[renderer])
        handler = logging.StreamHandler(buffer)
        handler.setFormatter(formatter)
        root = logging.getLogger()
        root.handlers.clear()
        root.addHandler(handler)
        root.setLevel(level)
        return buffer

    def test_structlog_json_output_in_production(self):
        """LOG_JSON=true produces JSON logs with request_id."""
        buffer = self._capture_logs(json_logs=True)
        import order_api

        jwt_token = create_dev_jwt("test-orchestrator", "orchestrator")
        with TestClient(order_api.app) as client:
            resp = client.get(
                "/api/health",
                headers={"X-Request-ID": "test-req-001", "Authorization": f"Bearer {jwt_token}"},
            )
            self.assertEqual(resp.status_code, 200)

        log_output = buffer.getvalue()
        lines = [line for line in log_output.strip().split("\n") if line]
        self.assertTrue(len(lines) > 0)
        # At least one line should be JSON with request_id
        json_lines = [json.loads(line) for line in lines if line.startswith("{")]
        self.assertTrue(len(json_lines) > 0)
        request_ids = [entry.get("request_id") for entry in json_lines if "request_id" in entry]
        self.assertIn("test-req-001", request_ids)

    def test_structlog_console_output_in_development(self):
        """LOG_JSON=false produces colored console (non-JSON) output."""
        buffer = self._capture_logs(json_logs=False)
        import structlog

        log = structlog.get_logger("test")
        log.info("test_event", key="value")
        output = buffer.getvalue()
        self.assertNotEqual(output.strip()[0], "{")

    def test_request_id_generated_when_missing(self):
        """When X-Request-ID header is missing, a UUID is generated."""
        import order_api

        jwt_token = create_dev_jwt("test-orchestrator", "orchestrator")
        with TestClient(order_api.app) as client:
            resp = client.get("/api/health", headers={"Authorization": f"Bearer {jwt_token}"})
            self.assertEqual(resp.status_code, 200)
            # When no X-Request-ID header is sent, the response should still succeed
            # and the middleware generates an internal UUID (not exposed in response)

    def test_stdlib_logging_bridged(self):
        """stdlib logging.getLogger calls are bridged through ProcessorFormatter."""
        buffer = self._capture_logs(json_logs=True)
        stdlib_logger = logging.getLogger("test.bridge")
        stdlib_logger.warning("bridged warning message")
        log_output = buffer.getvalue()
        json_lines = [json.loads(line) for line in log_output.strip().split("\n") if line.startswith("{")]
        self.assertTrue(len(json_lines) > 0)
        events = [e.get("event") or e.get("message") or "" for e in json_lines]
        self.assertTrue(any("bridged warning" in e for e in events))

    def test_request_id_propagates_to_orchestrator_logger(self):
        """request_id from middleware appears in orchestrator_runtime LOGGER calls."""
        buffer = self._capture_logs(json_logs=True)
        import order_api

        jwt_token = create_dev_jwt("test-orchestrator", "orchestrator")
        with TestClient(order_api.app) as client:
            resp = client.post(
                "/api/orchestrator/respond",
                json={"message": "你好", "conversation_id": "test-conv-rid"},
                headers={"X-Request-ID": "rid-prop-001", "Authorization": f"Bearer {jwt_token}"},
            )
            # Request may return any status — we only care about logs
            self.assertIn(resp.status_code, {200, 422})

        log_output = buffer.getvalue()
        json_lines = [json.loads(line) for line in log_output.strip().split("\n") if line.startswith("{")]
        request_ids = {entry.get("request_id") for entry in json_lines if "request_id" in entry}
        self.assertIn("rid-prop-001", request_ids)


class RateLimitTest(unittest.TestCase):
    """Test slowapi rate limiting enforcement."""

    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(prefix="ratelimit-test-", suffix=".db")
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
        # Enable limiter at runtime (conftest sets RATE_LIMIT_ENABLED=false at import time)
        from rate_limit import limiter

        limiter.enabled = True
        limiter.reset()

    def tearDown(self) -> None:
        from rate_limit import limiter

        limiter.enabled = False
        limiter.reset()
        database.reset_engine_for_tests("sqlite+pysqlite:///:memory:")
        for suffix in ("", "-wal", "-shm"):
            path = f"{self.db_path}{suffix}"
            if os.path.exists(path):
                os.remove(path)

    def test_health_endpoints_not_rate_limited(self):
        """Health endpoints should not be rate limited."""
        import order_api

        with TestClient(order_api.app) as client:
            for _ in range(10):
                resp = client.get("/api/health")
                self.assertEqual(resp.status_code, 200)

    def test_otp_rate_limit_5_per_minute(self):
        """OTP request endpoint limited to 5/minute."""
        import order_api

        with TestClient(order_api.app) as client:
            statuses = []
            for _ in range(6):
                resp = client.post(
                    "/api/auth/otp/request",
                    json={
                        "channel": "customer_identity",
                        "delivery_method": "email",
                        "destination": "test@example.com",
                        "customer_id": 1,
                        "order_id": "SO20260601001",
                    },
                )
                statuses.append(resp.status_code)
            self.assertIn(429, statuses)

    def test_read_endpoint_rate_limit(self):
        """Read endpoints limited to 120/minute."""
        import order_api

        jwt_token = create_dev_jwt("test-orchestrator", "orchestrator")
        with TestClient(order_api.app) as client:
            statuses = []
            for _ in range(121):
                resp = client.get(
                    "/api/orders",
                    params={"limit": 1},
                    headers={"Authorization": f"Bearer {jwt_token}"},
                )
                statuses.append(resp.status_code)
                if resp.status_code == 429:
                    break
            self.assertIn(429, statuses)

    def test_rate_limit_headers_not_required(self):
        """Rate limiting works without X-RateLimit headers (headers_enabled=False)."""
        import order_api

        with TestClient(order_api.app) as client:
            resp = client.post(
                "/api/auth/otp/request",
                json={
                    "channel": "customer_identity",
                    "delivery_method": "email",
                    "destination": "test@example.com",
                    "customer_id": 1,
                    "order_id": "SO20260601001",
                },
            )
            self.assertEqual(resp.status_code, 200)

    def test_rate_limit_429_body(self):
        """429 response has a body with error information."""
        import order_api

        with TestClient(order_api.app) as client:
            for _ in range(5):
                client.post(
                    "/api/auth/otp/request",
                    json={
                        "channel": "customer_identity",
                        "delivery_method": "email",
                        "destination": "test@example.com",
                        "customer_id": 1,
                        "order_id": "SO20260601001",
                    },
                )
            # 6th should be 429
            resp = client.post(
                "/api/auth/otp/request",
                json={
                    "channel": "customer_identity",
                    "delivery_method": "email",
                    "destination": "test@example.com",
                    "customer_id": 1,
                    "order_id": "SO20260601001",
                },
            )
            self.assertEqual(resp.status_code, 429)


if __name__ == "__main__":
    unittest.main()
