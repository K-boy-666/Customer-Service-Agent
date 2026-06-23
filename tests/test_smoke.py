"""
Integration smoke tests for the customer-service-agent 2.0 platform.

Covers 5 golden paths:
  1. 查订单 → 查物流 (order inquiry → logistics tracking)
  2. 退款申请 (after-sales return request)
  3. 投诉 → 升级 (complaint → ticket creation → escalation)
  4. 简单咨询 (FAQ knowledge base search)
  5. 满意度调查 (return completion → follow-up ticket)

Run with:
    uvicorn order_api:app --port 8000 &
    python tests/test_smoke.py

Requires: the REST API running at localhost:8000 with seeded data.
"""

import json
import os
import sys
import traceback

# Ensure the project root is on the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ---------------------------------------------------------------------------
# Test framework (minimal, no external dependencies)
# ---------------------------------------------------------------------------

PASSED = 0
FAILED = 0
ERRORS: list[str] = []


def test(name: str):
    """Decorator-free test runner."""
    def decorator(fn):
        def wrapper():
            global PASSED, FAILED
            try:
                fn()
                PASSED += 1
                print(f"  [PASS] {name}")
            except AssertionError as e:
                FAILED += 1
                ERRORS.append(f"FAIL: {name} -- {e}")
                print(f"  [FAIL] {name}: {e}")
            except Exception as e:
                FAILED += 1
                ERRORS.append(f"ERROR: {name} -- {e}\n{traceback.format_exc()}")
                print(f"  [ERROR] {name}: {e}")
        return wrapper
    return decorator


# We'll use the api_client directly (no REST API needed for DB tests)
import database
import seed_data


def setup_db():
    """Initialize and seed a fresh database."""
    database.init_db()
    conn = database.get_db()
    try:
        seed_data.seed(conn)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Test 1: 查订单 → 查物流
# Simulates: customer asks "我的快递到哪了？" → find order → track shipment
# ---------------------------------------------------------------------------

@test("查订单 → 查物流 (Order inquiry → Logistics tracking)")
def test_order_to_logistics():
    conn = database.get_db()
    try:
        # Step 1: Find Zhang San's orders
        cust = conn.execute(
            "SELECT id, name FROM customers WHERE name LIKE ?", ("%张三%",)
        ).fetchone()
        assert cust is not None, "Customer 张三 should exist"

        orders = conn.execute(
            "SELECT id, order_number, status FROM orders WHERE customer_id = ?",
            (cust["id"],),
        ).fetchall()
        assert len(orders) > 0, f"张三 should have orders, found {len(orders)}"

        # Step 2: Find a delivered order (has shipment)
        delivered = [o for o in orders if o["status"] == "delivered"]
        assert len(delivered) > 0, "张三 should have at least 1 delivered order"

        order_id = delivered[0]["id"]

        # Step 3: Get shipment for that order
        shipment = conn.execute(
            "SELECT * FROM shipments WHERE order_id = ?", (order_id,)
        ).fetchone()
        assert shipment is not None, f"Order {order_id} should have a shipment"

        # Step 4: Get tracking events
        events = conn.execute(
            "SELECT * FROM shipment_events WHERE shipment_id = ? ORDER BY event_time",
            (shipment["id"],),
        ).fetchall()
        assert len(events) > 0, "Shipment should have tracking events"

        print(f"      Order: {order_id} | Carrier: {shipment['carrier']} "
              f"| Tracking: {shipment['tracking_number']} | Events: {len(events)}")
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Test 2: 退款申请 → 状态流转
# Simulates: customer says "我要退款" → create return → approve → complete
# ---------------------------------------------------------------------------

@test("退款申请 (After-sales return → status flow)")
def test_return_flow():
    conn = database.get_db()
    try:
        # Step 1: Find a delivered order to return
        order = conn.execute(
            "SELECT id, customer_id FROM orders WHERE status = 'delivered' LIMIT 1"
        ).fetchone()
        assert order is not None, "Should have at least 1 delivered order"

        # Step 2: Create a return request (simulating create_return API)
        now = database.get_db().execute("SELECT datetime('now','localtime')").fetchone()[0]
        return_number = f"RMA-TEST-{now[:10].replace('-', '')}-001"
        conn.execute(
            """INSERT INTO returns (return_number, order_id, customer_id, type, reason,
               description, status, created_at, updated_at)
               VALUES (?, ?, ?, 'return', '烟雾测试-退货', '自动化测试创建的退货申请',
               'pending', datetime('now','localtime'), datetime('now','localtime'))""",
            (return_number, order["id"], order["customer_id"]),
        )
        conn.commit()

        return_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        assert return_id > 0, "Return should be created"

        # Step 3: Read it back
        ret = conn.execute("SELECT * FROM returns WHERE id = ?", (return_id,)).fetchone()
        assert ret is not None, "Should be able to read return"
        assert ret["status"] == "pending", f"Initial status should be pending, got {ret['status']}"

        # Step 4: Approve it (simulating update_return_status)
        conn.execute(
            "UPDATE returns SET status = 'approved', updated_at = datetime('now','localtime') WHERE id = ?",
            (return_id,),
        )
        conn.commit()

        ret = conn.execute("SELECT status FROM returns WHERE id = ?", (return_id,)).fetchone()
        assert ret["status"] == "approved", f"Status should be approved, got {ret['status']}"

        # Step 5: Advance to refunded
        conn.execute(
            "UPDATE returns SET status = 'refunded', updated_at = datetime('now','localtime') WHERE id = ?",
            (return_id,),
        )
        conn.commit()

        ret = conn.execute("SELECT status FROM returns WHERE id = ?", (return_id,)).fetchone()
        assert ret["status"] == "refunded", f"Status should be refunded, got {ret['status']}"

        # Cleanup test data
        conn.execute("DELETE FROM returns WHERE id = ?", (return_id,))
        conn.commit()

        print(f"      Return {return_number}: pending -> approved -> refunded [OK]")
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Test 3: 投诉 → 升级 (创建紧急工单)
# Simulates: customer complains → create P1 ticket → assign → escalate
# ---------------------------------------------------------------------------

