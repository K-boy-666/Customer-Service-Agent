"""Tests for the recommendation service (Task 4 — cs-profit-engine).

Uses a shared-cache in-memory SQLite database so no files are written to
disk. A sentinel connection is held open throughout each test to keep the
in-memory database alive across the ``database`` module's engine cycles.
Mirrors the fixture pattern in ``test_user_profile_service.py`` and the
unittest style of ``test_daily_analytics.py`` / ``test_demand_mining_service.py``.

Coverage:
- generate_recommendations: threshold filter, max-3 cap, sort order, DB
  persistence, script template, expected-conversion-rate composition.
- record_funnel_event: 24h dedup within same event_type, different
  event_types not deduped, after-24h allowed, order event with order_id.
- is_recommendation_exposed_recently: True after exposure, False outside
  the 24h window.
- get_recommendation / list_user_recommendations: detail and list queries.
"""

from __future__ import annotations

import os
import sys
import unittest
from datetime import timedelta

from sqlalchemy import create_engine

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import database
import recommendation_service as recs
from models import FunnelEvent, Recommendation, UserProfile, now

# Shared-cache in-memory SQLite URI: keeps the DB in RAM and lets multiple
# engines reach the same database. Distinct from the URIs used by other
# test files so each module gets its own in-memory namespace.
IN_MEMORY_URL = "sqlite+pysqlite:///file:recommendation_test?mode=memory&cache=shared&uri=true"


class RecommendationServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        os.environ["DATABASE_URL"] = IN_MEMORY_URL
        os.environ["AUTH_DEV_SECRET"] = "customer-service-test-secret-min-32-bytes"
        # Sentinel connection keeps the shared in-memory DB alive for the test.
        self._sentinel_engine = create_engine(IN_MEMORY_URL)
        self._sentinel_conn = self._sentinel_engine.connect()
        database.reset_engine_for_tests()
        database.init_db()
        # No seed_data — recommendation_service only reads user_profile,
        # and a missing profile safely falls back to "low" tier (matching
        # the demand_mining_service fallback convention).

    def tearDown(self) -> None:
        session = database.get_session()
        try:
            session.rollback()
        finally:
            session.close()
        self._sentinel_conn.close()
        self._sentinel_engine.dispose()
        database.reset_engine_for_tests("sqlite+pysqlite:///:memory:")

    # -- helpers -----------------------------------------------------------

    def _session(self):
        return database.get_session()

    def _make_user(self, session, user_id: str = "u_test_user") -> str:
        """Insert a bare UserProfile row so user_value_tier resolves to "low"."""
        session.add(UserProfile(user_id=user_id))
        session.flush()
        return user_id

    def _make_opportunities(
        self,
        scores: list[float],
        recommend_type: str = "cross_sell",
        sku_prefix: str = "SKU",
    ) -> list[dict]:
        return [
            {
                "type": recommend_type,
                "target_sku": f"{sku_prefix}-{i}",
                "target_name": f"测试商品{i}",
                "opportunity_score": score,
                "reason": f"reason-{i}",
            }
            for i, score in enumerate(scores)
        ]

    # -- SubTask 4.1: generate_recommendations -----------------------------

    def test_generate_recommendations_filters_by_threshold(self) -> None:
        # opportunity_score ≤ 0.6 must NOT generate a recommendation.
        # Strictly-greater-than: 0.6 itself is filtered out, 0.7+ kept.
        session = self._session()
        try:
            user_id = self._make_user(session)
            opportunities = self._make_opportunities([0.5, 0.6, 0.7, 0.8])
            result = recs.generate_recommendations(
                session, user_id, "conv-1", opportunities
            )
            session.commit()

            self.assertEqual(len(result), 2)
            scores = sorted(r["opportunity_score"] for r in result)
            self.assertEqual(scores, [0.7, 0.8])
            for r in result:
                self.assertGreater(r["opportunity_score"], recs.OPPORTUNITY_THRESHOLD)
        finally:
            session.close()

    def test_generate_recommendations_max_three(self) -> None:
        # More than 3 qualified opportunities → only top 3 returned.
        session = self._session()
        try:
            user_id = self._make_user(session)
            opportunities = self._make_opportunities([0.7, 0.75, 0.8, 0.85, 0.9])
            result = recs.generate_recommendations(
                session, user_id, "conv-1", opportunities
            )
            session.commit()

            self.assertEqual(len(result), recs.MAX_RECOMMENDATIONS)
            self.assertEqual(len(result), 3)
        finally:
            session.close()

    def test_generate_recommendations_sorted_by_score(self) -> None:
        # Output must be sorted by opportunity_score descending.
        session = self._session()
        try:
            user_id = self._make_user(session)
            opportunities = self._make_opportunities([0.7, 0.9, 0.8, 0.75, 0.85])
            result = recs.generate_recommendations(
                session, user_id, "conv-1", opportunities
            )
            session.commit()

            scores = [r["opportunity_score"] for r in result]
            self.assertEqual(scores, sorted(scores, reverse=True))
            self.assertAlmostEqual(scores[0], 0.9)
        finally:
            session.close()

    def test_generate_recommendations_creates_db_records(self) -> None:
        # Each returned recommendation must have a matching Recommendation row.
        session = self._session()
        try:
            user_id = self._make_user(session)
            opportunities = self._make_opportunities([0.7, 0.8])
            result = recs.generate_recommendations(
                session, user_id, "conv-1", opportunities
            )
            session.commit()

            self.assertEqual(len(result), 2)
            rec_ids = {r["recommendation_id"] for r in result}
            self.assertEqual(len(rec_ids), 2)
            for r in result:
                self.assertTrue(r["recommendation_id"].startswith("rec_"))

            db_rows = (
                session.query(Recommendation)
                .filter(Recommendation.recommendation_id.in_(rec_ids))
                .all()
            )
            self.assertEqual({row.recommendation_id for row in db_rows}, rec_ids)
            for row in db_rows:
                self.assertEqual(row.user_id, user_id)
                self.assertEqual(row.conversation_id, "conv-1")
                self.assertEqual(row.status, "pending")
                # target_ref stores the SKU (Recommendation.target_ref column).
                self.assertTrue(row.target_ref.startswith("SKU-"))
                self.assertTrue(row.script)
                self.assertGreater(row.opportunity_score, recs.OPPORTUNITY_THRESHOLD)
        finally:
            session.close()

    def test_generate_recommendations_includes_script(self) -> None:
        # Output must include a non-empty script matching the type template.
        session = self._session()
        try:
            user_id = self._make_user(session)
            opportunities = self._make_opportunities([0.7], recommend_type="cross_sell")
            result = recs.generate_recommendations(
                session, user_id, "conv-1", opportunities
            )
            session.commit()

            self.assertEqual(len(result), 1)
            script = result[0]["script"]
            self.assertTrue(script)
            self.assertIn("测试商品0", script)
            self.assertIn("为您推荐搭配商品", script)

            # Verify up_sell and coupon templates too.
            up_result = recs.generate_recommendations(
                session,
                user_id,
                "conv-2",
                self._make_opportunities([0.8], recommend_type="up_sell", sku_prefix="UP"),
            )
            session.commit()
            self.assertEqual(len(up_result), 1)
            self.assertIn("升级款", up_result[0]["script"])
        finally:
            session.close()

    def test_generate_recommendations_expected_conversion_rate(self) -> None:
        # Conversion rate = clamp(opportunity_score * 0.5 + tier_boost, 0, 0.95).
        # UserProfile with no value score → "low" tier → boost 0.
        session = self._session()
        try:
            user_id = self._make_user(session)
            opportunities = self._make_opportunities([0.8])
            result = recs.generate_recommendations(
                session, user_id, "conv-1", opportunities
            )
            session.commit()

            self.assertEqual(len(result), 1)
            # base = 0.8 * 0.5 = 0.4; low boost = 0; result = 0.4.
            self.assertAlmostEqual(result[0]["expected_conversion_rate"], 0.4)
            self.assertLessEqual(result[0]["expected_conversion_rate"], recs.CONVERSION_MAX)
            self.assertGreaterEqual(result[0]["expected_conversion_rate"], recs.CONVERSION_MIN)
        finally:
            session.close()

    def test_generate_recommendations_empty_when_no_qualified(self) -> None:
        # All opportunities below threshold → empty list, no DB writes.
        session = self._session()
        try:
            user_id = self._make_user(session)
            opportunities = self._make_opportunities([0.3, 0.5, 0.6])
            result = recs.generate_recommendations(
                session, user_id, "conv-1", opportunities
            )
            session.commit()

            self.assertEqual(result, [])
            self.assertEqual(
                session.query(Recommendation).filter_by(user_id=user_id).count(),
                0,
            )
        finally:
            session.close()

    # -- SubTask 4.2: record_funnel_event ---------------------------------

    def test_record_funnel_event_dedup_within_24h(self) -> None:
        # Same (recommendation_id, event_type) within 24h → second call returns False.
        session = self._session()
        try:
            user_id = self._make_user(session)
            recommendations = recs.generate_recommendations(
                session, user_id, "conv-1", self._make_opportunities([0.7])
            )
            session.commit()
            self.assertEqual(len(recommendations), 1)
            rec_id = recommendations[0]["recommendation_id"]

            first = recs.record_funnel_event(
                session, rec_id, user_id, "sess-1", "exposure"
            )
            session.commit()
            second = recs.record_funnel_event(
                session, rec_id, user_id, "sess-1", "exposure"
            )
            session.commit()

            self.assertTrue(first)
            self.assertFalse(second)

            count = (
                session.query(FunnelEvent)
                .filter_by(recommendation_id=rec_id, event_type="exposure")
                .count()
            )
            self.assertEqual(count, 1)
        finally:
            session.close()

    def test_record_funnel_event_allows_different_types(self) -> None:
        # Different event_types for the same recommendation are NOT deduped.
        session = self._session()
        try:
            user_id = self._make_user(session)
            recommendations = recs.generate_recommendations(
                session, user_id, "conv-1", self._make_opportunities([0.7])
            )
            session.commit()
            rec_id = recommendations[0]["recommendation_id"]

            exposure = recs.record_funnel_event(
                session, rec_id, user_id, "sess-1", "exposure"
            )
            click = recs.record_funnel_event(
                session, rec_id, user_id, "sess-1", "click"
            )
            consult = recs.record_funnel_event(
                session, rec_id, user_id, "sess-1", "consult"
            )
            order = recs.record_funnel_event(
                session, rec_id, user_id, "sess-1", "order",
                order_id="ORD-1",
            )
            session.commit()

            self.assertTrue(exposure)
            self.assertTrue(click)
            self.assertTrue(consult)
            self.assertTrue(order)

            total = (
                session.query(FunnelEvent)
                .filter_by(recommendation_id=rec_id)
                .count()
            )
            self.assertEqual(total, 4)
        finally:
            session.close()

    def test_record_funnel_event_allows_after_24h(self) -> None:
        # An event older than 24h must NOT block a new event of the same type.
        session = self._session()
        try:
            user_id = self._make_user(session)
            recommendations = recs.generate_recommendations(
                session, user_id, "conv-1", self._make_opportunities([0.7])
            )
            session.commit()
            rec_id = recommendations[0]["recommendation_id"]

            # Manually plant an exposure event 25 hours ago — outside the window.
            session.add(
                FunnelEvent(
                    recommendation_id=rec_id,
                    user_id=user_id,
                    session_id="sess-old",
                    event_type="exposure",
                    created_at=now() - timedelta(hours=25),
                )
            )
            session.commit()

            # A new exposure within the 24h window must be written.
            written = recs.record_funnel_event(
                session, rec_id, user_id, "sess-new", "exposure"
            )
            session.commit()

            self.assertTrue(written)
            count = (
                session.query(FunnelEvent)
                .filter_by(recommendation_id=rec_id, event_type="exposure")
                .count()
            )
            self.assertEqual(count, 2)
        finally:
            session.close()

    def test_record_funnel_event_with_order(self) -> None:
        # order event must persist order_id and payload for attribution.
        session = self._session()
        try:
            user_id = self._make_user(session)
            recommendations = recs.generate_recommendations(
                session, user_id, "conv-1", self._make_opportunities([0.7])
            )
            session.commit()
            rec_id = recommendations[0]["recommendation_id"]

            written = recs.record_funnel_event(
                session,
                rec_id,
                user_id,
                "sess-1",
                "order",
                order_id="ORD-12345",
                payload={"amount": 199.0, "currency": "CNY"},
            )
            session.commit()

            self.assertTrue(written)
            row = (
                session.query(FunnelEvent)
                .filter_by(recommendation_id=rec_id, event_type="order")
                .one()
            )
            self.assertEqual(row.order_id, "ORD-12345")
            self.assertEqual(row.payload, {"amount": 199.0, "currency": "CNY"})
        finally:
            session.close()

    # -- SubTask 4.2: is_recommendation_exposed_recently -----------------

    def test_is_recommendation_exposed_recently(self) -> None:
        # Before exposure: False. After exposure: True within 24h.
        session = self._session()
        try:
            user_id = self._make_user(session)
            recommendations = recs.generate_recommendations(
                session, user_id, "conv-1", self._make_opportunities([0.7])
            )
            session.commit()
            rec_id = recommendations[0]["recommendation_id"]

            self.assertFalse(recs.is_recommendation_exposed_recently(session, rec_id))

            written = recs.record_funnel_event(
                session, rec_id, user_id, "sess-1", "exposure"
            )
            session.commit()
            self.assertTrue(written)

            self.assertTrue(recs.is_recommendation_exposed_recently(session, rec_id))
        finally:
            session.close()

    def test_is_recommendation_exposed_recently_outside_window(self) -> None:
        # An exposure older than 24h must NOT count as recently exposed.
        session = self._session()
        try:
            user_id = self._make_user(session)
            recommendations = recs.generate_recommendations(
                session, user_id, "conv-1", self._make_opportunities([0.7])
            )
            session.commit()
            rec_id = recommendations[0]["recommendation_id"]

            session.add(
                FunnelEvent(
                    recommendation_id=rec_id,
                    user_id=user_id,
                    session_id="sess-old",
                    event_type="exposure",
                    created_at=now() - timedelta(hours=25),
                )
            )
            session.commit()

            self.assertFalse(recs.is_recommendation_exposed_recently(session, rec_id))
            # Custom shorter window should also report False (no exposure in last hour).
            self.assertFalse(recs.is_recommendation_exposed_recently(session, rec_id, hours=1))
        finally:
            session.close()

    # -- SubTask 4.1: get / list ------------------------------------------

    def test_get_recommendation_returns_details(self) -> None:
        session = self._session()
        try:
            user_id = self._make_user(session)
            recommendations = recs.generate_recommendations(
                session, user_id, "conv-1", self._make_opportunities([0.7])
            )
            session.commit()
            rec_id = recommendations[0]["recommendation_id"]

            details = recs.get_recommendation(session, rec_id)
            self.assertIsNotNone(details)
            assert details is not None  # narrow type for static checkers
            self.assertEqual(details["recommendation_id"], rec_id)
            self.assertEqual(details["user_id"], user_id)
            self.assertEqual(details["conversation_id"], "conv-1")
            self.assertEqual(details["recommend_type"], "cross_sell")
            self.assertIn("script", details)
            self.assertIn("expected_conversion_rate", details)
            self.assertIn("opportunity_score", details)
            self.assertEqual(details["status"], "pending")

            # Unknown recommendation_id returns None.
            self.assertIsNone(recs.get_recommendation(session, "rec_does_not_exist"))
        finally:
            session.close()

    def test_list_user_recommendations(self) -> None:
        session = self._session()
        try:
            user_id = self._make_user(session)
            # 2 recommendations for user_id, 1 for another user.
            recs.generate_recommendations(
                session, user_id, "conv-1", self._make_opportunities([0.7, 0.8])
            )
            other_user = self._make_user(session, user_id="u_other_user")
            recs.generate_recommendations(
                session,
                other_user,
                "conv-2",
                self._make_opportunities([0.7], sku_prefix="OTH"),
            )
            session.commit()

            rows = recs.list_user_recommendations(session, user_id)
            self.assertEqual(len(rows), 2)
            for row in rows:
                self.assertEqual(row["user_id"], user_id)

            # Other user's recommendations must not leak into this user's list.
            other_rows = recs.list_user_recommendations(session, other_user)
            self.assertEqual(len(other_rows), 1)
            self.assertEqual(other_rows[0]["user_id"], other_user)
        finally:
            session.close()

    def test_list_user_recommendations_respects_limit(self) -> None:
        session = self._session()
        try:
            user_id = self._make_user(session)
            recs.generate_recommendations(
                session, user_id, "conv-1", self._make_opportunities([0.7, 0.8, 0.9])
            )
            session.commit()

            rows = recs.list_user_recommendations(session, user_id, limit=2)
            self.assertEqual(len(rows), 2)

            # limit=0 returns an empty list (defensive boundary).
            self.assertEqual(recs.list_user_recommendations(session, user_id, limit=0), [])

            # Default limit covers all 3.
            self.assertEqual(len(recs.list_user_recommendations(session, user_id)), 3)
        finally:
            session.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
