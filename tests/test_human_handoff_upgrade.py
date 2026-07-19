"""Tests for the intelligent human-handoff upgrade (Task 8 — cs-profit-engine).

Covers SubTask 8.1 (proactive handoff trigger + payload), SubTask 8.2
(agent-assist suggestions / event recording / one-click adoption), and
SubTask 8.3 (agent load-balancing router).

Uses a shared-cache in-memory SQLite database (mirroring
``test_demand_mining_service.py`` / ``test_recommendation_service.py``)
so all profit-engine tables can be exercised against the real ORM. A
sentinel connection is held open for the test's lifetime to keep the
in-memory DB alive across ``database.reset_engine_for_tests`` cycles.

Test list (per SubTask 8.4 spec):
1. ``test_should_proactively_handoff_vip_low_confidence``
2. ``test_should_proactively_handoff_vip_high_confidence``
3. ``test_should_proactively_handoff_non_vip``
4. ``test_evaluate_proactive_handoff_triggers``
5. ``test_evaluate_proactive_handoff_skips``
6. ``test_build_handoff_payload_includes_profile``
7. ``test_build_handoff_payload_includes_recommendations``
8. ``test_generate_assist_suggestions_script``
9. ``test_generate_assist_suggestions_cross_sell``
10. ``test_record_assist_event``
11. ``test_adopt_assist_suggestion``
12. ``test_agent_router_routes_vip_to_senior``
13. ``test_agent_router_routes_by_load``
14. ``test_agent_router_respects_skills``
15. ``test_agent_router_no_available``
"""

from __future__ import annotations

import os
import sys
import unittest

from sqlalchemy import create_engine

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import agent_assist_service as assist
import agent_routing as routing
import database
import human_handoff_upgrade as hhu
from models import (
    AgentAssistEvent,
    CustomerServiceUsageEvent,
    Recommendation,
    UserProfile,
    UserValueScore,
)

# Shared-cache in-memory SQLite URI — distinct namespace from the other
# profit-engine test files so each module gets its own in-memory DB.
IN_MEMORY_URL = "sqlite+pysqlite:///file:human_handoff_upgrade_test?mode=memory&cache=shared&uri=true"


