"""Tests for the profit-dashboard API (Task 9 — cs-profit-engine).

Covers SubTask 9.1 – 9.5: the three v1 dashboard endpoints
(``/api/v1/profit-dashboard``, ``/api/v1/recommendations/funnel``,
``/api/v1/attributions``), their auth requirement, response-time SLA
(< 2s), and Prometheus metric instrumentation.

Uses a temp-file SQLite database (mirroring ``test_orchestrator_e2e.py``
and ``test_metrics_prometheus.py``) so the FastAPI ``TestClient`` can
share the engine across request threads. Each test plants its own
profit-engine data (UserProfile / Order / TouchPoint / Recommendation /
FunnelEvent / CustomerServiceUsageEvent / SatisfactionSurvey) because
``seed_data.seed`` only populates the original order-management tables.

Test list (per SubTask 9.5 spec):
1. ``test_profit_dashboard_returns_kpi`` — KPI block present with all keys.
2. ``test_profit_dashboard_returns_revenue`` — revenue block present.
3. ``test_profit_dashboard_returns_insights`` — insights block present.
4. ``test_profit_dashboard_respects_time_range`` — empty window returns
   zeroed KPIs.
5. ``test_profit_dashboard_response_time_under_2s`` — latency < 2s.
6. ``test_recommendations_funnel_returns_stages`` — four stages present.
7. ``test_recommendations_funnel_conversion_rates`` — rates computed
   correctly from planted funnel events.
8. ``test_attributions_list_with_model_param`` — model param switches
   the records returned.
9. ``test_attributions_list_with_user_filter`` — user_id filter works.
10. ``test_attributions_summary_multi_model`` — summary contains all
    four attribution models.
11. ``test_dashboard_requires_auth`` — no auth → 401; wrong role → 403.
12. ``test_metrics_recorded`` — Prometheus latency histogram observed.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from datetime import timedelta

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import attribution_service as attr
import database
import seed_data
from fastapi.testclient import TestClient
from models import (
    CustomerServiceUsageEvent,
    FunnelEvent,
    Order,
    OrderItem,
    Recommendation,
    SatisfactionSurvey,
    TouchPoint,
    UserProfile,
    now,
)
from security import create_dev_jwt


class ProfitDashboardApiTest(unittest.TestCase):
    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(prefix="profit-dashboard-", suffix=".db")
        os.close(fd)
        os.environ["DATABASE_URL"] = "sqlite+pysqlite:///" + self.db_path.replace("\\", "/")
        os.environ["AUTH_DEV_SECRET"] = "customer-service-test-secret-min-32-bytes"
        os.environ["RATE_LIMIT_ENABLED"] = "false"
        database.reset_engine_for_tests()
        database.init_db()
        session = database.get_session()
        try:
            seed_data.seed(session)
            self._plant_profit_engine_data(session)
            session.commit()
        finally:
            session.close()

    def tearDown(self) -> None:
        database.reset_engine_for_tests("sqlite+pysqlite:///:memory:")
        for suffix in ("", "-wal", "-shm"):
            path = f"{self.db_path}{suffix}"
            if os.path.exists(path):
                os.remove(path)

    # -- data planting ---------------------------------------------------

    def _plant_profit_engine_data(self, session) -> None:
        """Plant attribution / recommendation / funnel / KPI rows.

        All timestamps use ``now()`` so a date window covering today
        captures every row. The planted data is deterministic so test
        assertions can rely on exact counts and amounts.
        """
        customer_id = 1
        user_id = "u_dash_test"

        # UserProfile links customer_id=1 → user_id so attribution can
        # reverse-lookup the user from the order's customer_id.
        session.add(UserProfile(user_id=user_id, primary_customer_id=customer_id))
        session.flush()

        # Fresh order at now() so the 24h attribution window covers the
        # touch points planted below.
        self.order_id = "ORD-DASH-001"
        order_time = now().isoformat()
        session.add(
            Order(
                id=self.order_id,
                order_number="SODASH001",
                customer_id=customer_id,
                status="pending",
                total_amount=1000.0,
                currency="CNY",
                shipping_address="dashboard-test-address",
                created_at=order_time,
                updated_at=order_time,
            )
        )
        # Plant an OrderItem so the order has a SKU context.
        session.add(
            OrderItem(
                order_id=self.order_id,
                sku="MOUSE-WL-02",
                name="无线鼠标",
                qty=1,
                price=230.0,
            )
        )
        session.flush()

        # Three touch points within 24h before the order, one per agent.
        conversion = attr._parse_datetime(order_time)
        self.touch_conversations = []
        for i, hours in enumerate((12, 6, 1)):
            conv_id = f"conv-dash-{i}"
            self.touch_conversations.append(conv_id)
            session.add(
                TouchPoint(
                    user_id=user_id,
                    conversation_id=conv_id,
                    agent_id=f"agent_{i}",
                    touch_time=conversion - timedelta(hours=hours),
                )
            )
        session.flush()

        # Attribute the order under all four models so the summary has
        # data for every model and the model-param test can switch.
        for model in attr.ATTRIBUTION_MODELS:
            attr.attribute_order(session, self.order_id, model=model)
        session.flush()

        # Recommendations — three rows so top_opportunities is non-empty.
        self.rec_ids = []
        for i in range(3):
            rec_id = f"rec_dash_{i}"
            self.rec_ids.append(rec_id)
            session.add(
                Recommendation(
                    recommendation_id=rec_id,
                    user_id=user_id,
                    conversation_id=f"conv-dash-{i}",
                    recommend_type="cross_sell",
                    target_ref=f"SKU-DASH-{i}",
                    content="reason",
                    script=f"话术 {i}",
                    expected_conversion_rate=0.4,
                    opportunity_score=0.7 + i * 0.05,
                    status="pending",
                )
            )
        session.flush()

        # Funnel events — one full pass through all four stages for
        # rec_dash_0, plus five extra exposure events across recs so
        # the conversion rates are non-trivial.
        for event_type in ("exposure", "click", "consult", "order"):
            session.add(
                FunnelEvent(
                    recommendation_id="rec_dash_0",
                    user_id=user_id,
                    session_id="sess-dash",
                    event_type=event_type,
                    order_id=self.order_id if event_type == "order" else None,
                    payload={},
                )
            )
        for i in range(5):
            session.add(
                FunnelEvent(
                    recommendation_id=f"rec_dash_{i % 3}",
                    user_id=user_id,
                    session_id="sess-dash",
                    event_type="exposure",
                    payload={},
                )
            )
        session.flush()

        # CustomerServiceUsageEvents — 5 conversations, 3 success.
        for i in range(5):
            session.add(
                CustomerServiceUsageEvent(
                    conversation_id=f"conv-usage-{i}",
                    customer_id=customer_id,
                    order_id=self.order_id if i == 0 else None,
                    status="success" if i < 3 else "failed",
                    emotional_level="L1",
                    message_length=100,
                    intents=[],
                    dispatched_agents=[],
                    tool_calls=[],
                    needs_human=0,
                )
            )
        session.flush()

        # SatisfactionSurveys — 5 ratings (avg = 4.2).
        for i, rating in enumerate([5, 4, 5, 3, 4]):
            session.add(
                SatisfactionSurvey(
                    survey_number=f"SAT-DASH-{i}",
                    customer_id=customer_id,
                    order_id=None,
                    rating=rating,
                    feedback_text="",
                )
            )
        session.flush()

        self.user_id = user_id

    # -- helpers ---------------------------------------------------------

    def _client(self):
        import order_api

        return TestClient(order_api.app)

    def _auth_header(self, role: str = "analytics") -> dict:
        token = create_dev_jwt("test-user", role)
        return {"Authorization": f"Bearer {token}"}

    def _date_range(self) -> tuple[str, str]:
        """Return a wide date range covering all planted data."""
        start = (now() - timedelta(days=7)).strftime("%Y-%m-%d")
        end = (now() + timedelta(days=1)).strftime("%Y-%m-%d")
        return start, end

    # ------------------------------------------------------------------
    # SubTask 9.1 — profit-dashboard endpoint
    # ------------------------------------------------------------------

    def test_profit_dashboard_returns_kpi(self) -> None:
        start, end = self._date_range()
        with self._client() as client:
            resp = client.get(
                "/api/v1/profit-dashboard",
                params={"start": start, "end": end},
                headers=self._auth_header(),
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertIn("kpi", body)
        kpi = body["kpi"]
        # All four KPI keys present.
        for key in (
            "response_time_avg_seconds",
            "resolution_rate",
            "csat_avg",
            "total_conversations",
        ):
            self.assertIn(key, kpi, f"missing KPI key: {key}")
        # Planted data: 5 usage events → 5 distinct conversations.
        self.assertGreaterEqual(kpi["total_conversations"], 5)
        # 3 of 5 events have status="success" → resolution_rate ≈ 0.6.
        self.assertGreater(kpi["resolution_rate"], 0.0)
        # CSAT average must be a valid score in [1, 5]. The wide date
        # range also captures surveys planted by ``seed_data.seed``, so
        # the exact value is not asserted — only that the KPI is computed
        # from real DB rows (non-zero) and stays within the valid range.
        self.assertGreater(kpi["csat_avg"], 0.0)
        self.assertLessEqual(kpi["csat_avg"], 5.0)

    def test_profit_dashboard_returns_revenue(self) -> None:
        start, end = self._date_range()
        with self._client() as client:
            resp = client.get(
                "/api/v1/profit-dashboard",
                params={"start": start, "end": end, "model": "last_touch"},
                headers=self._auth_header(),
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertIn("revenue", body)
        rev = body["revenue"]
        for key in ("attributed_revenue", "service_cost", "roi", "conversion_rate"):
            self.assertIn(key, rev, f"missing revenue key: {key}")
        # service_cost has human / ai / total sub-keys.
        for key in ("human", "ai", "total"):
            self.assertIn(key, rev["service_cost"])
        # last_touch attribution credits the full 1000.0 to the latest
        # touch point, so attributed_revenue = 1000.0.
        self.assertAlmostEqual(rev["attributed_revenue"], 1000.0)
        # conversion_rate = attributed_orders / total_conversations > 0.
        self.assertGreater(rev["conversion_rate"], 0.0)

    def test_profit_dashboard_returns_insights(self) -> None:
        start, end = self._date_range()
        with self._client() as client:
            resp = client.get(
                "/api/v1/profit-dashboard",
                params={"start": start, "end": end},
                headers=self._auth_header(),
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertIn("insights", body)
        insights = body["insights"]
        for key in ("top_agents", "top_scripts", "top_opportunities"):
            self.assertIn(key, insights, f"missing insights key: {key}")
        # Three touch points → three agents in the ranking (last_touch
        # credits only agent_2, but the others may still appear with 0).
        self.assertIsInstance(insights["top_agents"], list)
        # top_opportunities comes from the 3 planted Recommendations.
        opp_list = insights["top_opportunities"]
        self.assertIsInstance(opp_list, list)
        self.assertGreater(len(opp_list), 0)
        for opp in opp_list:
            self.assertIn("target_sku", opp)
            self.assertIn("opportunity_score", opp)
            self.assertIn("count", opp)

    def test_profit_dashboard_respects_time_range(self) -> None:
        # Use a 2020 window — no planted data falls in this range.
        with self._client() as client:
            resp = client.get(
                "/api/v1/profit-dashboard",
                params={"start": "2020-01-01", "end": "2020-01-02"},
                headers=self._auth_header(),
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["kpi"]["total_conversations"], 0)
        self.assertEqual(body["kpi"]["resolution_rate"], 0.0)
        self.assertEqual(body["kpi"]["csat_avg"], 0.0)
        self.assertEqual(body["revenue"]["attributed_revenue"], 0.0)
        self.assertEqual(body["insights"]["top_agents"], [])
        self.assertEqual(body["insights"]["top_opportunities"], [])

    def test_profit_dashboard_response_time_under_2s(self) -> None:
        start, end = self._date_range()
        with self._client() as client:
            t0 = time.monotonic()
            resp = client.get(
                "/api/v1/profit-dashboard",
                params={"start": start, "end": end},
                headers=self._auth_header(),
            )
            elapsed = time.monotonic() - t0
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertLess(elapsed, 2.0, f"dashboard latency {elapsed:.3f}s exceeds 2s SLA")

    # ------------------------------------------------------------------
    # SubTask 9.2 — recommendations/funnel endpoint
    # ------------------------------------------------------------------

    def test_recommendations_funnel_returns_stages(self) -> None:
        start, end = self._date_range()
        with self._client() as client:
            resp = client.get(
                "/api/v1/recommendations/funnel",
                params={"start": start, "end": end},
                headers=self._auth_header(),
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertIn("stages", body)
        stages = body["stages"]
        # Exactly four stages, in spec order.
        self.assertEqual([s["stage"] for s in stages], ["exposure", "click", "consult", "order"])
        # Planted: 6 exposure (1 full pass + 5 extra), 1 click, 1 consult, 1 order.
        counts = {s["stage"]: s["count"] for s in stages}
        self.assertEqual(counts["exposure"], 6)
        self.assertEqual(counts["click"], 1)
        self.assertEqual(counts["consult"], 1)
        self.assertEqual(counts["order"], 1)

    def test_recommendations_funnel_conversion_rates(self) -> None:
        start, end = self._date_range()
        with self._client() as client:
            resp = client.get(
                "/api/v1/recommendations/funnel",
                params={"start": start, "end": end},
                headers=self._auth_header(),
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        rates = body["conversion_rates"]
        for key in (
            "exposure_to_click",
            "click_to_consult",
            "consult_to_order",
            "overall",
        ):
            self.assertIn(key, rates)
        # exposure=6, click=1 → 1/6.
        self.assertAlmostEqual(rates["exposure_to_click"], 1.0 / 6.0)
        # click=1, consult=1 → 1.0.
        self.assertAlmostEqual(rates["click_to_consult"], 1.0)
        # consult=1, order=1 → 1.0.
        self.assertAlmostEqual(rates["consult_to_order"], 1.0)
        # overall = order / exposure = 1/6.
        self.assertAlmostEqual(rates["overall"], 1.0 / 6.0)

    # ------------------------------------------------------------------
    # SubTask 9.3 — attributions endpoint
    # ------------------------------------------------------------------

    def test_attributions_list_with_model_param(self) -> None:
        start, end = self._date_range()
        with self._client() as client:
            resp_last = client.get(
                "/api/v1/attributions",
                params={"start": start, "end": end, "model": "last_touch"},
                headers=self._auth_header(),
            )
            resp_first = client.get(
                "/api/v1/attributions",
                params={"start": start, "end": end, "model": "first_touch"},
                headers=self._auth_header(),
            )
        self.assertEqual(resp_last.status_code, 200, resp_last.text)
        self.assertEqual(resp_first.status_code, 200, resp_first.text)
        last_records = resp_last.json()["records"]
        first_records = resp_first.json()["records"]
        # Both models attribute the same 3 touch points.
        self.assertEqual(len(last_records), 3)
        self.assertEqual(len(first_records), 3)
        # All records match the requested model.
        for r in last_records:
            self.assertEqual(r["model"], "last_touch")
        for r in first_records:
            self.assertEqual(r["model"], "first_touch")
        # The attributed_amount distribution differs: last_touch credits
        # only the latest touch point, first_touch only the earliest.
        last_amounts = sorted(r["attributed_amount"] for r in last_records)
        first_amounts = sorted(r["attributed_amount"] for r in first_records)
        # Both models credit the full 1000.0 to exactly one touch point.
        self.assertAlmostEqual(max(last_amounts), 1000.0)
        self.assertAlmostEqual(max(first_amounts), 1000.0)
        # The remaining touch points receive 0.0.
        self.assertAlmostEqual(min(last_amounts), 0.0)
        self.assertAlmostEqual(min(first_amounts), 0.0)

    def test_attributions_list_with_user_filter(self) -> None:
        start, end = self._date_range()
        with self._client() as client:
            resp = client.get(
                "/api/v1/attributions",
                params={
                    "start": start,
                    "end": end,
                    "model": "last_touch",
                    "user_id": self.user_id,
                },
                headers=self._auth_header(),
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        records = resp.json()["records"]
        self.assertGreater(len(records), 0)
        for r in records:
            self.assertEqual(r["user_id"], self.user_id)

        # Filter by a non-existent user → empty records.
        with self._client() as client:
            resp_empty = client.get(
                "/api/v1/attributions",
                params={
                    "start": start,
                    "end": end,
                    "model": "last_touch",
                    "user_id": "u_does_not_exist",
                },
                headers=self._auth_header(),
            )
        self.assertEqual(resp_empty.status_code, 200, resp_empty.text)
        self.assertEqual(resp_empty.json()["records"], [])

    def test_attributions_summary_multi_model(self) -> None:
        start, end = self._date_range()
        with self._client() as client:
            resp = client.get(
                "/api/v1/attributions",
                params={"start": start, "end": end},
                headers=self._auth_header(),
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        summary = resp.json()["summary"]
        # Summary must include all four attribution models.
        self.assertEqual(set(summary["models"].keys()), set(attr.ATTRIBUTION_MODELS))
        # Each model attributed the same 1000.0 (full order amount).
        for model in attr.ATTRIBUTION_MODELS:
            entry = summary["models"][model]
            self.assertIn("attributed_revenue", entry)
            self.assertIn("record_count", entry)
            self.assertEqual(entry["record_count"], 3)
            self.assertAlmostEqual(entry["attributed_revenue"], 1000.0)
        # Cross-model de-duplication: 1 distinct order, 1000.0 total revenue.
        self.assertEqual(summary["total_orders"], 1)
        self.assertAlmostEqual(summary["total_revenue"], 1000.0)

    # ------------------------------------------------------------------
    # Auth requirement
    # ------------------------------------------------------------------

    def test_dashboard_requires_auth(self) -> None:
        start, end = self._date_range()
        with self._client() as client:
            # No Authorization header → 401 (missing_bearer_token).
            resp_no_auth = client.get(
                "/api/v1/profit-dashboard",
                params={"start": start, "end": end},
            )
            # orchestrator role lacks analytics:read → 403.
            resp_wrong_role = client.get(
                "/api/v1/profit-dashboard",
                params={"start": start, "end": end},
                headers=self._auth_header(role="orchestrator"),
            )
        self.assertEqual(resp_no_auth.status_code, 401)
        self.assertEqual(resp_wrong_role.status_code, 403)

    # ------------------------------------------------------------------
    # Prometheus metrics
    # ------------------------------------------------------------------

    def test_metrics_recorded(self) -> None:
        start, end = self._date_range()
        with self._client() as client:
            # Hit all three dashboard endpoints so each label is observed.
            client.get(
                "/api/v1/profit-dashboard",
                params={"start": start, "end": end},
                headers=self._auth_header(),
            )
            client.get(
                "/api/v1/recommendations/funnel",
                params={"start": start, "end": end},
                headers=self._auth_header(),
            )
            client.get(
                "/api/v1/attributions",
                params={"start": start, "end": end},
                headers=self._auth_header(),
            )
            # Scrape /api/metrics — no auth required (mirrors
            # test_metrics_prometheus.py).
            resp = client.get("/api/metrics")

        self.assertEqual(resp.status_code, 200, resp.text)
        text = resp.text
        # The latency histogram must appear with all three endpoint labels.
        self.assertIn("# HELP dashboard_latency_seconds", text)
        self.assertIn("# TYPE dashboard_latency_seconds histogram", text)
        for endpoint in (
            "/api/v1/profit-dashboard",
            "/api/v1/recommendations/funnel",
            "/api/v1/attributions",
        ):
            self.assertIn(f'endpoint="{endpoint}"', text)
        # The attribution revenue counter must appear with the model label.
        self.assertIn("# HELP attribution_revenue_total", text)
        self.assertIn("# TYPE attribution_revenue_total counter", text)
        self.assertIn('model="last_touch"', text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
