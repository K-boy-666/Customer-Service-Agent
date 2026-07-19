"""Tests for the demand mining service (Task 3 — cs-profit-engine).

Uses a shared-cache in-memory SQLite database so no files are written to
disk. A sentinel connection is held open throughout each test to keep the
in-memory database alive across the ``database`` module's engine cycles.
Mirrors the fixture pattern in ``test_user_profile_service.py`` and the
unittest style of ``test_daily_analytics.py``.
"""

from __future__ import annotations

import os
import sys
import unittest

from sqlalchemy import create_engine

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import database
import demand_mining_service as dms
import seed_data
from models import Order, OrderItem, Product

# Shared-cache in-memory SQLite URI: keeps the DB in RAM and lets multiple
# engines reach the same database.
IN_MEMORY_URL = "sqlite+pysqlite:///file:demand_mining_test?mode=memory&cache=shared&uri=true"


class DemandMiningServiceTest(unittest.TestCase):
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

    def _add_order(self, session, order_id: str, items: list[tuple[str, str, float, int]]) -> None:
        """Create one Order + its OrderItems. ``items`` = [(sku, name, price, qty)]."""
        total = sum(price * qty for _sku, _name, price, qty in items)
        session.add(
            Order(
                id=order_id,
                order_number=f"SON{order_id}",
                customer_id=1,
                status="delivered",
                total_amount=round(total, 2),
                currency="CNY",
                shipping_address="test-address",
                created_at="2026-07-01T00:00:00",
                updated_at="2026-07-01T00:00:00",
            )
        )
        for sku, name, price, qty in items:
            session.add(
                OrderItem(order_id=order_id, sku=sku, name=name, qty=qty, price=price)
            )

    # -- SubTask 3.1: classify_intent --------------------------------------

    def test_classify_intent_keywords(self) -> None:
        cases = [
            # after_sales_return
            ("我要退货", "intent:after_sales_return", 0.85),
            ("申请退款", "intent:after_sales_return", 0.85),
            ("想换货", "intent:after_sales_return", 0.85),
            # product_inquiry
            ("我想咨询下这个商品", "intent:product_inquiry", 0.75),
            ("了解一下你们的产品", "intent:product_inquiry", 0.75),
            ("问下这款鼠标", "intent:product_inquiry", 0.75),
            # logistics_inquiry
            ("我的快递到哪了", "intent:logistics_inquiry", 0.8),
            ("物流太慢了", "intent:logistics_inquiry", 0.8),
            # complaint
            ("我要投诉", "intent:complaint", 0.9),
            ("给个差评", "intent:complaint", 0.9),
            ("很不满意", "intent:complaint", 0.9),
            # upgrade_inquiry
            ("想升级套餐", "intent:upgrade_inquiry", 0.8),
            ("有没有 Pro 版本", "intent:upgrade_inquiry", 0.8),
            ("想要 plus", "intent:upgrade_inquiry", 0.8),
            ("有没有 max 配置", "intent:upgrade_inquiry", 0.8),
        ]
        for message, expected_intent, expected_confidence in cases:
            intent, confidence = dms.classify_intent(message)
            self.assertEqual(intent, expected_intent, f"message={message!r}")
            self.assertAlmostEqual(
                confidence,
                expected_confidence,
                msg=f"message={message!r}",
            )

    def test_classify_intent_default(self) -> None:
        for message in ("你好", "", "   "):
            intent, confidence = dms.classify_intent(message)
            self.assertEqual(intent, "intent:general", f"message={message!r}")
            self.assertAlmostEqual(confidence, 0.4, msg=f"message={message!r}")

    def test_classify_intent_priority_complaint_over_return(self) -> None:
        # "投诉退货" matches both complaint and after_sales_return keywords;
        # complaint must win because it is checked first (stronger signal).
        intent, confidence = dms.classify_intent("我要投诉并退货")
        self.assertEqual(intent, "intent:complaint")
        self.assertAlmostEqual(confidence, 0.9)

    def test_classify_intent_priority_upgrade_over_inquiry(self) -> None:
        # "咨询升级" matches both product_inquiry and upgrade_inquiry keywords;
        # upgrade_inquiry must win because it is checked first.
        intent, confidence = dms.classify_intent("我想咨询下升级方案")
        self.assertEqual(intent, "intent:upgrade_inquiry")
        self.assertAlmostEqual(confidence, 0.8)

    # -- SubTask 3.2: get_product_relations --------------------------------

    def test_get_product_relations_returns_co_occurrence(self) -> None:
        session = self._session()
        try:
            # Dedicated products so the co-occurrence count is fully
            # controlled by this test (independent of seed_data).
            session.add(Product(sku="TEST-A", name="测试商品A", category="测试类目", unit_price=100.0))
            session.add(Product(sku="TEST-B", name="测试商品B", category="测试类目", unit_price=50.0))
            session.add(Product(sku="TEST-C", name="测试商品C", category="测试类目", unit_price=200.0))
            session.flush()

            # 3 orders where TEST-A and TEST-B co-occur.
            for i in range(3):
                self._add_order(
                    session,
                    f"TEST-ORDER-CO-{i}",
                    [("TEST-A", "测试商品A", 100.0, 1), ("TEST-B", "测试商品B", 50.0, 1)],
                )
            # 2 orders where TEST-A appears with TEST-C (not B).
            for i in range(2):
                self._add_order(
                    session,
                    f"TEST-ORDER-AC-{i}",
                    [("TEST-A", "测试商品A", 100.0, 1), ("TEST-C", "测试商品C", 200.0, 1)],
                )
            session.commit()

            total_orders = session.query(Order).count()
            relations = dms.get_product_relations(session, "TEST-A", top_n=5)

            # TEST-B (3 co-occ) must rank above TEST-C (2 co-occ).
            self.assertGreaterEqual(len(relations), 2)
            top = relations[0]
            self.assertEqual(top["sku"], "TEST-B")
            self.assertEqual(top["name"], "测试商品B")
            self.assertEqual(top["co_occurrence_count"], 3)
            self.assertAlmostEqual(top["weight"], 3 / total_orders)

            c_relation = next(r for r in relations if r["sku"] == "TEST-C")
            self.assertEqual(c_relation["co_occurrence_count"], 2)
            self.assertAlmostEqual(c_relation["weight"], 2 / total_orders)
        finally:
            session.close()

    def test_get_product_relations_top_n_limit(self) -> None:
        session = self._session()
        try:
            # LAPTOP-BAG-01 in seed_data co-occurs with MOUSE-WL-02 (4 times)
            # and WEBCAM-1080P (1 time). top_n=1 must keep only MOUSE-WL-02.
            relations = dms.get_product_relations(session, "LAPTOP-BAG-01", top_n=1)
            self.assertEqual(len(relations), 1)
            self.assertEqual(relations[0]["sku"], "MOUSE-WL-02")
            self.assertEqual(relations[0]["co_occurrence_count"], 4)
            # top_n=0 returns an empty list (boundary case).
            self.assertEqual(dms.get_product_relations(session, "LAPTOP-BAG-01", top_n=0), [])
            # Unknown SKU returns an empty list.
            self.assertEqual(dms.get_product_relations(session, "SKU-DOES-NOT-EXIST", top_n=5), [])
        finally:
            session.close()

    # -- SubTask 3.3: score_opportunity ------------------------------------

    def test_score_opportunity_vip_boost(self) -> None:
        # Same inputs except tier; vip must score higher than low.
        common = dict(relation_weight=0.4, intent_confidence=0.8, relation_category_match=True)
        vip_score = dms.score_opportunity(user_value_tier="vip", **common)
        low_score = dms.score_opportunity(user_value_tier="low", **common)
        self.assertGreater(vip_score, low_score)
        # Explicit values:
        #   vip = 0.4 + 0.2 + (0.8 * 0.2) + 0.1 = 0.4 + 0.2 + 0.16 + 0.1 = 0.86
        #   low = 0.4 + 0.0 + (0.8 * 0.2) + 0.1 = 0.4 + 0.0 + 0.16 + 0.1 = 0.66
        self.assertAlmostEqual(vip_score, 0.86)
        self.assertAlmostEqual(low_score, 0.66)
        # Tier ordering must be monotonic: vip > high > medium > low.
        tiers = ["vip", "high", "medium", "low"]
        scores = [dms.score_opportunity(user_value_tier=t, **common) for t in tiers]
        self.assertGreater(scores[0], scores[1])
        self.assertGreater(scores[1], scores[2])
        self.assertGreater(scores[2], scores[3])

    def test_score_opportunity_clamped_to_1(self) -> None:
        # Sum before clamp: 0.95 + 0.2 + (1.0 * 0.2) + 0.1 = 1.45 → clamp to 1.0.
        score = dms.score_opportunity(
            user_value_tier="vip",
            relation_weight=0.95,
            intent_confidence=1.0,
            relation_category_match=True,
        )
        self.assertAlmostEqual(score, 1.0)
        # Negative inputs must clamp to 0 (defensive).
        score_neg = dms.score_opportunity(
            user_value_tier="low",
            relation_weight=-0.5,
            intent_confidence=0.0,
            relation_category_match=False,
        )
        self.assertAlmostEqual(score_neg, 0.0)
        # Unknown tier falls back to the low boost (0.0) — no inflation.
        self.assertAlmostEqual(
            dms.score_opportunity(
                user_value_tier="bogus",
                relation_weight=0.1,
                intent_confidence=0.5,
                relation_category_match=False,
            ),
            0.1 + 0.0 + 0.1 + 0.0,
        )

    def test_score_opportunity_category_match_bonus(self) -> None:
        # Only the category-match flag differs; match=True must score higher.
        base_kwargs = dict(
            user_value_tier="medium",
            relation_weight=0.3,
            intent_confidence=0.5,
        )
        with_match = dms.score_opportunity(relation_category_match=True, **base_kwargs)
        without_match = dms.score_opportunity(relation_category_match=False, **base_kwargs)
        self.assertAlmostEqual(with_match - without_match, 0.1)

    # -- SubTask 3.1+3.4: mine_demand end-to-end ---------------------------

    def test_mine_demand_after_sales_cross_sell(self) -> None:
        # After-sales intent on a product that has co-occurrence data must
        # surface cross-sell opportunities (accessories). After-sales must
        # NOT trigger up-sell (the customer is unhappy, not upgrading).
        session = self._session()
        try:
            result = dms.mine_demand(
                session,
                user_id="u_does_not_exist",
                conversation_context={
                    "message": "我买的笔记本电脑包想退货",
                    "mentioned_skus": ["LAPTOP-BAG-01"],
                    "order_id": "ORD-20260601-001",
                },
            )
            self.assertEqual(result["intent"], "intent:after_sales_return")
            self.assertAlmostEqual(result["intent_confidence"], 0.85)
            self.assertGreater(len(result["opportunities"]), 0)

            cross_sell = [o for o in result["opportunities"] if o["type"] == "cross_sell"]
            self.assertGreater(len(cross_sell), 0)
            cross_sell_skus = {o["target_sku"] for o in cross_sell}
            # MOUSE-WL-02 is the strongest co-occurrence of LAPTOP-BAG-01.
            self.assertIn("MOUSE-WL-02", cross_sell_skus)

            up_sell = [o for o in result["opportunities"] if o["type"] == "up_sell"]
            self.assertEqual(len(up_sell), 0)

            for opp in result["opportunities"]:
                self.assertIn("opportunity_score", opp)
                self.assertGreaterEqual(opp["opportunity_score"], 0.0)
                self.assertLessEqual(opp["opportunity_score"], 1.0)
                self.assertTrue(opp["reason"])
                self.assertIn(opp["type"], {"cross_sell", "up_sell"})
        finally:
            session.close()

    def test_mine_demand_product_inquiry_up_sell(self) -> None:
        # Product-inquiry intent must surface up-sell opportunities: same
        # category, higher-priced. MOUSE-WL-02 (外设, 230 CNY) has multiple
        # higher-priced 外设 siblings (WEBCAM-1080P=250, MOUSE-PAD-XL=1000,
        # MECH-KB-RGB=1999, MONITOR-27-4K=4200).
        session = self._session()
        try:
            result = dms.mine_demand(
                session,
                user_id="u_does_not_exist",
                conversation_context={
                    "message": "我想咨询下无线鼠标",
                    "mentioned_skus": ["MOUSE-WL-02"],
                },
            )
            self.assertEqual(result["intent"], "intent:product_inquiry")
            self.assertAlmostEqual(result["intent_confidence"], 0.75)
            self.assertGreater(len(result["opportunities"]), 0)

            up_sell = [o for o in result["opportunities"] if o["type"] == "up_sell"]
            self.assertGreater(len(up_sell), 0)
            up_sell_skus = {o["target_sku"] for o in up_sell}
            expected_up_sell = {"WEBCAM-1080P", "MOUSE-PAD-XL", "MECH-KB-RGB", "MONITOR-27-4K"}
            self.assertTrue(
                up_sell_skus & expected_up_sell,
                f"expected at least one of {expected_up_sell}, got {up_sell_skus}",
            )
            # Up-sell candidates must be strictly more expensive than the source.
            source_price = 230.0  # MOUSE-WL-02
            for opp in up_sell:
                # Look up the candidate product directly to verify price rule.
                prod = session.query(Product).filter_by(sku=opp["target_sku"]).one()
                self.assertGreater(prod.unit_price, source_price)
                self.assertEqual(prod.category, "外设")
        finally:
            session.close()

    def test_mine_demand_logistics_no_opportunity(self) -> None:
        # Logistics intent must yield no opportunities — even when SKUs and
        # order_id are present, a delivery-status question is not a sales
        # moment.
        session = self._session()
        try:
            result = dms.mine_demand(
                session,
                user_id="u_does_not_exist",
                conversation_context={
                    "message": "我的快递到哪了",
                    "mentioned_skus": ["LAPTOP-BAG-01"],
                    "order_id": "ORD-20260601-001",
                },
            )
            self.assertEqual(result["intent"], "intent:logistics_inquiry")
            self.assertAlmostEqual(result["intent_confidence"], 0.8)
            self.assertEqual(result["opportunities"], [])
        finally:
            session.close()

    def test_mine_demand_complaint_no_opportunity(self) -> None:
        # Complaint intent must not trigger any sales pitch.
        session = self._session()
        try:
            result = dms.mine_demand(
                session,
                user_id="u_does_not_exist",
                conversation_context={
                    "message": "我要投诉这个商品质量",
                    "mentioned_skus": ["MOUSE-WL-02"],
                },
            )
            self.assertEqual(result["intent"], "intent:complaint")
            self.assertAlmostEqual(result["intent_confidence"], 0.9)
            self.assertEqual(result["opportunities"], [])
        finally:
            session.close()

    def test_mine_demand_missing_profile_uses_low_tier(self) -> None:
        # An unknown user_id (no UserProfile) must still produce a normal
        # result; internally user_value_tier falls back to "low".
        session = self._session()
        try:
            result = dms.mine_demand(
                session,
                user_id="u_never_seen_before",
                conversation_context={
                    "message": "我想咨询下无线鼠标",
                    "mentioned_skus": ["MOUSE-WL-02"],
                },
            )
            self.assertEqual(result["intent"], "intent:product_inquiry")
            self.assertGreater(len(result["opportunities"]), 0)
            # With low tier the score equals: base + 0 + (0.75 * 0.2) + 0.1.
            # The base for up-sell without co-occurrence is UP_SELL_FALLBACK_WEIGHT=0.3.
            for opp in result["opportunities"]:
                self.assertGreaterEqual(opp["opportunity_score"], 0.0)
                self.assertLessEqual(opp["opportunity_score"], 1.0)
        finally:
            session.close()

    def test_mine_demand_no_mentioned_skus_returns_empty_opportunities(self) -> None:
        # Without mentioned_skus there is no source product to mine from,
        # even when the intent is opportunity-eligible.
        session = self._session()
        try:
            result = dms.mine_demand(
                session,
                user_id="u_does_not_exist",
                conversation_context={
                    "message": "我想咨询下",
                    "mentioned_skus": [],
                },
            )
            self.assertEqual(result["intent"], "intent:product_inquiry")
            self.assertEqual(result["opportunities"], [])
        finally:
            session.close()

    def test_mine_demand_opportunities_sorted_by_score_desc(self) -> None:
        # Opportunities must come back sorted by opportunity_score descending
        # so the orchestrator can surface the strongest signal first.
        session = self._session()
        try:
            result = dms.mine_demand(
                session,
                user_id="u_does_not_exist",
                conversation_context={
                    "message": "我想咨询下无线鼠标",
                    "mentioned_skus": ["MOUSE-WL-02"],
                },
            )
            scores = [o["opportunity_score"] for o in result["opportunities"]]
            self.assertEqual(scores, sorted(scores, reverse=True))
        finally:
            session.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