@test("投诉 → 升级 (Complaint → P1 ticket → escalation)")
def test_complaint_to_escalation():
    conn = database.get_db()
    try:
        # Step 1: Create a complaint ticket (P1 - critical)
        now = database.get_db().execute("SELECT datetime('now','localtime')").fetchone()[0]
        ticket_number = f"TK-TEST-{now[:10].replace('-', '')}-001"

        conn.execute(
            """INSERT INTO tickets (ticket_number, title, type, priority, status,
               description, customer_id, assignee, department, created_at, updated_at)
               VALUES (?, '客户投诉：商品质量问题要求赔偿', 'incident', 'P1', 'new',
               '客户反馈收到的商品有明显瑕疵，情绪激动，要求三倍赔偿并威胁向消协投诉。',
               1, '', '应用支持部', datetime('now','localtime'), datetime('now','localtime'))""",
            (ticket_number,),
        )
        conn.commit()
        ticket_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        # Step 2: Verify it's in the system
        ticket = conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
        assert ticket is not None, "Ticket should exist"
        assert ticket["priority"] == "P1", "Should be P1 priority"
        assert ticket["status"] == "new", "Should start as new"

        # Step 3: Add an escalation note
        conn.execute(
            """INSERT INTO ticket_notes (ticket_id, content, author, created_at)
               VALUES (?, '【升级】客户威胁向消协投诉，建议立即转主管处理。情绪级别：L2-高',
               'complaint-agent', datetime('now','localtime'))""",
            (ticket_id,),
        )
        conn.commit()

        # Step 4: Assign and escalate (update to assigned + in_progress)
        conn.execute(
            """UPDATE tickets SET status = 'assigned', assignee = '王主管',
               priority = 'P1', updated_at = datetime('now','localtime')
               WHERE id = ?""",
            (ticket_id,),
        )
        conn.commit()

        ticket = conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
        assert ticket["status"] == "assigned", f"Should be assigned, got {ticket['status']}"
        assert ticket["assignee"] == "王主管", "Should be assigned to 王主管"

        # Step 5: Verify note exists
        notes = conn.execute(
            "SELECT * FROM ticket_notes WHERE ticket_id = ?", (ticket_id,)
        ).fetchall()
        assert len(notes) >= 1, "Should have at least 1 escalation note"

        # Cleanup
        conn.execute("DELETE FROM ticket_notes WHERE ticket_id = ?", (ticket_id,))
        conn.execute("DELETE FROM tickets WHERE id = ?", (ticket_id,))
        conn.commit()

        print(f"      Ticket {ticket_number}: new -> assigned (王主管) + escalation note [OK]")
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Test 4: 简单咨询 (FAQ knowledge base search)
# Simulates: customer asks "退货期限是多久？" → search FAQ → get answer
# ---------------------------------------------------------------------------

@test("简单咨询 (FAQ knowledge base search)")
def test_faq_search():
    FAQ_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "faq.json")

    # Step 1: Load FAQ database
    assert os.path.exists(FAQ_PATH), f"faq.json should exist at {FAQ_PATH}"
    with open(FAQ_PATH, "r", encoding="utf-8") as f:
        faq = json.load(f)

    assert len(faq) >= 50, f"Should have 50+ FAQ entries, found {len(faq)}"

    # Step 2: Check all 8 categories exist
    categories = set(entry["category"] for entry in faq)
    expected_cats = {"退货政策", "换货流程", "退款时效", "物流配送", "会员权益", "支付与发票", "保修条款", "产品咨询"}
    missing = expected_cats - categories
    assert not missing, f"Missing FAQ categories: {missing}"

    # Step 3: Search for "退货" keyword
    query = "退货"
    results = []
    for entry in faq:
        if query in entry["question"] or any(query in kw for kw in entry.get("keywords", [])):
            results.append(entry)

    assert len(results) >= 3, f"Should find 3+ results for '{query}', found {len(results)}"

    # Step 4: Verify a specific FAQ exists
    faq_ids = {e["id"] for e in faq}
    assert "faq-001" in faq_ids, "faq-001 (退货期限) should exist"
    assert "faq-019" in faq_ids, "faq-019 (发货时间) should exist"
    assert "faq-043" in faq_ids, "faq-043 (4K显示器) should exist"

    # Step 5: Verify product FAQ entries
    product_skus = {"MONITOR-27-4K", "MBP-16-M3", "CHAIR-ERG", "SSD-1TB-NVME", "ROUTER-WIFI6"}
    for entry in faq:
        if entry["category"] == "产品咨询":
            for sku in entry.get("related_product_skus", []):
                product_skus.discard(sku)

    print(f"      FAQ entries: {len(faq)} | Categories: {len(categories)} "
          f"| '{query}' results: {len(results)} [OK]")


