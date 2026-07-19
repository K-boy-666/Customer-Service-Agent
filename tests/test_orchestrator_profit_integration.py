"""Integration tests for the Orchestrator profit-engine hooks (Task 6).

Covers SubTask 6.1 – 6.6: async demand-mining hook, synchronous
recommendation generation with 2s timeout, async attribution touch-point
recording, dispatcher routing of recommendation / analytics intents,
mining-result persistence to the usage event's ``intents`` JSON field
(SubTask 6.5 option B), and the guarantee that hook failures never break
the main customer response.

Uses a shared-cache in-memory SQLite database (mirroring
``test_demand_mining_service.py``) so the ThreadPoolExecutor worker
threads can reach the same in-memory DB as the test's main thread. A
sentinel connection is held open for the test's lifetime to keep the
in-memory DB alive across ``database.reset_engine_for_tests`` cycles.

Test list (per SubTask 6.6 spec):
1. ``test_demand_mining_hook_does_not_block_response`` — mining timeout
   returns control to the main response within the 2s budget.
2. ``test_recommendation_generated_when_opportunity_high`` — vip user +
   product inquiry → recommendations surfaced & persisted.
3. ``test_recommendation_skipped_when_opportunity_low`` — low-tier user
   + product inquiry → no recommendations (opportunity_score ≤ 0.6).
4. ``test_attribution_recorded_after_order`` — touch point recorded and
   attribution row written when the order falls in the 24h window.
5. ``test_mining_result_written_to_conversation_state`` — mining result
   appended to ``customer_service_usage_events.intents`` JSON field.
6. ``test_dispatcher_routes_recommendation_intent`` — dispatcher detects
   the recommendation intent from cross-sell / up-sell keywords.
7. ``test_dispatcher_routes_analytics_intent`` — dispatcher detects the
   analytics intent from attribution / ROI keywords.
8. ``test_hook_failure_does_not_break_response`` — exception in hook
   submission is caught; main response is returned unchanged.
"""

from __future__ import annotations

import os
import sys
import time
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from sqlalchemy import create_engine

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import database
import profit_engine_hooks
import seed_data
from dispatcher import RuleBasedIntentDispatcher
from models import (
    AttributionRecord,
    CustomerServiceUsageEvent,
    Order,
    OrderItem,
    Recommendation,
    TouchPoint,
    UserProfile,
    UserValueScore,
)
from orchestrator_api import respond_to_customer_message
from orchestrator_runtime import _CONVERSATION_STATES
from security import Actor, load_verification, request_otp, verify_otp

# Shared-cache in-memory SQLite URI — distinct namespace from the other
# profit-engine test files so each module gets its own in-memory DB.
IN_MEMORY_URL = "sqlite+pysqlite:///file:orch_profit_integration_test?mode=memory&cache=shared&uri=true"

# The order planted by seed_data that contains LAPTOP-BAG-01 + MOUSE-WL-02
# (customer_id=1 / 张三). Used as the source-order context for mining.
# seed_data.ORDER_SPECS[0] is (cust_idx=0, day_off=3, "delivered",
# [("LAPTOP-BAG-01", 2), ("MOUSE-WL-02", 4)]). Order ID depends on TODAY
# (seed_data.TODAY), so we compute it dynamically instead of hard-coding
# a date that ages out as the system clock advances.
SEEDED_ORDER_ID = seed_data._order_id(3, 1)
SEEDED_CUSTOMER_ID = 1


class OrchestratorProfitIntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        os.environ["DATABASE_URL"] = IN_MEMORY_URL
        os.environ["AUTH_DEV_SECRET"] = "customer-service-test-secret-min-32-bytes"
        # Sentinel connection keeps the shared in-memory DB alive for the test.
        self._sentinel_engine = create_engine(IN_MEMORY_URL)
        self._sentinel_conn = self._sentinel_engine.connect()
        database.reset_engine_for_tests()
        _CONVERSATION_STATES.clear()
        # Shut down any executor leftover from a previous test so the new
        # engine is the only one the worker threads bind to.
        profit_engine_hooks.shutdown_executor_for_tests()
        database.init_db()
        session = database.get_session()
        try:
            seed_data.seed(session)
            session.commit()
        finally:
            session.close()

    def tearDown(self) -> None:
        # Drain pending hook tasks before disposing the engine so worker
        # threads do not touch a disposed engine.
        profit_engine_hooks.shutdown_executor_for_tests()
        session = database.get_session()
        try:
            session.rollback()
        finally:
            session.close()
        _CONVERSATION_STATES.clear()
        self._sentinel_conn.close()
        self._sentinel_engine.dispose()
        database.reset_engine_for_tests("sqlite+pysqlite:///:memory:")

    # -- helpers -----------------------------------------------------------

    def _session(self):
        return database.get_session()

    def _make_vip_profile(self, customer_id: int = SEEDED_CUSTOMER_ID) -> str:
        """Create a UserProfile + UserValueScore (vip) for ``customer_id``.

        The ``user_id`` is ``str(customer_id)`` so it matches what
        ``orchestrator_api._run_profit_engine_hooks`` passes to the hooks
        (it derives ``user_id`` from ``str(payload["customer_id"])``).
        """
        session = self._session()
        try:
            user_id = str(customer_id)
            session.add(UserProfile(user_id=user_id, primary_customer_id=customer_id))
            session.flush()
            session.add(
                UserValueScore(
                    user_id=user_id,
                    score=90.0,
                    tier="vip",
                    rfm_r=100.0,
                    rfm_f=85.0,
                    rfm_m=75.0,
                    interaction_weight=80.0,
                )
            )
            session.commit()
            return user_id
        finally:
            session.close()

    def _make_fresh_order(
        self,
        *,
        order_id: str = "ORD-PROFIT-ATTR-001",
        customer_id: int = SEEDED_CUSTOMER_ID,
        total_amount: float = 500.0,
        created_at: datetime | None = None,
    ) -> str:
        """Create an Order whose ``created_at`` is just after ``now``.

        The 24-hour attribution window is
        ``[created_at - 24h, created_at]``. A touch point recorded at
        ``now`` falls inside the window only when ``created_at >= now``,
        so we default ``created_at`` to ``now + 1 minute`` to avoid
        sub-second race conditions.
        """
        if created_at is None:
            created_at = datetime.now() + timedelta(minutes=1)
        session = self._session()
        try:
            session.add(
                Order(
                    id=order_id,
                    order_number=f"SO{order_id.replace('-', '')}",
                    customer_id=customer_id,
                    status="pending",
                    total_amount=total_amount,
                    currency="CNY",
                    shipping_address="profit-engine-test-address",
                    created_at=created_at.isoformat(),
                    updated_at=created_at.isoformat(),
                )
            )
            # Plant one OrderItem so _extract_mentioned_skus returns a SKU
            # the mining service can use.
            session.add(
                OrderItem(
                    order_id=order_id,
                    sku="MOUSE-WL-02",
                    name="无线鼠标",
                    qty=1,
                    price=230.0,
                )
            )
            session.commit()
            return order_id
        finally:
            session.close()

    def _capture_attribution_futures(self) -> tuple[list, object]:
        """Return (captured_futures, patched_callable) for attribution hooks.

        The patch wraps ``profit_engine_hooks.run_attribution_async`` so
        each call appends the returned Future to ``captured_futures``.
        Tests then ``.result(timeout=...)`` on each Future to wait for
        the fire-and-forget hook to land its DB writes before asserting.
        """
        captured: list = []
        original = profit_engine_hooks.run_attribution_async

        def _capturing(*args, **kwargs):
            future = original(*args, **kwargs)
            captured.append(future)
            return future

        return captured, _capturing

    def _make_verification(self, customer_id: int = SEEDED_CUSTOMER_ID) -> object:
        """Create a verified OTP challenge and return the Verification.

        Passing ``order_id=None`` keeps the verification generic (not
        tied to a specific order) so the same verification can be reused
        across orders. Mirrors the pattern in ``test_orchestrator_e2e``.
        """
        session = self._session()
        try:
            challenge = request_otp(
                session,
                "customer_identity",
                "email",
                "test@example.com",
                customer_id,
                None,
            )
            verified = verify_otp(session, challenge["challenge_id"], challenge["dev_code"])
            session.commit()
            token = verified["verification_token"]
        finally:
            session.close()
        session = self._session()
        try:
            return load_verification(session, token)
        finally:
            session.close()

    # ------------------------------------------------------------------
    # SubTask 6.1 — async demand-mining hook does not block the response
    # ------------------------------------------------------------------

    def test_demand_mining_hook_does_not_block_response(self) -> None:
        """Mining hook timeout returns control within the 2s budget.

        Patches ``demand_mining_service.mine_demand`` to sleep 3s. The
        orchestrator's 2s mining timeout fires first; the main response
        is returned without mining results. Total elapsed time must stay
        below 3s (2s timeout + main-response overhead).
        """
        import demand_mining_service as dms

        original = dms.mine_demand

        def _slow_mine_demand(session, user_id, conversation_context):
            time.sleep(3.0)
            return original(session, user_id, conversation_context)

        verification = self._make_verification(customer_id=SEEDED_CUSTOMER_ID)
        start = time.monotonic()
        with patch("demand_mining_service.mine_demand", side_effect=_slow_mine_demand):
            result = respond_to_customer_message(
                {
                    "message": "我想咨询下无线鼠标",
                    "customer_id": SEEDED_CUSTOMER_ID,
                    "order_id": SEEDED_ORDER_ID,
                    "conversation_id": "conv-mining-timeout",
                },
                actor=Actor("api-user", "orchestrator", {}),
                verification=verification,
            )
        elapsed = time.monotonic() - start

        # Main response must be returned well under the 3s sleep — the
        # 2s mining timeout kicks in and the hook abandons the result.
        self.assertLess(elapsed, 3.0)
        # The main response is intact (consultation intent -> success /
        # partial). Mining timeout must not corrupt the result shape.
        self.assertIn("status", result)
        self.assertIn("customer_reply", result)
        # Mining timed out → no recommendations surfaced.
        self.assertNotIn("recommendations", result)

    # ------------------------------------------------------------------
    # SubTask 6.2 — recommendation generated when opportunity_score > 0.6
    # ------------------------------------------------------------------

    def test_recommendation_generated_when_opportunity_high(self) -> None:
        """Vip user + product inquiry → opportunity_score > 0.6 → recs.

        Setup: UserProfile(user_id="1", vip) + product-inquiry message
        on ORD-20260601-001 (items: LAPTOP-BAG-01, MOUSE-WL-02). Up-sell
        candidates without co-occurrence get the fallback weight 0.3;
        with vip boost the opportunity_score = 0.3 + 0.2 + 0.15 + 0.1
        = 0.75 > 0.6, so recommendations are generated and persisted.
        """
        self._make_vip_profile(customer_id=SEEDED_CUSTOMER_ID)
        verification = self._make_verification(customer_id=SEEDED_CUSTOMER_ID)

        result = respond_to_customer_message(
            {
                "message": "我想咨询下无线鼠标",  # product_inquiry intent
                "customer_id": SEEDED_CUSTOMER_ID,
                "order_id": SEEDED_ORDER_ID,
                "conversation_id": "conv-rec-high",
            },
            actor=Actor("api-user", "orchestrator", {}),
            verification=verification,
        )

        # Main response is intact.
        self.assertIn("status", result)
        # Recommendations surfaced on the result (non-breaking extra key).
        self.assertIn("recommendations", result)
        recs = result["recommendations"]
        self.assertGreater(len(recs), 0)
        # Each recommendation carries the spec-required keys.
        for rec in recs:
            self.assertIn("recommendation_id", rec)
            self.assertIn("recommend_type", rec)
            self.assertIn("script", rec)
            self.assertIn("expected_conversion_rate", rec)
            self.assertGreater(rec["opportunity_score"], 0.6)

        # Recommendations persisted to the Recommendation table.
        session = self._session()
        try:
            persisted = (
                session.query(Recommendation)
                .filter_by(conversation_id="conv-rec-high")
                .all()
            )
            self.assertEqual(len(persisted), len(recs))
        finally:
            session.close()

    # ------------------------------------------------------------------
    # SubTask 6.2 — recommendation skipped when opportunity_score ≤ 0.6
    # ------------------------------------------------------------------

    def test_recommendation_skipped_when_opportunity_low(self) -> None:
        """Low-tier user (no profile) + product inquiry → score ≤ 0.6.

        Without a UserProfile, ``demand_mining_service._resolve_value_tier``
        falls back to ``"low"``. Up-sell candidates with the fallback
        weight 0.3 score 0.3 + 0.0 + 0.15 + 0.1 = 0.55 < 0.6, so no
        recommendations are generated or persisted.
        """
        # Intentionally NO UserProfile — user falls back to "low" tier.
        verification = self._make_verification(customer_id=SEEDED_CUSTOMER_ID)
        result = respond_to_customer_message(
            {
                "message": "我想咨询下无线鼠标",
                "customer_id": SEEDED_CUSTOMER_ID,
                "order_id": SEEDED_ORDER_ID,
                "conversation_id": "conv-rec-low",
            },
            actor=Actor("api-user", "orchestrator", {}),
            verification=verification,
        )

        self.assertIn("status", result)
        # No recommendations surfaced (opportunity_score never exceeded 0.6).
        self.assertNotIn("recommendations", result)
        # No Recommendation rows persisted.
        session = self._session()
        try:
            count = (
                session.query(Recommendation)
                .filter_by(conversation_id="conv-rec-low")
                .count()
            )
            self.assertEqual(count, 0)
        finally:
            session.close()

    # ------------------------------------------------------------------
    # SubTask 6.3 — attribution recorded after order (async, non-blocking)
    # ------------------------------------------------------------------

    def test_attribution_recorded_after_order(self) -> None:
        """Touch point + fresh order → AttributionRecord persisted.

        Setup: UserProfile(user_id="1") so the touch point's user_id
        resolves to a profile with primary_customer_id=1. A fresh Order
        is planted with ``created_at = now + 1 min`` so the touch point
        recorded at ``now`` falls inside the 24-hour attribution window.
        The attribution hook is fire-and-forget; the test captures the
        Future and waits for it to land before asserting.
        """
        self._make_vip_profile(customer_id=SEEDED_CUSTOMER_ID)
        fresh_order_id = self._make_fresh_order()
        verification = self._make_verification(customer_id=SEEDED_CUSTOMER_ID)

        captured, capturing = self._capture_attribution_futures()
        with patch("orchestrator_api.run_attribution_async", side_effect=capturing):
            result = respond_to_customer_message(
                {
                    "message": "我想咨询下无线鼠标",
                    "customer_id": SEEDED_CUSTOMER_ID,
                    "order_id": fresh_order_id,
                    "conversation_id": "conv-attr",
                },
                actor=Actor("api-user", "orchestrator", {}),
                verification=verification,
            )

        # Main response is intact.
        self.assertIn("status", result)

        # Wait for the fire-and-forget attribution hook to complete.
        self.assertGreater(len(captured), 0, "attribution hook must fire at least once")
        for future in captured:
            future.result(timeout=5.0)

        # A TouchPoint was recorded for the conversation.
        session = self._session()
        try:
            touch_count = (
                session.query(TouchPoint)
                .filter_by(conversation_id="conv-attr")
                .count()
            )
            self.assertGreater(touch_count, 0)

            # AttributionRecord was written for the fresh order. The
            # 24h window contains the touch point, so at least one
            # attribution row must exist.
            attr_rows = (
                session.query(AttributionRecord)
                .filter_by(order_id=fresh_order_id)
                .all()
            )
            self.assertGreater(len(attr_rows), 0)
            for row in attr_rows:
                self.assertEqual(row.order_id, fresh_order_id)
                self.assertGreater(row.total_order_amount, 0.0)
        finally:
            session.close()

    # ------------------------------------------------------------------
    # SubTask 6.5 — mining result appended to usage event's intents JSON
    # ------------------------------------------------------------------

    def test_mining_result_written_to_conversation_state(self) -> None:
        """Mining result appended to the latest usage event's ``intents``.

        SubTask 6.5 option B: store the mining payload in the existing
        ``customer_service_usage_events.intents`` JSON column under the
        synthetic intent ``"profit_engine:mining_result"``. The runtime
        writes the usage event at the end of ``handle_message``; the
        hook then appends to the most recent event for the conversation.
        """
        self._make_vip_profile(customer_id=SEEDED_CUSTOMER_ID)
        verification = self._make_verification(customer_id=SEEDED_CUSTOMER_ID)

        result = respond_to_customer_message(
            {
                "message": "我想咨询下无线鼠标",
                "customer_id": SEEDED_CUSTOMER_ID,
                "order_id": SEEDED_ORDER_ID,
                "conversation_id": "conv-state-write",
            },
            actor=Actor("api-user", "orchestrator", {}),
            verification=verification,
        )

        self.assertIn("status", result)

        session = self._session()
        try:
            event = (
                session.query(CustomerServiceUsageEvent)
                .filter_by(conversation_id="conv-state-write")
                .order_by(CustomerServiceUsageEvent.id.desc())
                .first()
            )
            self.assertIsNotNone(event, "usage event must be recorded for the conversation")
            intents = list(event.intents or [])
            mining_entries = [
                i for i in intents if i.get("intent") == "profit_engine:mining_result"
            ]
            self.assertGreater(
                len(mining_entries),
                0,
                "mining result must be appended to the usage event's intents JSON",
            )
            entry = mining_entries[0]
            self.assertEqual(entry.get("suggested_agent"), "recommendation-agent")
            # Opportunities list is present (mining produced a result).
            self.assertIn("opportunities", entry)
            # Recommendations key always present (may be empty list).
            self.assertIn("recommendations", entry)
        finally:
            session.close()

    # ------------------------------------------------------------------
    # SubTask 6.4 — dispatcher routes recommendation intent
    # ------------------------------------------------------------------

    def test_dispatcher_routes_recommendation_intent(self) -> None:
        """Dispatcher detects the recommendation intent from keywords.

        ``recommendation_keywords`` (推荐 / 搭配 / 交叉销售 / 向上销售 /
        recommend / cross-sell / up-sell) must produce a
        ``recommendation`` intent with ``suggested_agent="recommendation-agent"``.
        """
        dispatcher = RuleBasedIntentDispatcher()
        cases = [
            "我想看看推荐搭配",
            "有没有交叉销售方案",
            "请给我向上销售建议",
            "please recommend a product",
            "any cross-sell options?",
        ]
        for message in cases:
            result = dispatcher.analyze(message)
            intents = [i.intent for i in result.intents]
            self.assertIn(
                "recommendation",
                intents,
                f"message={message!r} must trigger the recommendation intent",
            )
            rec_intent = next(i for i in result.intents if i.intent == "recommendation")
            self.assertEqual(rec_intent.suggested_agent, "recommendation-agent")
            self.assertGreater(rec_intent.confidence, 0.0)

    # ------------------------------------------------------------------
    # SubTask 6.4 — dispatcher routes analytics intent
    # ------------------------------------------------------------------

    def test_dispatcher_routes_analytics_intent(self) -> None:
        """Dispatcher detects the analytics intent from keywords.

        ``analytics_keywords`` (归因 / ROI / 转化率 / 漏斗 / 看板 /
        attribution / funnel / conversion / dashboard) must produce an
        ``analytics`` intent with ``suggested_agent="analytics-agent"``.
        """
        dispatcher = RuleBasedIntentDispatcher()
        cases = [
            "查看归因数据",
            "本月ROI是多少",
            "转化率漏斗怎么样",
            "打开价值看板",
            "show me the attribution report",
            "funnel conversion dashboard",
        ]
        for message in cases:
            result = dispatcher.analyze(message)
            intents = [i.intent for i in result.intents]
            self.assertIn(
                "analytics",
                intents,
                f"message={message!r} must trigger the analytics intent",
            )
            analytics_intent = next(i for i in result.intents if i.intent == "analytics")
            self.assertEqual(analytics_intent.suggested_agent, "analytics-agent")
            self.assertGreater(analytics_intent.confidence, 0.0)

    # ------------------------------------------------------------------
    # SubTask 6.1/6.6 — hook failure does not break the main response
    # ------------------------------------------------------------------

    def test_hook_failure_does_not_break_response(self) -> None:
        """Exception in hook submission is caught; response is returned.

        Patches ``run_demand_mining_async`` to raise ``RuntimeError``
        directly (simulating a bug in hook submission, not the hook's
        internal work). The outer try/except in
        ``respond_to_customer_message`` catches it; the customer-facing
        result is returned unchanged.
        """
        verification = self._make_verification(customer_id=SEEDED_CUSTOMER_ID)
        with patch(
            "orchestrator_api.run_demand_mining_async",
            side_effect=RuntimeError("simulated hook submission failure"),
        ):
            result = respond_to_customer_message(
                {
                    "message": "我想咨询下无线鼠标",
                    "customer_id": SEEDED_CUSTOMER_ID,
                    "order_id": SEEDED_ORDER_ID,
                    "conversation_id": "conv-hook-failure",
                },
                actor=Actor("api-user", "orchestrator", {}),
                verification=verification,
            )

        # Main response is intact despite the hook failure.
        self.assertIn("status", result)
        self.assertIn("customer_reply", result)
        # No recommendations surfaced (hook never ran).
        self.assertNotIn("recommendations", result)


if __name__ == "__main__":
    unittest.main(verbosity=2)