class HumanHandoffUpgradeTest(unittest.TestCase):
    def setUp(self) -> None:
        os.environ["DATABASE_URL"] = IN_MEMORY_URL
        os.environ["AUTH_DEV_SECRET"] = "customer-service-test-secret-min-32-bytes"
        # Sentinel connection keeps the shared in-memory DB alive for the test.
        self._sentinel_engine = create_engine(IN_MEMORY_URL)
        self._sentinel_conn = self._sentinel_engine.connect()
        database.reset_engine_for_tests()
        database.init_db()
        # Isolate the global agent_router between tests so registered
        # agents from one test case do not leak into another.
        routing.agent_router.reset_for_tests()

    def tearDown(self) -> None:
        session = database.get_session()
        try:
            session.rollback()
        finally:
            session.close()
        routing.agent_router.reset_for_tests()
        self._sentinel_conn.close()
        self._sentinel_engine.dispose()
        database.reset_engine_for_tests("sqlite+pysqlite:///:memory:")

    # -- helpers -----------------------------------------------------------

    def _session(self):
        return database.get_session()

    def _make_vip_user(
        self,
        session,
        *,
        user_id: str = "u_vip_user",
        customer_id: int | None = 1,
    ) -> str:
        """Insert a UserProfile + UserValueScore(tier=vip) row."""
        session.add(
            UserProfile(
                user_id=user_id,
                primary_customer_id=customer_id,
                display_name="VIP测试用户",
            )
        )
        session.flush()
        session.add(
            UserValueScore(
                user_id=user_id,
                score=92.0,
                tier="vip",
                rfm_r=100.0,
                rfm_f=85.0,
                rfm_m=80.0,
                interaction_weight=75.0,
            )
        )
        session.flush()
        return user_id

    def _make_low_user(
        self,
        session,
        *,
        user_id: str = "u_low_user",
    ) -> str:
        """Insert a UserProfile + UserValueScore(tier=low) row."""
        session.add(UserProfile(user_id=user_id, display_name="普通用户"))
        session.flush()
        session.add(
            UserValueScore(
                user_id=user_id,
                score=15.0,
                tier="low",
                rfm_r=10.0,
                rfm_f=30.0,
                rfm_m=20.0,
                interaction_weight=10.0,
            )
        )
        session.flush()
        return user_id

    def _make_recommendation(
        self,
        session,
        *,
        user_id: str,
        conversation_id: str = "conv-handoff-1",
        recommendation_id: str = "rec_test_001",
        recommend_type: str = "cross_sell",
        target_sku: str = "MOUSE-WL-02",
        script: str = "为您推荐搭配商品 无线鼠标。",
        opportunity_score: float = 0.75,
    ) -> str:
        session.add(
            Recommendation(
                recommendation_id=recommendation_id,
                user_id=user_id,
                conversation_id=conversation_id,
                recommend_type=recommend_type,
                target_ref=target_sku,
                content="cross-sell reason",
                script=script,
                expected_conversion_rate=0.55,
                opportunity_score=opportunity_score,
                status="pending",
            )
        )
        session.flush()
        return recommendation_id

    def _make_usage_event(
        self,
        session,
        *,
        conversation_id: str,
        needs_human: int = 0,
        status: str = "success",
        emotional_level: str = "L1",
    ) -> int:
        event = CustomerServiceUsageEvent(
            conversation_id=conversation_id,
            customer_id=None,
            order_id=None,
            status=status,
            emotional_level=emotional_level,
            message_length=20,
            intents=[{"intent": "intent:product_inquiry"}],
            dispatched_agents=["consultation-agent"],
            tool_calls=[],
            needs_human=needs_human,
        )
        session.add(event)
        session.flush()
        return int(event.id)

    # ====================================================================
    # SubTask 8.1 — should_proactively_handoff (pure function)
    # ====================================================================

    def test_should_proactively_handoff_vip_low_confidence(self) -> None:
        """vip + confidence 0.5 → True (rule fires)."""
        self.assertTrue(hhu.should_proactively_handoff("vip", 0.5))
        # Edge: exactly 0.7 (exclusive) → also fires (< not <=).
        self.assertTrue(hhu.should_proactively_handoff("vip", 0.69))
        # Edge: confidence 0.0 → fires (we don't know anything, hand off).
        self.assertTrue(hhu.should_proactively_handoff("vip", 0.0))

    def test_should_proactively_handoff_vip_high_confidence(self) -> None:
        """vip + confidence 0.9 → False (high-confidence AI can handle)."""
        self.assertFalse(hhu.should_proactively_handoff("vip", 0.9))
        # Edge: exactly 0.7 → False (rule is strictly < 0.7).
        self.assertFalse(hhu.should_proactively_handoff("vip", 0.7))
        # Edge: above 1.0 (impossible but defensive) → False.
        self.assertFalse(hhu.should_proactively_handoff("vip", 1.5))

    def test_should_proactively_handoff_non_vip(self) -> None:
        """non-vip → False regardless of confidence (proactive path is vip-only)."""
        for tier in ("low", "medium", "high", "", "VIP"):  # case-sensitive: "VIP" != "vip"
            self.assertFalse(hhu.should_proactively_handoff(tier, 0.1))
            self.assertFalse(hhu.should_proactively_handoff(tier, 0.9))

    # ====================================================================
    # SubTask 8.1 — evaluate_proactive_handoff (DB-backed)
    # ====================================================================

    def test_evaluate_proactive_handoff_triggers(self) -> None:
        """vip user + low-confidence mining_result → trigger + payload.

        Setup: vip UserProfile + mining_result with intent_confidence=0.5.
        Expectation: should_handoff=True, reason="vip_low_confidence",
        payload contains user_profile (with value.tier="vip") and
        recommendations list (empty by default, but the key exists).
        """
        session = self._session()
        try:
            user_id = self._make_vip_user(session)
            session.commit()

            mining_result = {
                "intent": "intent:general",
                "intent_confidence": 0.5,
                "opportunities": [],
            }
            evaluation = hhu.evaluate_proactive_handoff(
                session,
                user_id=user_id,
                mining_result=mining_result,
            )
            self.assertTrue(evaluation["should_handoff"])
            self.assertEqual(evaluation["reason"], "vip_low_confidence")
            payload = evaluation["payload"]
            self.assertIsNotNone(payload)
            profile = payload["user_profile"]
            self.assertEqual(profile["user_id"], user_id)
            self.assertEqual(profile["value"]["tier"], "vip")
            self.assertIn("recommendations", payload)
            self.assertIn("conversation_summary", payload)
        finally:
            session.close()

    def test_evaluate_proactive_handoff_skips(self) -> None:
        """low-tier user (or high-confidence vip) → no trigger, payload None.

        Two skip scenarios:
        1. low-tier user with low confidence: tier != vip → skip.
        2. vip user with high confidence (0.9): confidence >= 0.7 → skip.
        """
        session = self._session()
        try:
            # Scenario 1: low-tier user.
            user_id_low = self._make_low_user(session)
            session.commit()
            evaluation_low = hhu.evaluate_proactive_handoff(
                session,
                user_id=user_id_low,
                mining_result={"intent_confidence": 0.4, "intent": "intent:general"},
            )
            self.assertFalse(evaluation_low["should_handoff"])
            self.assertEqual(evaluation_low["reason"], "no_trigger")
            self.assertIsNone(evaluation_low["payload"])

            # Scenario 2: vip user with high confidence.
            user_id_vip = self._make_vip_user(session, user_id="u_vip_high")
            session.commit()
            evaluation_vip_high = hhu.evaluate_proactive_handoff(
                session,
                user_id=user_id_vip,
                mining_result={"intent_confidence": 0.9, "intent": "intent:product_inquiry"},
            )
            self.assertFalse(evaluation_vip_high["should_handoff"])
            self.assertIsNone(evaluation_vip_high["payload"])
        finally:
            session.close()

    # ====================================================================
    # SubTask 8.1 — build_handoff_payload composition
    # ====================================================================

    def test_build_handoff_payload_includes_profile(self) -> None:
        """payload['user_profile'] mirrors user_profile_service.get_profile."""
        session = self._session()
        try:
            user_id = self._make_vip_user(session)
            session.commit()

            payload = hhu.build_handoff_payload(
                session,
                user_id=user_id,
                conversation_id="conv-payload-profile",
            )
            profile = payload["user_profile"]
            self.assertEqual(profile["user_id"], user_id)
            self.assertEqual(profile["display_name"], "VIP测试用户")
            self.assertEqual(profile["value"]["tier"], "vip")
            # conversation_summary is empty when no usage events exist.
            self.assertEqual(payload["conversation_summary"], "")
        finally:
            session.close()

    def test_build_handoff_payload_includes_recommendations(self) -> None:
        """payload['recommendations'] lists the user's recent recommendations.

        Setup: persist 2 recommendations for the user with different
        opportunity scores. The payload's recommendations list should
        include both, ordered newest-first (mirroring
        ``recommendation_service.list_user_recommendations``).
        """
        session = self._session()
        try:
            user_id = self._make_vip_user(session)
            self._make_recommendation(
                session,
                user_id=user_id,
                recommendation_id="rec_payload_1",
                target_sku="SKU-A",
                script="话术A",
            )
            self._make_recommendation(
                session,
                user_id=user_id,
                recommendation_id="rec_payload_2",
                target_sku="SKU-B",
                script="话术B",
            )
            session.commit()

            payload = hhu.build_handoff_payload(
                session,
                user_id=user_id,
                conversation_id="conv-payload-recs",
            )
            recs = payload["recommendations"]
            self.assertEqual(len(recs), 2)
            # Each recommendation carries the spec-required keys.
            for rec in recs:
                self.assertIn("recommendation_id", rec)
                self.assertIn("script", rec)
                self.assertIn("target_ref", rec)
            rec_ids = {rec["recommendation_id"] for rec in recs}
            self.assertEqual(rec_ids, {"rec_payload_1", "rec_payload_2"})
        finally:
            session.close()

    def test_build_handoff_payload_includes_conversation_summary(self) -> None:
        """payload['conversation_summary'] summarises recent usage events."""
        session = self._session()
        try:
            user_id = self._make_vip_user(session)
            conv_id = "conv-summary-test"
            self._make_usage_event(
                session,
                conversation_id=conv_id,
                needs_human=0,
                status="success",
                emotional_level="L1",
            )
            self._make_usage_event(
                session,
                conversation_id=conv_id,
                needs_human=1,
                status="needs-human",
                emotional_level="L2",
            )
            session.commit()

            payload = hhu.build_handoff_payload(
                session,
                user_id=user_id,
                conversation_id=conv_id,
            )
            summary = payload["conversation_summary"]
            self.assertIn(conv_id, conv_id)  # sanity
            self.assertIn("status=success", summary)
            self.assertIn("status=needs-human", summary)
            self.assertIn("needs_human=0", summary)
            self.assertIn("needs_human=1", summary)
        finally:
            session.close()

    # ====================================================================
    # SubTask 8.2 — agent_assist_service.generate_assist_suggestions
    # ====================================================================

    def test_generate_assist_suggestions_script(self) -> None:
        """Script suggestion is built from the latest user recommendation.

        Setup: vip user with one Recommendation (script="为您推荐搭配商品...").
        Expectation: a script suggestion with assist_type="script" and
        content == the recommendation's script.
        """
        session = self._session()
        try:
            user_id = self._make_vip_user(session)
            self._make_recommendation(
                session,
                user_id=user_id,
                recommendation_id="rec_script_test",
                script="为您推荐搭配商品 无线鼠标，与您咨询的商品常常一起购买。",
            )
            session.commit()

            suggestions = assist.generate_assist_suggestions(
                session,
                conversation_id="conv-assist-script",
                user_id=user_id,
                mining_result={
                    "intent": "intent:product_inquiry",
                    "intent_confidence": 0.75,
                    "opportunities": [],
                },
            )
            scripts = [s for s in suggestions if s["assist_type"] == "script"]
            self.assertEqual(len(scripts), 1)
            self.assertEqual(
                scripts[0]["content"],
                "为您推荐搭配商品 无线鼠标，与您咨询的商品常常一起购买。",
            )
            self.assertEqual(scripts[0]["metadata"]["source"], "recommendation")
            self.assertEqual(scripts[0]["metadata"]["recommendation_id"], "rec_script_test")
        finally:
            session.close()

    def test_generate_assist_suggestions_script_from_intent_when_no_recs(self) -> None:
        """Script suggestion falls back to intent-based synthesis when no recs.

        Without any persisted Recommendation rows, the script builder
        synthesises a话术 from the mining intent so the agent always has
        *some* opening line. The metadata.source is "intent" so the UI
        can distinguish persisted话术 vs synthesised fallback.
        """
        session = self._session()
        try:
            user_id = self._make_vip_user(session)
            session.commit()

            suggestions = assist.generate_assist_suggestions(
                session,
                conversation_id="conv-assist-script-intent",
                user_id=user_id,
                mining_result={
                    "intent": "intent:product_inquiry",
                    "intent_confidence": 0.75,
                    "opportunities": [],
                },
            )
            scripts = [s for s in suggestions if s["assist_type"] == "script"]
            self.assertEqual(len(scripts), 1)
            self.assertEqual(scripts[0]["metadata"]["source"], "intent")
            self.assertEqual(scripts[0]["metadata"]["intent"], "intent:product_inquiry")
            self.assertIsInstance(scripts[0]["content"], str)
            self.assertGreater(len(scripts[0]["content"]), 0)
        finally:
            session.close()

    def test_generate_assist_suggestions_cross_sell(self) -> None:
        """Cross-sell suggestion is built from the top cross_sell opportunity.

        Setup: mining_result with 2 cross_sell opportunities (different
        scores) + 1 up_sell. Expectation: only the highest-scoring
        cross_sell is surfaced; up_sell is intentionally not surfaced
        here (the script suggestion handles up-sell话术).
        """
        session = self._session()
        try:
            user_id = self._make_vip_user(session)
            session.commit()

            mining_result = {
                "intent": "intent:product_inquiry",
                "intent_confidence": 0.75,
                "opportunities": [
                    {
                        "type": "cross_sell",
                        "target_sku": "SKU-LOW",
                        "target_name": "低分交叉商品",
                        "opportunity_score": 0.5,
                        "reason": "low-score reason",
                    },
                    {
                        "type": "cross_sell",
                        "target_sku": "SKU-HIGH",
                        "target_name": "高分交叉商品",
                        "opportunity_score": 0.9,
                        "reason": "high-score reason",
                    },
                    {
                        "type": "up_sell",
                        "target_sku": "SKU-UP",
                        "target_name": "升级商品",
                        "opportunity_score": 0.85,
                        "reason": "up-sell reason",
                    },
                ],
            }
            suggestions = assist.generate_assist_suggestions(
                session,
                conversation_id="conv-assist-cross",
                user_id=user_id,
                mining_result=mining_result,
            )
            cross_sells = [s for s in suggestions if s["assist_type"] == "cross_sell"]
            self.assertEqual(len(cross_sells), 1)
            suggestion = cross_sells[0]
            self.assertEqual(suggestion["metadata"]["target_sku"], "SKU-HIGH")
            self.assertEqual(suggestion["metadata"]["target_name"], "高分交叉商品")
            self.assertAlmostEqual(suggestion["metadata"]["opportunity_score"], 0.9)
            self.assertIn("高分交叉商品", suggestion["content"])
            self.assertIn("SKU-HIGH", suggestion["content"])
        finally:
            session.close()

    # ====================================================================
    # SubTask 8.2 — agent_assist_service.record_assist_event / list
    # ====================================================================

    def test_record_assist_event(self) -> None:
        """record_assist_event persists an AgentAssistEvent and returns its id.

        Setup: call record_assist_event twice (adopted=False, adopted=True).
        list_assist_events returns both newest-first; each event dict
        carries the spec-required keys (assist_type, content, adopted).
        """
        session = self._session()
        try:
            id1 = assist.record_assist_event(
                session,
                conversation_id="conv-record-1",
                agent_id="agent_A",
                assist_type="script",
                content="话术A",
                adopted=False,
            )
            id2 = assist.record_assist_event(
                session,
                conversation_id="conv-record-1",
                agent_id="agent_A",
                assist_type="cross_sell",
                content="交叉销售建议",
                adopted=True,
            )
            session.commit()

            self.assertIsInstance(id1, int)
            self.assertIsInstance(id2, int)
            self.assertGreater(id2, id1)

            events = assist.list_assist_events(session, conversation_id="conv-record-1")
            self.assertEqual(len(events), 2)
            # Newest-first.
            self.assertEqual(events[0]["id"], id2)
            self.assertEqual(events[0]["assist_type"], "cross_sell")
            self.assertEqual(events[0]["adopted"], 1)
            self.assertEqual(events[1]["id"], id1)
            self.assertEqual(events[1]["adopted"], 0)

            # Verify the row was actually persisted to the table.
            row_count = (
                session.query(AgentAssistEvent)
                .filter_by(conversation_id="conv-record-1")
                .count()
            )
            self.assertEqual(row_count, 2)
        finally:
            session.close()

    def test_adopt_assist_suggestion(self) -> None:
        """adopt_assist_suggestion flips adopted=0 → 1; idempotent.

        Setup: record an event with adopted=False. Adopt it twice —
        both calls return True (idempotent), and the row's adopted flag
        stays 1. An invalid event_id returns False (no row updated).
        """
        session = self._session()
        try:
            event_id = assist.record_assist_event(
                session,
                conversation_id="conv-adopt",
                agent_id="agent_A",
                assist_type="script",
                content="待采纳话术",
                adopted=False,
            )
            session.commit()

            # First adoption: 0 → 1, returns True.
            self.assertTrue(assist.adopt_assist_suggestion(session, event_id))
            session.commit()

            events = assist.list_assist_events(session, conversation_id="conv-adopt")
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["adopted"], 1)

            # Second adoption: idempotent — still 1, still returns True.
            self.assertTrue(assist.adopt_assist_suggestion(session, event_id))

            # Invalid event_id → False.
            self.assertFalse(assist.adopt_assist_suggestion(session, 999_999_999))
        finally:
            session.close()

    # ====================================================================
    # SubTask 8.3 — agent_routing.AgentRouter
    # ====================================================================

    def test_agent_router_routes_vip_to_senior(self) -> None:
        """vip user → senior agent wins over junior at the same load.

        Setup: register one senior + one junior agent, both at zero
        load and with the same skill set. Routing a vip user must
        pick the senior agent.
        """
        router = routing.AgentRouter()
        router.register_agent(
            "agent_junior",
            seniority="junior",
            skills={"complaint"},
            max_capacity=5,
        )
        router.register_agent(
            "agent_senior",
            seniority="senior",
            skills={"complaint"},
            max_capacity=5,
        )

        chosen = router.route(user_value_tier="vip", required_skills={"complaint"})
        self.assertEqual(chosen, "agent_senior")

        # Non-vip user → seniority does not bias; tie broken by load
        # (both at zero → equal) then by agent_id asc → agent_junior wins.
        chosen_non_vip = router.route(user_value_tier="low", required_skills={"complaint"})
        self.assertEqual(chosen_non_vip, "agent_junior")

    def test_agent_router_routes_by_load(self) -> None:
        """Lower-load agent wins when seniority is equal.

        Setup: two junior agents, one with 4/5 active (rate 0.8) and one
        with 1/5 active (rate 0.2). Routing must pick the less-loaded
        one (agent_light).
        """
        router = routing.AgentRouter()
        router.register_agent(
            "agent_busy",
            seniority="junior",
            skills={"complaint"},
            max_capacity=5,
        )
        router.register_agent(
            "agent_light",
            seniority="junior",
            skills={"complaint"},
            max_capacity=5,
        )
        router.update_load("agent_busy", 4)
        router.update_load("agent_light", 1)

        chosen = router.route(user_value_tier="vip", required_skills={"complaint"})
        self.assertEqual(chosen, "agent_light")

        # Summary reflects the load.
        summary = {row["agent_id"]: row for row in router.get_load_summary()}
        self.assertEqual(summary["agent_busy"]["active_conversations"], 4)
        self.assertEqual(summary["agent_light"]["active_conversations"], 1)
        self.assertGreater(summary["agent_busy"]["load_rate"], summary["agent_light"]["load_rate"])

    def test_agent_router_respects_skills(self) -> None:
        """Agent without a matching skill is never routed to.

        Setup: three agents — one with {complaint}, one with
        {after_sales}, one with both. Required skill = {complaint}:
        only the agents whose skill set intersects {complaint} are
        candidates. The single-skill after_sales agent is filtered out.
        """
        router = routing.AgentRouter()
        router.register_agent(
            "agent_complaint_only",
            seniority="senior",
            skills={"complaint"},
            max_capacity=5,
        )
        router.register_agent(
            "agent_after_sales_only",
            seniority="senior",
            skills={"after_sales"},
            max_capacity=5,
        )
        router.register_agent(
            "agent_both",
            seniority="junior",
            skills={"complaint", "after_sales"},
            max_capacity=5,
        )

        # vip user, required skill = complaint → senior complaint-only
        # agent wins (vip prefers senior, both at zero load).
        chosen = router.route(user_value_tier="vip", required_skills={"complaint"})
        self.assertEqual(chosen, "agent_complaint_only")

        # Required skill = after_sales → senior after-sales-only wins.
        chosen_after_sales = router.route(
            user_value_tier="vip", required_skills={"after_sales"}
        )
        self.assertEqual(chosen_after_sales, "agent_after_sales_only")

        # No required skills → skill filter is skipped; senior agents
        # tie (both at zero load) → broken by agent_id asc.
        chosen_no_skill = router.route(user_value_tier="vip", required_skills=None)
        self.assertEqual(chosen_no_skill, "agent_after_sales_only")

    def test_agent_router_no_available(self) -> None:
        """Returns None when no agent matches (no skills / no capacity / empty registry).

        Three scenarios:
        1. Empty registry → None.
        2. Registered agents but none match the required skill → None.
        3. Skill matches but all are at max capacity → None.
        """
        # Scenario 1: empty registry.
        router_empty = routing.AgentRouter()
        self.assertIsNone(router_empty.route(user_value_tier="vip", required_skills={"complaint"}))

        # Scenario 2: no skill match.
        router_no_skill = routing.AgentRouter()
        router_no_skill.register_agent(
            "agent_a",
            seniority="senior",
            skills={"after_sales"},
            max_capacity=5,
        )
        self.assertIsNone(
            router_no_skill.route(user_value_tier="vip", required_skills={"complaint"})
        )

        # Scenario 3: at capacity.
        router_full = routing.AgentRouter()
        router_full.register_agent(
            "agent_full",
            seniority="senior",
            skills={"complaint"},
            max_capacity=2,
        )
        router_full.update_load("agent_full", 2)
        self.assertIsNone(
            router_full.route(user_value_tier="vip", required_skills={"complaint"})
        )

    def test_agent_router_register_overwrites_and_unregister(self) -> None:
        """register_agent overwrites; unregister removes the agent."""
        router = routing.AgentRouter()
        router.register_agent(
            "agent_x",
            seniority="junior",
            skills={"complaint"},
            max_capacity=5,
        )
        # First route: junior agent is the only candidate.
        self.assertEqual(
            router.route(user_value_tier="low", required_skills={"complaint"}),
            "agent_x",
        )
        # Re-register with different config — old record replaced.
        router.register_agent(
            "agent_x",
            seniority="senior",
            skills={"complaint", "after_sales"},
            max_capacity=3,
        )
        summary = {row["agent_id"]: row for row in router.get_load_summary()}
        self.assertEqual(summary["agent_x"]["seniority"], "senior")
        self.assertEqual(set(summary["agent_x"]["skills"]), {"complaint", "after_sales"})
        self.assertEqual(summary["agent_x"]["max_capacity"], 3)

        # Unregister → no candidates.
        router.unregister_agent("agent_x")
        self.assertIsNone(router.route(user_value_tier="vip", required_skills={"complaint"}))
        # Unregister non-existent agent is a no-op (no exception).
        router.unregister_agent("agent_does_not_exist")

    def test_agent_router_update_load_clamps_to_capacity(self) -> None:
        """update_load clamps active_conversations to [0, max_capacity]."""
        router = routing.AgentRouter()
        router.register_agent(
            "agent_clamp",
            seniority="junior",
            skills={"complaint"},
            max_capacity=5,
        )
        # Above max_capacity → clamped to 5, agent becomes full.
        router.update_load("agent_clamp", 99)
        summary = {row["agent_id"]: row for row in router.get_load_summary()}
        self.assertEqual(summary["agent_clamp"]["active_conversations"], 5)
        self.assertEqual(summary["agent_clamp"]["load_rate"], 1.0)
        self.assertIsNone(
            router.route(user_value_tier="vip", required_skills={"complaint"})
        )
        # Negative → clamped to 0.
        router.update_load("agent_clamp", -5)
        summary = {row["agent_id"]: row for row in router.get_load_summary()}
        self.assertEqual(summary["agent_clamp"]["active_conversations"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
