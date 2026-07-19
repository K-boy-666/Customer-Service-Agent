"""Tests for the unified user profile service (Task 2 — cs-profit-engine).

Uses a shared-cache in-memory SQLite database so no files are written to
disk. A sentinel connection is held open throughout each test to keep the
in-memory database alive across the ``database`` module's engine cycles.
Mirrors the fixture pattern in ``test_profit_engine_migration.py`` and the
unittest style of ``test_daily_analytics.py``.
"""

from __future__ import annotations

import os
import sys
import unittest
from datetime import timedelta

from sqlalchemy import create_engine

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import database
import seed_data
import user_profile_service as ups
from models import (
    CustomerServiceUsageEvent,
    UserIdentity,
    UserIntentTag,
    UserProfile,
    UserValueScore,
    now,
)

# Shared-cache in-memory SQLite URI: keeps the DB in RAM and lets multiple
# engines reach the same database.
IN_MEMORY_URL = "sqlite+pysqlite:///file:user_profile_test?mode=memory&cache=shared&uri=true"


class UserProfileServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        os.environ["DATABASE_URL"] = IN_MEMORY_URL
        os.environ["AUTH_DEV_SECRET"] = "customer-service-test-secret-min-32-bytes"
        # Sentinel connection keeps the shared in-memory DB alive for the test.
        self._sentinel_engine = create_engine(IN_MEMORY_URL)
        self._sentinel_conn = self._sentinel_engine.connect()
        database.reset_engine_for_tests()
        database.init_db()
        session = database.get_session()
        try:
            seed_data.seed(session)
        finally:
            session.close()

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

    def _add_usage_events(self, session, customer_id: int, count: int, *, days_ago: int = 0) -> None:
        base = now() - timedelta(days=days_ago)
        for i in range(count):
            session.add(
                CustomerServiceUsageEvent(
                    conversation_id=f"conv-{customer_id}-{i}",
                    customer_id=customer_id,
                    order_id=None,
                    status="success",
                    emotional_level="L1",
                    message_length=10,
                    intents=[],
                    dispatched_agents=[],
                    tool_calls=[],
                    needs_human=0,
                    created_at=base,
                )
            )
        session.flush()

    # -- SubTask 2.2: merge_identity ---------------------------------------

    def test_merge_identity_creates_new_profile(self) -> None:
        session = self._session()
        try:
            before_profiles = session.query(UserProfile).count()
            before_identities = session.query(UserIdentity).count()

            user_id = ups.merge_identity(session, "web", "phone", "13900000001")

            session.commit()
            after_profiles = session.query(UserProfile).count()
            after_identities = session.query(UserIdentity).count()

            self.assertTrue(user_id.startswith("u_"))
            self.assertEqual(after_profiles, before_profiles + 1)
            self.assertEqual(after_identities, before_identities + 1)

            profile = session.query(UserProfile).filter_by(user_id=user_id).one()
            self.assertIsNone(profile.primary_customer_id)
            identity = session.query(UserIdentity).filter_by(user_id=user_id).one()
            self.assertEqual(identity.platform, "web")
            self.assertEqual(identity.identity_type, "phone")
            self.assertEqual(identity.identity_value, "13900000001")
        finally:
            session.close()

    def test_merge_identity_links_existing_phone(self) -> None:
        # Phone first registered on web, then the same number surfaces on the
        # APP — must reuse the existing user_id and add a second identity row.
        session = self._session()
        try:
            web_user_id = ups.merge_identity(session, "web", "phone", "13900000001")
            app_user_id = ups.merge_identity(session, "app", "phone", "13900000001")
            session.commit()

            self.assertEqual(web_user_id, app_user_id)

            identities = (
                session.query(UserIdentity)
                .filter_by(user_id=web_user_id)
                .order_by(UserIdentity.platform)
                .all()
            )
            self.assertEqual({row.platform for row in identities}, {"app", "web"})
            self.assertEqual({row.identity_type for row in identities}, {"phone"})
            self.assertEqual(session.query(UserProfile).count(), 1)
        finally:
            session.close()

    def test_merge_identity_priority_phone_over_email(self) -> None:
        # Two separate profiles share the same identity_value under different
        # identity_types. A new open_id merge must resolve to the phone-linked
        # profile (phone > email per the priority order).
        session = self._session()
        try:
            phone_user_id = ups.merge_identity(session, "web", "phone", "shared-value")
            # Create a second profile manually that is linked only via email.
            email_user_id = ups.merge_identity(session, "web", "email", "shared-value")
            session.commit()

            # Sanity: the algorithm should have already linked the second call
            # to the phone profile via step-2 priority match (phone wins).
            self.assertEqual(phone_user_id, email_user_id)

            # To exercise the cross-type priority independently, plant a
            # stand-alone email-linked profile directly via the ORM.
            solo_profile = UserProfile(user_id="u_solo_email_profile")
            session.add(solo_profile)
            session.flush()
            session.add(
                UserIdentity(
                    user_id="u_solo_email_profile",
                    platform="mp",
                    identity_type="email",
                    identity_value="priority-battle",
                )
            )
            session.flush()
            # Plant a phone-linked profile with the SAME value.
            phone_profile = UserProfile(user_id="u_phone_winner_profile")
            session.add(phone_profile)
            session.flush()
            session.add(
                UserIdentity(
                    user_id="u_phone_winner_profile",
                    platform="mp",
                    identity_type="phone",
                    identity_value="priority-battle",
                )
            )
            session.commit()

            resolved = ups.merge_identity(session, "app", "open_id", "priority-battle")
            session.commit()

            self.assertEqual(resolved, "u_phone_winner_profile")
            # The new open_id identity row should be linked to the phone winner.
            open_id_row = (
                session.query(UserIdentity)
                .filter_by(platform="app", identity_type="open_id")
                .one()
            )
            self.assertEqual(open_id_row.user_id, "u_phone_winner_profile")
        finally:
            session.close()

    # -- SubTask 2.3: update_intent_tag ------------------------------------

    def test_update_intent_tag_creates_tag(self) -> None:
        session = self._session()
        try:
            user_id = ups.merge_identity(session, "web", "phone", "13900000010")
            ups.update_intent_tag(session, user_id, "refund-inquiry", "conversation", 0.8)
            session.commit()

            tags = session.query(UserIntentTag).filter_by(user_id=user_id).all()
            self.assertEqual(len(tags), 1)
            self.assertEqual(tags[0].tag, "refund-inquiry")
            self.assertEqual(tags[0].source, "conversation")
            self.assertAlmostEqual(tags[0].confidence, 0.8)
        finally:
            session.close()

    def test_update_intent_tag_updates_confidence(self) -> None:
        session = self._session()
        try:
            user_id = ups.merge_identity(session, "web", "phone", "13900000011")
            ups.update_intent_tag(session, user_id, "cross-sell-monitor", "conversation", 0.5)
            ups.update_intent_tag(session, user_id, "cross-sell-monitor", "conversation", 0.92)
            session.commit()

            tags = session.query(UserIntentTag).filter_by(user_id=user_id).all()
            self.assertEqual(len(tags), 1)
            self.assertAlmostEqual(tags[0].confidence, 0.92)
        finally:
            session.close()

    # -- SubTask 2.4: compute_value_score ---------------------------------

    def test_compute_value_score_low_tier(self) -> None:
        # A brand-new profile with no primary_customer_id: no orders, no
        # interactions → all components zero → low tier.
        session = self._session()
        try:
            user_id = ups.merge_identity(session, "web", "open_id", "lonely-open-id")
            result = ups.compute_value_score(session, user_id)
            session.commit()

            self.assertEqual(result["tier"], "low")
            self.assertEqual(result["score"], 0.0)
            self.assertEqual(result["rfm_r"], 0.0)
            self.assertEqual(result["rfm_f"], 0.0)
            self.assertEqual(result["rfm_m"], 0.0)
            self.assertEqual(result["interaction_weight"], 0.0)

            stored = session.query(UserValueScore).filter_by(user_id=user_id).all()
            self.assertEqual(len(stored), 1)
            self.assertEqual(stored[0].tier, "low")
        finally:
            session.close()

    def test_compute_value_score_vip_tier(self) -> None:
        # seed_data customer 1 (张三) has 6 orders totalling ~10059 CNY with
        # the most recent order created today. With primary_customer_id=1 and
        # 11+ recent interactions, the score pushes into vip territory.
        session = self._session()
        try:
            user_id = ups.merge_identity(
                session,
                "web",
                "phone",
                "13800138001",  # 张三's seeded phone number
                primary_customer_id=1,
            )
            self._add_usage_events(session, customer_id=1, count=11, days_ago=0)
            result = ups.compute_value_score(session, user_id)
            session.commit()

            self.assertEqual(result["tier"], "vip")
            self.assertGreater(result["score"], 85.0)
            # RFM components for customer 1: R=100 (today), F=85 (6 orders),
            # M=100 (>5000). Interaction: 80 (11+) + 20 (recent) = 100.
            self.assertEqual(result["rfm_r"], 100.0)
            self.assertEqual(result["rfm_f"], 85.0)
            self.assertEqual(result["rfm_m"], 100.0)
            self.assertEqual(result["interaction_weight"], 100.0)
        finally:
            session.close()

    # -- SubTask 2.1: get_profile / update_profile ------------------------

    def test_get_profile_returns_360_view(self) -> None:
        session = self._session()
        try:
            user_id = ups.merge_identity(session, "web", "phone", "13900000020")
            ups.update_profile(
                session,
                user_id,
                {
                    "display_name": "Alice",
                    "aggregated_attrs": {"age": 30, "city": "Shanghai"},
                    "vip_level": "gold",  # custom key → lands in aggregated_attrs
                },
            )
            ups.update_intent_tag(session, user_id, "product-A-inquiry", "conversation", 0.9)
            ups.update_intent_tag(session, user_id, "shipping-question", "conversation", 0.6)

            view = ups.get_profile(session, user_id)
            session.commit()

            assert view is not None
            self.assertEqual(view["user_id"], user_id)
            self.assertEqual(view["display_name"], "Alice")
            self.assertEqual(view["primary_customer_id"], None)
            self.assertEqual(view["aggregated_attrs"]["age"], 30)
            self.assertEqual(view["aggregated_attrs"]["city"], "Shanghai")
            self.assertEqual(view["aggregated_attrs"]["vip_level"], "gold")

            tags = view["intent_tags"]
            self.assertEqual(len(tags), 2)
            self.assertEqual({t["tag"] for t in tags}, {"product-A-inquiry", "shipping-question"})
            for tag in tags:
                self.assertIn("created_at", tag)
                self.assertIn("confidence", tag)

            value = view["value"]
            self.assertIn("score", value)
            self.assertIn("tier", value)
            self.assertIn(value["tier"], {"low", "medium", "high", "vip"})
            # No orders and no interactions linked to this profile (no
            # primary_customer_id) → expect low tier.
            self.assertEqual(value["tier"], "low")
        finally:
            session.close()

    def test_get_profile_returns_none_for_unknown(self) -> None:
        session = self._session()
        try:
            self.assertIsNone(ups.get_profile(session, "u_does_not_exist"))
        finally:
            session.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
