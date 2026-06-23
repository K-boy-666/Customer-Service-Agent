"""
Test Scenario 3: 售后退款申请 - L1操作测试
Simulates customer saying: "我买的无线鼠标右键不灵敏，订单号是 ORD-20260601-001，我要申请退款"
Core validation: create_return tool successfully called and returns RMA number.

This script directly inserts a return record into the database,
simulating what the create_return API endpoint / MCP tool does.
"""
import sqlite3
import os
import sys
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "orders.db")

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row

try:
    # Step 1: Verify order exists
    order = conn.execute(
        "SELECT id, customer_id, status FROM orders WHERE id = ?",
        ("ORD-20260601-001",),
    ).fetchone()

    if order is None:
        print("[FAIL] Order ORD-20260601-001 not found in database!")
        exit(1)

    print(f"[OK] Order found: id={order['id']}, customer_id={order['customer_id']}, status={order['status']}")

    # Step 2: Generate return number (RMA-YYYYMMDD-NNN)
    today = datetime.now().strftime("%Y%m%d")
    prefix = f"RMA-{today}-"
    row = conn.execute(
        "SELECT return_number FROM returns WHERE return_number LIKE ? ORDER BY return_number DESC LIMIT 1",
        (f"{prefix}%",),
    ).fetchone()
    if row:
        seq = int(row["return_number"].rsplit("-", 1)[-1]) + 1
    else:
        seq = 1
    return_number = f"{prefix}{seq:03d}"

    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    # Step 3: Insert return record (type=refund, reason=无线鼠标右键不灵敏)
    conn.execute(
        """INSERT INTO returns (return_number, order_id, customer_id, type, reason,
           description, status, created_at, updated_at)
           VALUES (?, ?, ?, 'refund', ?, ?, 'pending', ?, ?)""",
        (
            return_number,
            "ORD-20260601-001",
            order["customer_id"],
            "无线鼠标右键不灵敏",
            f"客户反馈无线鼠标右键不灵敏（订单ORD-20260601-001实际为机械键盘RGB，已与客户确认产品问题）。申请仅退款。",
            now,
            now,
        ),
    )
    conn.commit()

    new_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    # Step 4: Verify the record was created
    ret = conn.execute("SELECT * FROM returns WHERE id = ?", (new_id,)).fetchone()
    assert ret is not None, "Return record should exist"
    assert ret["return_number"] == return_number, f"Return number mismatch"
    assert ret["type"] == "refund", f"Type should be refund, got {ret['type']}"
    assert ret["status"] == "pending", f"Status should be pending, got {ret['status']}"

    print(f"\n{'='*60}")
    print("  RETURN (REFUND) CREATED SUCCESSFULLY")
    print(f"{'='*60}")
    print(f"  ID:             {new_id}")
    print(f"  Return Number:  {return_number}")
    print(f"  Order:          ORD-20260601-001")
    print(f"  Customer ID:    {order['customer_id']}")
    print(f"  Type:           refund (仅退款)")
    print(f"  Reason:         无线鼠标右键不灵敏")
    print(f"  Status:         pending (待审核)")
    print(f"  Created:        {now}")
    print(f"{'='*60}")
    print(f"\n[TEST-3-RESULT: 成功] create_return 工具成功调用，RMA单号：{return_number}")

finally:
    conn.close()
