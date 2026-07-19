"""Tests for the revenue attribution service (Task 5 — cs-profit-engine).

Uses a shared-cache in-memory SQLite database so no files are written to
disk. A sentinel connection is held open throughout each test to keep the
in-memory database alive across the ``database`` module's engine cycles.
Mirrors the fixture pattern in ``test_recommendation_service.py`` and the
unittest style of ``test_daily_analytics.py``.

Coverage:
- record_touch_point: persists a TouchPoint row, returns its id.
- attribute_order: four models (first_touch / last_touch / linear /
  time_decay), no-touch-points empty list, outside-24h-window empty list,
  AttributionRecord DB persistence.
- attribute_order_if_in_window: within-window attribution succeeds,
  outside-window returns empty list.
- compute_roi: basic revenue / cost / ROI math, Top Agent ranking,
  Top Script ranking.
- list_attributions: filter by model, filter by user_id.
- get_attribution_summary: multi-model comparison with de-duplicated
  total_orders and total_revenue.
"""

from __future__ import annotations

import os
import sys
import unittest
from datetime import timedelta

from sqlalchemy import create_engine

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import attribution_service as attr
import database
from models import (
    AgentAssistEvent,
    AttributionRecord,
    Customer,
    CustomerServiceUsageEvent,
    Order,
    Recommendation,
    TouchPoint,
    UserProfile,
    now,
)

# Shared-cache in-memory SQLite URI: keeps the DB in RAM and lets multiple
# engines reach the same database. Distinct from the URIs used by other
# test files so each module gets its own in-memory namespace.
IN_MEMORY_URL = "sqlite+pysqlite:///file:attribution_test?mode=memory&cache=shared&uri=true"


class AttributionServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        os.environ["DATABASE_URL"] = IN_MEMORY_URL
        os.environ["AUTH_DEV_SECRET"] = "customer-service-test-secret-min-32-bytes"
        # Sentinel connection keeps the shared in-memory DB alive for the test.
        self._sentinel_engine = create_engine(IN_MEMORY_URL)
        self._sentinel_conn = self._sentinel_engine.connect()
        database.reset_engine_for_tests()
        database.init_db()
        # No seed_data — attribution tests need full control over touch
        # point timing and order amounts, so each test plants its own
        # Customer / UserProfile / Order / TouchPoint rows.

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

    def _make_customer_and_user(
        self,
        session,
        *,
        customer_id: int = 1,
        user_id: str = "u_attr_user",
    ) -> tuple[int, str]:
        """Insert a Customer + UserProfile pair linked by primary_customer_id."""
        session.add(
            Customer(
                id=customer_id,
                name="归因测试客户",
                email=f"attr{customer_id}@example.com",
                phone="13900000001",
                membership_tier="gold",
                points=0,
                joined_at="2026-01-01T00:00:00",
            )
        )
        session.flush()
        session.add(
            UserProfile(
                user_id=user_id,
                primary_customer_id=customer_id,
            )
        )
        session.flush()
        return customer_id, user_id

    def _make_order(
        self,
        session,
        *,
        order_id: str = "ORD-ATTR-001",
        customer_id: int = 1,
        total_amount: float = 1000.0,
        created_at: str = "2026-07-15T12:00:00",
    ) -> str:
        """Insert an Order with a known created_at ISO string."""
        session.add(
            Order(
                id=order_id,
                order_number=f"SO{order_id.replace('-', '')}",
                customer_id=customer_id,
                status="pending",
                total_amount=total_amount,
                currency="CNY",
                shipping_address="测试地址",
                created_at=created_at,
                updated_at=created_at,
            )
        )
        session.flush()
        return order_id

    def _make_touch_point(
        self,
        session,
        *,
        user_id: str,
        conversation_id: str,
        agent_id: str,
        recommendation_id: str | None = None,
        touch_type: str = "conversation",
        touch_time,
    ) -> int:
        """Insert a TouchPoint with an explicit touch_time (datetime)."""
        touch = TouchPoint(
            user_id=user_id,
            conversation_id=conversation_id,
            agent_id=agent_id,
            recommendation_id=recommendation_id,
            touch_type=touch_type,
            touch_time=touch_time,
        )
        session.add(touch)
        session.flush()
        return int(touch.id)

    # -- SubTask 5.1: record_touch_point ---------------------------------

    def test_record_touch_point(self) -> None:
        # record_touch_point must persist a TouchPoint and return its id.
        session = self._session()
        try:
            _, user_id = self._make_customer_and_user(session)
            touch_id = attr.record_touch_point(
                session,
                user_id=user_id,
                conversation_id="conv-1",
                agent_id="agent_a",
                recommendation_id="rec_test_1",
                touch_type="recommendation_exposure",
            )
            session.commit()

            self.assertGreater(touch_id, 0)
            row = session.query(TouchPoint).filter_by(id=touch_id).one()
            self.assertEqual(row.user_id, user_id)
            self.assertEqual(row.conversation_id, "conv-1")
            self.assertEqual(row.agent_id, "agent_a")
            self.assertEqual(row.recommendation_id, "rec_test_1")
            self.assertEqual(row.touch_type, "recommendation_exposure")
            self.assertIsNotNone(row.touch_time)
        finally:
            session.close()

    # -- SubTask 5.1: attribute_order — four models ----------------------

    def test_attribute_order_first_touch(self) -> None:
        # first_touch: 全部归首触点（touch_time 最早的那个）。
        session = self._session()
        try:
            customer_id, user_id = self._make_customer_and_user(session)
            self._make_order(
                session,
                order_id="ORD-FIRST-001",
                customer_id=customer_id,
                total_amount=1000.0,
                created_at="2026-07-15T12:00:00",
            )
            conversion = attr._parse_datetime("2026-07-15T12:00:00")
            self._make_touch_point(
                session,
                user_id=user_id,
                conversation_id="conv-1",
                agent_id="agent_a",
                touch_time=conversion - timedelta(hours=12),
            )
            self._make_touch_point(
                session,
                user_id=user_id,
                conversation_id="conv-2",
                agent_id="agent_b",
                touch_time=conversion - timedelta(hours=6),
            )
            self._make_touch_point(
                session,
                user_id=user_id,
                conversation_id="conv-3",
                agent_id="agent_c",
                touch_time=conversion - timedelta(hours=1),
            )
            session.commit()

            result = attr.attribute_order(session, "ORD-FIRST-001", model="first_touch")
            session.commit()

            self.assertEqual(len(result), 3)
            # 首触点（conv-1）拿全部 1000，其余 0。
            by_conv = {r["conversation_id"]: r for r in result}
            self.assertAlmostEqual(by_conv["conv-1"]["attributed_amount"], 1000.0)
            self.assertAlmostEqual(by_conv["conv-2"]["attributed_amount"], 0.0)
            self.assertAlmostEqual(by_conv["conv-3"]["attributed_amount"], 0.0)
            self.assertAlmostEqual(by_conv["conv-1"]["weight"], 1.0)
            self.assertAlmostEqual(by_conv["conv-2"]["weight"], 0.0)
            self.assertAlmostEqual(by_conv["conv-3"]["weight"], 0.0)
            for r in result:
                self.assertEqual(r["model"], "first_touch")
                self.assertTrue(r["attribution_id"].startswith("attr_"))
        finally:
            session.close()

    def test_attribute_order_last_touch(self) -> None:
        # last_touch: 全部归末触点（touch_time 最晚的那个）。
        session = self._session()
        try:
            customer_id, user_id = self._make_customer_and_user(session)
            self._make_order(
                session,
                order_id="ORD-LAST-001",
                customer_id=customer_id,
                total_amount=1000.0,
                created_at="2026-07-15T12:00:00",
            )
            conversion = attr._parse_datetime("2026-07-15T12:00:00")
            self._make_touch_point(
                session,
                user_id=user_id,
                conversation_id="conv-1",
                agent_id="agent_a",
                touch_time=conversion - timedelta(hours=12),
            )
            self._make_touch_point(
                session,
                user_id=user_id,
                conversation_id="conv-2",
                agent_id="agent_b",
                touch_time=conversion - timedelta(hours=6),
            )
            self._make_touch_point(
                session,
                user_id=user_id,
                conversation_id="conv-3",
                agent_id="agent_c",
                touch_time=conversion - timedelta(hours=1),
            )
            session.commit()

            result = attr.attribute_order(session, "ORD-LAST-001", model="last_touch")
            session.commit()

            self.assertEqual(len(result), 3)
            by_conv = {r["conversation_id"]: r for r in result}
            self.assertAlmostEqual(by_conv["conv-1"]["attributed_amount"], 0.0)
            self.assertAlmostEqual(by_conv["conv-2"]["attributed_amount"], 0.0)
            self.assertAlmostEqual(by_conv["conv-3"]["attributed_amount"], 1000.0)
            self.assertAlmostEqual(by_conv["conv-3"]["weight"], 1.0)
        finally:
            session.close()

    def test_attribute_order_linear(self) -> None:
        # linear: 均分到所有触点。
        session = self._session()
        try:
            customer_id, user_id = self._make_customer_and_user(session)
            self._make_order(
                session,
                order_id="ORD-LIN-001",
                customer_id=customer_id,
                total_amount=999.0,
                created_at="2026-07-15T12:00:00",
            )
            conversion = attr._parse_datetime("2026-07-15T12:00:00")
            for i, hours in enumerate((12, 6, 1)):
                self._make_touch_point(
                    session,
                    user_id=user_id,
                    conversation_id=f"conv-{i}",
                    agent_id=f"agent_{i}",
                    touch_time=conversion - timedelta(hours=hours),
                )
            session.commit()

            result = attr.attribute_order(session, "ORD-LIN-001", model="linear")
            session.commit()

            self.assertEqual(len(result), 3)
            # 每条 ≈ 333.0，weight = 1/3。
            for r in result:
                self.assertAlmostEqual(r["attributed_amount"], 333.0)
                self.assertAlmostEqual(r["weight"], 1.0 / 3.0)
            # 总和等于订单金额。
            total = sum(r["attributed_amount"] for r in result)
            self.assertAlmostEqual(total, 999.0)
        finally:
            session.close()

    def test_attribute_order_time_decay(self) -> None:
        # time_decay: 接近转化的触点权重更大（7 天半衰期）。
        session = self._session()
        try:
            customer_id, user_id = self._make_customer_and_user(session)
            self._make_order(
                session,
                order_id="ORD-TD-001",
                customer_id=customer_id,
                total_amount=1000.0,
                created_at="2026-07-15T12:00:00",
            )
            conversion = attr._parse_datetime("2026-07-15T12:00:00")
            # 三个触点：12h / 6h / 1h 之前。
            self._make_touch_point(
                session,
                user_id=user_id,
                conversation_id="conv-old",
                agent_id="agent_a",
                touch_time=conversion - timedelta(hours=12),
            )
            self._make_touch_point(
                session,
                user_id=user_id,
                conversation_id="conv-mid",
                agent_id="agent_b",
                touch_time=conversion - timedelta(hours=6),
            )
            self._make_touch_point(
                session,
                user_id=user_id,
                conversation_id="conv-near",
                agent_id="agent_c",
                touch_time=conversion - timedelta(hours=1),
            )
            session.commit()

            result = attr.attribute_order(session, "ORD-TD-001", model="time_decay")
            session.commit()

            self.assertEqual(len(result), 3)
            by_conv = {r["conversation_id"]: r for r in result}
            # 越接近转化的权重越大：conv-near > conv-mid > conv-old。
            self.assertGreater(
                by_conv["conv-near"]["weight"],
                by_conv["conv-mid"]["weight"],
            )
            self.assertGreater(
                by_conv["conv-mid"]["weight"],
                by_conv["conv-old"]["weight"],
            )
            # 权重归一化（总和 = 1）。
            weight_sum = sum(r["weight"] for r in result)
            self.assertAlmostEqual(weight_sum, 1.0, places=6)
            # 归因金额总和 = 订单总金额。
            amount_sum = sum(r["attributed_amount"] for r in result)
            self.assertAlmostEqual(amount_sum, 1000.0, places=6)
            # 每条 attributed_amount = total * weight。
            for r in result:
                self.assertAlmostEqual(
                    r["attributed_amount"],
                    1000.0 * r["weight"],
                    places=6,
                )
        finally:
            session.close()

    def test_attribute_order_no_touch_points(self) -> None:
        # 无触点：返回空列表，不写 AttributionRecord。
        session = self._session()
        try:
            customer_id, user_id = self._make_customer_and_user(session)
            self._make_order(
                session,
                order_id="ORD-EMPTY-001",
                customer_id=customer_id,
                total_amount=500.0,
                created_at="2026-07-15T12:00:00",
            )
            session.commit()

            result = attr.attribute_order(session, "ORD-EMPTY-001", model="last_touch")
            session.commit()

            self.assertEqual(result, [])
            self.assertEqual(session.query(AttributionRecord).count(), 0)
        finally:
            session.close()

    def test_attribute_order_outside_window(self) -> None:
        # 触点超出 24h 窗口：不归因（窗口外触点不进入查询结果）。
        session = self._session()
        try:
            customer_id, user_id = self._make_customer_and_user(session)
            self._make_order(
                session,
                order_id="ORD-WIN-001",
                customer_id=customer_id,
                total_amount=500.0,
                created_at="2026-07-15T12:00:00",
            )
            conversion = attr._parse_datetime("2026-07-15T12:00:00")
            # 25h 之前 — 超出 24h 窗口。
            self._make_touch_point(
                session,
                user_id=user_id,
                conversation_id="conv-old",
                agent_id="agent_a",
                touch_time=conversion - timedelta(hours=25),
            )
            session.commit()

            result = attr.attribute_order(session, "ORD-WIN-001", model="last_touch")
            session.commit()

            self.assertEqual(result, [])
            self.assertEqual(session.query(AttributionRecord).count(), 0)
        finally:
            session.close()

    def test_attribute_order_creates_db_records(self) -> None:
        # 归因后 DB 中应有 AttributionRecord 行，且 attribution_id 唯一。
        session = self._session()
        try:
            customer_id, user_id = self._make_customer_and_user(session)
            self._make_order(
                session,
                order_id="ORD-DB-001",
                customer_id=customer_id,
                total_amount=300.0,
                created_at="2026-07-15T12:00:00",
            )
            conversion = attr._parse_datetime("2026-07-15T12:00:00")
            self._make_touch_point(
                session,
                user_id=user_id,
                conversation_id="conv-1",
                agent_id="agent_a",
                touch_time=conversion - timedelta(hours=3),
            )
            self._make_touch_point(
                session,
                user_id=user_id,
                conversation_id="conv-2",
                agent_id="agent_b",
                touch_time=conversion - timedelta(hours=1),
            )
            session.commit()

            result = attr.attribute_order(session, "ORD-DB-001", model="linear")
            session.commit()

            self.assertEqual(len(result), 2)
            db_rows = (
                session.query(AttributionRecord)
                .filter_by(order_id="ORD-DB-001", model="linear")
                .all()
            )
            self.assertEqual(len(db_rows), 2)
            ids = {row.attribution_id for row in db_rows}
            self.assertEqual(len(ids), 2)
            for row in db_rows:
                self.assertTrue(row.attribution_id.startswith("attr_"))
                self.assertEqual(row.order_id, "ORD-DB-001")
                self.assertEqual(row.user_id, user_id)
                self.assertEqual(row.model, "linear")
                self.assertAlmostEqual(row.total_order_amount, 300.0)
                self.assertGreater(row.attributed_amount, 0)
            # linear 均分：每条 150。
            amounts = sorted(row.attributed_amount for row in db_rows)
            self.assertAlmostEqual(amounts[0], 150.0)
            self.assertAlmostEqual(amounts[1], 150.0)
        finally:
            session.close()

    # -- SubTask 5.2: attribute_order_if_in_window -----------------------

    def test_attribute_order_if_in_window_within(self) -> None:
        # 窗口内：触点在订单 created_at 之前 24h 内 → 归因。
        session = self._session()
        try:
            customer_id, user_id = self._make_customer_and_user(session)
            self._make_order(
                session,
                order_id="ORD-SUB-WITHIN-001",
                customer_id=customer_id,
                total_amount=400.0,
                created_at="2026-07-15T12:00:00",
            )
            conversion = attr._parse_datetime("2026-07-15T12:00:00")
            self._make_touch_point(
                session,
                user_id=user_id,
                conversation_id="conv-1",
                agent_id="agent_a",
                touch_time=conversion - timedelta(hours=5),
            )
            session.commit()

            result = attr.attribute_order_if_in_window(
                session, "ORD-SUB-WITHIN-001", model="last_touch"
            )
            session.commit()

            self.assertEqual(len(result), 1)
            self.assertAlmostEqual(result[0]["attributed_amount"], 400.0)
            self.assertEqual(result[0]["model"], "last_touch")
        finally:
            session.close()

    def test_attribute_order_if_in_window_outside(self) -> None:
        # 窗口外：触点在订单 created_at 之前 > 24h → 不归因，返回空列表。
        session = self._session()
        try:
            customer_id, user_id = self._make_customer_and_user(session)
            self._make_order(
                session,
                order_id="ORD-SUB-OUTSIDE-001",
                customer_id=customer_id,
                total_amount=400.0,
                created_at="2026-07-15T12:00:00",
            )
            conversion = attr._parse_datetime("2026-07-15T12:00:00")
            self._make_touch_point(
                session,
                user_id=user_id,
                conversation_id="conv-1",
                agent_id="agent_a",
                touch_time=conversion - timedelta(hours=48),
            )
            session.commit()

            result = attr.attribute_order_if_in_window(
                session, "ORD-SUB-OUTSIDE-001", model="last_touch"
            )
            session.commit()

            self.assertEqual(result, [])
            self.assertEqual(session.query(AttributionRecord).count(), 0)
        finally:
            session.close()

    # -- SubTask 5.3: compute_roi ----------------------------------------

    def test_compute_roi_basic(self) -> None:
        # ROI = (revenue - cost) / cost；成本 = human*5 + ai*0.1。
        session = self._session()
        try:
            customer_id, user_id = self._make_customer_and_user(session)
            self._make_order(
                session,
                order_id="ORD-ROI-001",
                customer_id=customer_id,
                total_amount=1000.0,
                created_at="2026-07-15T12:00:00",
            )
            conversion = attr._parse_datetime("2026-07-15T12:00:00")
            self._make_touch_point(
                session,
                user_id=user_id,
                conversation_id="conv-1",
                agent_id="agent_a",
                touch_time=conversion - timedelta(hours=1),
            )
            session.commit()
            attr.attribute_order(session, "ORD-ROI-001", model="last_touch")
            session.commit()

            # 植入 2 个 AgentAssistEvent 与 10 个 CustomerServiceUsageEvent。
            for i in range(2):
                session.add(
                    AgentAssistEvent(
                        conversation_id=f"conv-assist-{i}",
                        agent_id="agent_a",
                        assist_type="script",
                        content="话术辅助",
                        adopted=1,
                    )
                )
            for i in range(10):
                session.add(
                    CustomerServiceUsageEvent(
                        conversation_id=f"conv-usage-{i}",
                        customer_id=customer_id,
                        order_id=None,
                        status="success",
                        emotional_level="L1",
                        message_length=10,
                        intents=[],
                        dispatched_agents=[],
                        tool_calls=[],
                        needs_human=0,
                    )
                )
            session.commit()

            # 用一个覆盖 now() 的宽窗口查询 ROI。
            start = (now() - timedelta(days=1)).isoformat()
            end = (now() + timedelta(days=1)).isoformat()
            roi = attr.compute_roi(session, start=start, end=end, model="last_touch")

            self.assertAlmostEqual(roi["attributed_revenue"], 1000.0)
            # human = 2 * 5 = 10；ai = 10 * 0.1 = 1；total = 11。
            self.assertAlmostEqual(roi["service_cost"]["human"], 10.0)
            self.assertAlmostEqual(roi["service_cost"]["ai"], 1.0)
            self.assertAlmostEqual(roi["service_cost"]["total"], 11.0)
            # ROI = (1000 - 11) / 11 ≈ 89.9091。
            self.assertAlmostEqual(roi["roi"], (1000.0 - 11.0) / 11.0)
        finally:
            session.close()

    def test_compute_roi_top_agents(self) -> None:
        # Top Agent：按归因营收降序前 5。
        # 每笔订单使用独立的 customer / user，避免共享 user_id 时多笔订单的
        # 触点全部进入同一归因窗口、最后触点夺走全部归因金额。
        session = self._session()
        try:
            orders = [
                ("ORD-TOP-A-001", 1000.0, "agent_a", 1, "u_top_a"),
                ("ORD-TOP-A-002", 500.0, "agent_b", 2, "u_top_b"),
                ("ORD-TOP-A-003", 300.0, "agent_c", 3, "u_top_c"),
            ]
            conversion = attr._parse_datetime("2026-07-15T12:00:00")
            for order_id, amount, agent_id, customer_id, user_id in orders:
                self._make_customer_and_user(
                    session, customer_id=customer_id, user_id=user_id
                )
                self._make_order(
                    session,
                    order_id=order_id,
                    customer_id=customer_id,
                    total_amount=amount,
                    created_at="2026-07-15T12:00:00",
                )
                self._make_touch_point(
                    session,
                    user_id=user_id,
                    conversation_id=f"conv-{order_id}",
                    agent_id=agent_id,
                    touch_time=conversion - timedelta(hours=1),
                )
            session.commit()

            for order_id, _, _, _, _ in orders:
                attr.attribute_order(session, order_id, model="last_touch")
            session.commit()

            start = (now() - timedelta(days=1)).isoformat()
            end = (now() + timedelta(days=1)).isoformat()
            roi = attr.compute_roi(session, start=start, end=end, model="last_touch")

            top_agents = roi["top_agents"]
            self.assertEqual(len(top_agents), 3)
            self.assertEqual(top_agents[0]["agent_id"], "agent_a")
            self.assertAlmostEqual(top_agents[0]["revenue"], 1000.0)
            self.assertEqual(top_agents[1]["agent_id"], "agent_b")
            self.assertAlmostEqual(top_agents[1]["revenue"], 500.0)
            self.assertEqual(top_agents[2]["agent_id"], "agent_c")
            self.assertAlmostEqual(top_agents[2]["revenue"], 300.0)
        finally:
            session.close()

    def test_compute_roi_top_scripts(self) -> None:
        # Top 话术：按 recommendation script 关联的归因营收降序前 5。
        # 每笔订单使用独立的 customer / user，避免共享 user_id 时多笔订单的
        # 触点全部进入同一归因窗口、最后触点夺走全部归因金额。
        session = self._session()
        try:
            scripts = [
                ("rec_script_1", "话术A — 高转化", 800.0, 1, "u_script_a"),
                ("rec_script_2", "话术B — 中转化", 400.0, 2, "u_script_b"),
                ("rec_script_3", "话术C — 低转化", 200.0, 3, "u_script_c"),
            ]
            conversion = attr._parse_datetime("2026-07-15T12:00:00")
            for i, (rec_id, script, amount, customer_id, user_id) in enumerate(scripts):
                order_id = f"ORD-SCRIPT-{i:03d}"
                self._make_customer_and_user(
                    session, customer_id=customer_id, user_id=user_id
                )
                self._make_order(
                    session,
                    order_id=order_id,
                    customer_id=customer_id,
                    total_amount=amount,
                    created_at="2026-07-15T12:00:00",
                )
                session.add(
                    Recommendation(
                        recommendation_id=rec_id,
                        user_id=user_id,
                        conversation_id=f"conv-{order_id}",
                        recommend_type="cross_sell",
                        target_ref=f"SKU-{i}",
                        content="reason",
                        script=script,
                        expected_conversion_rate=0.4,
                        opportunity_score=0.8,
                        status="pending",
                    )
                )
                self._make_touch_point(
                    session,
                    user_id=user_id,
                    conversation_id=f"conv-{order_id}",
                    agent_id="agent_a",
                    recommendation_id=rec_id,
                    touch_time=conversion - timedelta(hours=1),
                )
            session.flush()

            for i, (_, _, _, _, _) in enumerate(scripts):
                order_id = f"ORD-SCRIPT-{i:03d}"
                attr.attribute_order(session, order_id, model="last_touch")
            session.commit()

            start = (now() - timedelta(days=1)).isoformat()
            end = (now() + timedelta(days=1)).isoformat()
            roi = attr.compute_roi(session, start=start, end=end, model="last_touch")

            top_scripts = roi["top_scripts"]
            self.assertEqual(len(top_scripts), 3)
            self.assertEqual(top_scripts[0]["recommendation_id"], "rec_script_1")
            self.assertEqual(top_scripts[0]["script"], "话术A — 高转化")
            self.assertAlmostEqual(top_scripts[0]["revenue"], 800.0)
            self.assertEqual(top_scripts[1]["recommendation_id"], "rec_script_2")
            self.assertAlmostEqual(top_scripts[1]["revenue"], 400.0)
            self.assertEqual(top_scripts[2]["recommendation_id"], "rec_script_3")
            self.assertAlmostEqual(top_scripts[2]["revenue"], 200.0)
        finally:
            session.close()

    # -- SubTask 5.1: list_attributions ----------------------------------

    def test_list_attributions_filter_by_model(self) -> None:
        # 模型过滤：只返回指定模型的归因记录。
        session = self._session()
        try:
            customer_id, user_id = self._make_customer_and_user(session)
            self._make_order(
                session,
                order_id="ORD-FILTER-MODEL-001",
                customer_id=customer_id,
                total_amount=900.0,
                created_at="2026-07-15T12:00:00",
            )
            conversion = attr._parse_datetime("2026-07-15T12:00:00")
            self._make_touch_point(
                session,
                user_id=user_id,
                conversation_id="conv-1",
                agent_id="agent_a",
                touch_time=conversion - timedelta(hours=3),
            )
            self._make_touch_point(
                session,
                user_id=user_id,
                conversation_id="conv-2",
                agent_id="agent_b",
                touch_time=conversion - timedelta(hours=1),
            )
            session.commit()

            # 同一订单在四个模型下都归因 → 4*2 = 8 条记录。
            for model in attr.ATTRIBUTION_MODELS:
                attr.attribute_order(session, "ORD-FILTER-MODEL-001", model=model)
            session.commit()

            # 不带过滤：8 条。
            all_rows = attr.list_attributions(session)
            self.assertEqual(len(all_rows), 8)

            # 过滤 first_touch：2 条。
            first_rows = attr.list_attributions(session, model="first_touch")
            self.assertEqual(len(first_rows), 2)
            for r in first_rows:
                self.assertEqual(r["model"], "first_touch")

            # 过滤 linear：2 条。
            linear_rows = attr.list_attributions(session, model="linear")
            self.assertEqual(len(linear_rows), 2)
            for r in linear_rows:
                self.assertEqual(r["model"], "linear")
        finally:
            session.close()

    def test_list_attributions_filter_by_user(self) -> None:
        # 用户过滤：只返回该用户的归因记录。
        session = self._session()
        try:
            # 两个用户各下一单，各一个触点。
            cid_a, uid_a = self._make_customer_and_user(
                session, customer_id=1, user_id="u_user_a"
            )
            cid_b, uid_b = self._make_customer_and_user(
                session, customer_id=2, user_id="u_user_b"
            )
            self._make_order(
                session,
                order_id="ORD-UA-001",
                customer_id=cid_a,
                total_amount=100.0,
                created_at="2026-07-15T12:00:00",
            )
            self._make_order(
                session,
                order_id="ORD-UB-001",
                customer_id=cid_b,
                total_amount=200.0,
                created_at="2026-07-15T12:00:00",
            )
            conversion = attr._parse_datetime("2026-07-15T12:00:00")
            self._make_touch_point(
                session,
                user_id=uid_a,
                conversation_id="conv-a",
                agent_id="agent_a",
                touch_time=conversion - timedelta(hours=1),
            )
            self._make_touch_point(
                session,
                user_id=uid_b,
                conversation_id="conv-b",
                agent_id="agent_b",
                touch_time=conversion - timedelta(hours=1),
            )
            session.commit()

            attr.attribute_order(session, "ORD-UA-001", model="last_touch")
            attr.attribute_order(session, "ORD-UB-001", model="last_touch")
            session.commit()

            rows_a = attr.list_attributions(session, user_id=uid_a)
            self.assertEqual(len(rows_a), 1)
            self.assertEqual(rows_a[0]["user_id"], uid_a)
            self.assertEqual(rows_a[0]["order_id"], "ORD-UA-001")

            rows_b = attr.list_attributions(session, user_id=uid_b)
            self.assertEqual(len(rows_b), 1)
            self.assertEqual(rows_b[0]["user_id"], uid_b)
            self.assertEqual(rows_b[0]["order_id"], "ORD-UB-001")
        finally:
            session.close()

    # -- SubTask 5.3: get_attribution_summary ----------------------------

    def test_get_attribution_summary_multi_model(self) -> None:
        # 多模型对比：四个模型各有 N 条记录；total_orders / total_revenue 跨模型去重。
        session = self._session()
        try:
            customer_id, user_id = self._make_customer_and_user(session)
            self._make_order(
                session,
                order_id="ORD-SUMMARY-001",
                customer_id=customer_id,
                total_amount=600.0,
                created_at="2026-07-15T12:00:00",
            )
            conversion = attr._parse_datetime("2026-07-15T12:00:00")
            # 2 个触点。
            self._make_touch_point(
                session,
                user_id=user_id,
                conversation_id="conv-1",
                agent_id="agent_a",
                touch_time=conversion - timedelta(hours=3),
            )
            self._make_touch_point(
                session,
                user_id=user_id,
                conversation_id="conv-2",
                agent_id="agent_b",
                touch_time=conversion - timedelta(hours=1),
            )
            session.commit()

            # 四个模型都归因。
            for model in attr.ATTRIBUTION_MODELS:
                attr.attribute_order(session, "ORD-SUMMARY-001", model=model)
            session.commit()

            start = (now() - timedelta(days=1)).isoformat()
            end = (now() + timedelta(days=1)).isoformat()
            summary = attr.get_attribution_summary(session, start=start, end=end)

            # 每个模型都有 2 条记录，营收总和都是 600。
            self.assertEqual(set(summary["models"].keys()), set(attr.ATTRIBUTION_MODELS))
            for model in attr.ATTRIBUTION_MODELS:
                self.assertEqual(summary["models"][model]["record_count"], 2)
                self.assertAlmostEqual(
                    summary["models"][model]["attributed_revenue"], 600.0
                )

            # 跨模型去重：1 个订单、600 总营收（不重复计算 4 个模型的归因金额）。
            self.assertEqual(summary["total_orders"], 1)
            self.assertAlmostEqual(summary["total_revenue"], 600.0)
        finally:
            session.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