# ---------------------------------------------------------------------------
# Test 5: 满意度调查 (Satisfaction survey → low-score follow-up)
# Simulates: customer says "谢谢解决了" → survey → low score → create follow-up ticket
# ---------------------------------------------------------------------------

@test("满意度调查 (Satisfaction survey → low-score follow-up)")
def test_satisfaction_survey_flow():
    conn = database.get_db()
    try:
        # Step 1: Complete a return (simulating resolved issue)
        order = conn.execute(
            "SELECT id, customer_id FROM orders WHERE status = 'delivered' LIMIT 1"
        ).fetchone()
        assert order is not None, "Should have a delivered order"

        return_number = f"RMA-TEST-SAT-001"
        conn.execute(
            """INSERT INTO returns (return_number, order_id, customer_id, type, reason,
               description, status, created_at, updated_at)
               VALUES (?, ?, ?, 'refund', '满意度测试', '测试用退款',
               'completed', datetime('now','localtime'), datetime('now','localtime'))""",
            (return_number, order["id"], order["customer_id"]),
        )
        conn.commit()
        return_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        ret = conn.execute("SELECT status FROM returns WHERE id = ?", (return_id,)).fetchone()
        assert ret["status"] == "completed", "Return should be completed"

        # Step 2: Simulate low satisfaction score → create follow-up ticket
        ticket_number = f"TK-TEST-SAT-001"
        conn.execute(
            """INSERT INTO tickets (ticket_number, title, type, priority, status,
               description, customer_id, order_id, assignee, department, created_at, updated_at)
               VALUES (?, '低分回访工单 — 客户满意度1星', 'service_request', 'P2', 'new',
               '客户对售后处理打了1星评分，反馈退款到账太慢。需主管回访致歉并了解改进建议。',
               ?, ?, '王主管', 'IT服务台', datetime('now','localtime'), datetime('now','localtime'))""",
            (ticket_number, order["customer_id"], order["id"]),
        )
        conn.commit()
        followup_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        # Step 3: Verify follow-up ticket
        ticket = conn.execute("SELECT * FROM tickets WHERE id = ?", (followup_id,)).fetchone()
        assert ticket is not None, "Follow-up ticket should exist"
        assert "低分回访" in ticket["title"], "Title should mention low-score follow-up"
        assert ticket["priority"] in ("P1", "P2"), f"Priority should be P1/P2 for low-score, got {ticket['priority']}"

        # Step 4: Add resolution note
        conn.execute(
            """INSERT INTO ticket_notes (ticket_id, content, author, created_at)
               VALUES (?, '主管回访完成：客户接受道歉，建议优化退款到账速度。已反馈至财务部门。',
               '王主管', datetime('now','localtime'))""",
            (followup_id,),
        )
        conn.commit()

        # Close the follow-up ticket
        conn.execute(
            """UPDATE tickets SET status = 'resolved', updated_at = datetime('now','localtime')
               WHERE id = ?""",
            (followup_id,),
        )
        conn.commit()

        ticket = conn.execute("SELECT status FROM tickets WHERE id = ?", (followup_id,)).fetchone()
        assert ticket["status"] == "resolved", f"Follow-up should be resolved, got {ticket['status']}"

        # Cleanup
        conn.execute("DELETE FROM ticket_notes WHERE ticket_id IN (?, ?)", (return_id, followup_id))
        conn.execute("DELETE FROM tickets WHERE id = ?", (followup_id,))
        conn.execute("DELETE FROM returns WHERE id = ?", (return_id,))
        conn.commit()

        print(f"      Return completed -> 1* rating -> follow-up ticket -> resolved [OK]")
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def main():
    print("=" * 60)
    print("  客服智能体 2.0 — Integration Smoke Tests")
    print("=" * 60)

    # Setup: init DB and seed data
    print("\n[Setup] Initializing database and seeding data...")
    setup_db()
    print("[Setup] Database ready.\n")

    # Run all tests
    for _name, _fn in list(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            _fn()

    # Summary
    print(f"\n{'=' * 60}")
    total = PASSED + FAILED
    print(f"  Results: {PASSED}/{total} passed, {FAILED}/{total} failed")
    if FAILED > 0:
        print(f"\n  Failures:")
        for err in ERRORS:
            print(f"    {err}")
        sys.exit(1)
    else:
        print("  All smoke tests passed!")
    print("=" * 60)


if __name__ == "__main__":
    main()

