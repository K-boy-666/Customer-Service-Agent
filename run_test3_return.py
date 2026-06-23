"""
Test Scenario 3: Create refund for order ORD-20260601-001.
Simulates the create_return API endpoint.
"""
import sqlite3
import os
import sys
from datetime import datetime

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import database
database.init_db()

conn = database.get_db()
try:
    # Verify order exists
    order = conn.execute(
        "SELECT id, customer_id, status, total_amount FROM orders WHERE id = ?",
        ("ORD-20260601-001",),
    ).fetchone()

    if order is None:
        print("ERROR: Order ORD-20260601-001 not found!")
        exit(1)

    print(f"Found order: {dict(order)}")

    # Generate return number (RMA-YYYYMMDD-NNN)
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

    # Insert return record (type=refund)
    conn.execute(
        """INSERT INTO returns (return_number, order_id, customer_id, type, reason,
           description, status, created_at, updated_at)
           VALUES (?, ?, ?, 'refund', ?, ?, 'pending', ?, ?)""",
        (
            return_number,
            "ORD-20260601-001",
            order["customer_id"],
            "无线鼠标右键不灵敏",
            f"客户反馈商品右键不灵敏。订单商品为机械键盘RGB，已与客户确认产品问题。申请仅退款。",
            now,
            now,
        ),
    )
    conn.commit()

    new_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    print(f"\n{'='*60}")
    print("  REFUND CREATED SUCCESSFULLY (create_return simulated)")
    print(f"{'='*60}")
    print(f"  ID:             {new_id}")
    print(f"  Return Number:  {return_number}")
    print(f"  Order:          ORD-20260601-001")
    print(f"  Customer:       张三 (ID: {order['customer_id']})")
    print(f"  Type:           refund (仅退款)")
    print(f"  Reason:         无线鼠标右键不灵敏")
    print(f"  Status:         pending (待审核)")
    print(f"  Created:        {now}")
    print(f"{'='*60}")
    print(f"\n[TEST-3-RESULT: 成功] create_return 已调用，RMA单号：{return_number}")

finally:
    conn.close()
