"""
Self-contained script to create a return for order ORD-20260601-001.
Run with: python run_create_return.py
"""
import sqlite3
import os
import sys
from datetime import datetime

# Ensure we're in the project directory
os.chdir(os.path.dirname(os.path.abspath(__file__)))

# Add project to path so we can import database module
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import database

# Initialize DB if needed
database.init_db()

conn = database.get_db()
try:
    # Verify order exists
    order = conn.execute(
        "SELECT id, customer_id, status, total_amount, created_at FROM orders WHERE id = ?",
        ("ORD-20260601-001",),
    ).fetchone()

    if order is None:
        print(f"ERROR: Order ORD-20260601-001 not found in database!")
        # Check what orders exist
        all_orders = conn.execute("SELECT id, customer_id, status FROM orders LIMIT 10").fetchall()
        for o in all_orders:
            print(f"  Existing order: {dict(o)}")
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

    # Insert return record
    conn.execute(
        """INSERT INTO returns (return_number, order_id, customer_id, type, reason,
           description, status, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)""",
        (
            return_number,
            "ORD-20260601-001",
            1,  # 张三
            "return",
            "质量问题（按键不灵敏）",
            "机械键盘RGB按键不灵敏，属于质量问题。签收第10天，在7-15天退换货政策范围内。因质量问题退货，运费由商家承担。",
            now,
            now,
        ),
    )
    conn.commit()

    new_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    print(f"\n{'='*60}")
    print("  RETURN CREATED SUCCESSFULLY")
    print(f"{'='*60}")
    print(f"  ID:             {new_id}")
    print(f"  Return Number:  {return_number}")
    print(f"  Order:          ORD-20260601-001")
    print(f"  Customer:       张三 (ID: 1)")
    print(f"  Type:           return (退货)")
    print(f"  Reason:         质量问题（按键不灵敏）")
    print(f"  Description:    机械键盘RGB按键不灵敏，属于质量问题")
    print(f"  Status:         pending (待审核)")
    print(f"  Created:        {now}")
    print(f"{'='*60}")

finally:
    conn.close()
