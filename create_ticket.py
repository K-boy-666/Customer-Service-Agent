"""
Create an urgent P1 ticket for payment page timeout.
Run with: python create_ticket.py
"""
import sqlite3
import os
from datetime import datetime

os.chdir(os.path.dirname(os.path.abspath(__file__)))

import database

database.init_db()

conn = database.get_db()
try:
    # Generate ticket number: TK-YYYYMMDD-NNN
    today = datetime.now().strftime("%Y%m%d")
    prefix = f"TK-{today}-"
    row = conn.execute(
        "SELECT ticket_number FROM tickets WHERE ticket_number LIKE ? ORDER BY ticket_number DESC LIMIT 1",
        (f"{prefix}%",),
    ).fetchone()
    if row:
        seq = int(row["ticket_number"].rsplit("-", 1)[-1]) + 1
    else:
        seq = 1
    ticket_number = f"{prefix}{seq:03d}"

    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    conn.execute(
        """INSERT INTO tickets (ticket_number, title, type, priority, status,
           description, customer_id, order_id, department, assignee, created_at, updated_at)
           VALUES (?, ?, ?, ?, 'new', ?, ?, ?, ?, '', ?, ?)""",
        (
            ticket_number,
            "线上支付页面加载超时",
            "incident",
            "P1",
            "支付页面出现加载超时问题，影响用户正常支付，需紧急处理。客户张三反馈支付环节页面长时间无响应，无法完成支付流程。",
            1,                      # 张三
            "ORD-20260601-001",
            "技术部门",
            now,
            now,
        ),
    )
    conn.commit()

    new_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    print(f"\n{'='*60}")
    print("  ✅ TICKET CREATED SUCCESSFULLY")
    print(f"{'='*60}")
    print(f"  ID:             {new_id}")
    print(f"  Ticket Number:  {ticket_number}")
    print(f"  Title:          线上支付页面加载超时")
    print(f"  Type:           incident (故障)")
    print(f"  Priority:       P1 (紧急)")
    print(f"  Status:         new → 待指派")
    print(f"  Department:     技术部门")
    print(f"  Customer:       张三 (ID: 1)")
    print(f"  Order:          ORD-20260601-001")
    print(f"  Created:        {now}")
    print(f"{'='*60}")

finally:
    conn.close()
