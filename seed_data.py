"""
Seed the order database with realistic sample data.

Run standalone:  uv run seed_data.py
Also called automatically on first API startup when the database is empty.
"""

import sqlite3
from datetime import datetime, timedelta

import database

TODAY = datetime.now().replace(hour=12, minute=0, second=0, microsecond=0)

# ---------------------------------------------------------------------------
# Customers
# ---------------------------------------------------------------------------

CUSTOMERS = [
    (1, "张三",   "zhangsan@example.com",      "13800138001", "gold",     12500, "2025-01-15T10:00:00"),
    (2, "李四",   "lisi@example.com",          "13800138002", "platinum", 48000, "2024-06-01T09:00:00"),
    (3, "王五",   "wangwu@example.com",        "13800138003", "standard",   800, "2026-03-20T14:30:00"),
    (4, "赵六",   "zhaoliu@example.com",       "13800138004", "silver",    3200, "2025-09-10T11:00:00"),
    (5, "孙七",   "sunqi@example.com",         "13800138005", "gold",      9800, "2025-05-18T08:00:00"),
    (6, "周八",   "zhouba@example.com",        "13800138006", "standard",  1500, "2026-01-05T16:00:00"),
    (7, "吴九",   "wujiu@example.com",         "13800138007", "silver",    4500, "2025-11-22T13:00:00"),
    (8, "郑十",   "zhengshi@example.com",      "13800138008", "platinum", 22000, "2024-03-12T10:30:00"),
    (9, "陈小明", "chenxiaoming@example.com",  "13800138009", "standard",   200, "2026-05-01T15:00:00"),
    (10,"刘小红", "liuxiaohong@example.com",   "13800138010", "gold",      7600, "2025-07-30T12:00:00"),
]

# ---------------------------------------------------------------------------
# Products
# ---------------------------------------------------------------------------

PRODUCTS = [
    ("LAPTOP-BAG-01",  "笔记本电脑包",     "外设",       180.00),
    ("MOUSE-WL-02",    "无线鼠标",         "外设",       230.00),
    ("MONITOR-27-4K",  "27寸4K显示器",    "外设",      4200.00),
    ("HDMI-CBL-2M",    "HDMI线2米",       "线材/转接",  700.00),
    ("USB-HUB-7",      "7口USB集线器",    "线材/转接",  180.50),
    ("CABLE-TYPE-C",   "Type-C数据线",    "线材/转接",  170.00),
    ("MECH-KB-RGB",    "机械键盘RGB",     "外设",      1999.00),
    ("MOUSE-PAD-XL",   "超大鼠标垫",      "外设",      1000.00),
    ("WEBCAM-1080P",   "1080P网络摄像头", "外设",       250.00),
    ("MBP-16-M3",      "MacBook Pro 16 M3","电脑配件",15900.00),
    ("CHAIR-ERG",      "人体工学椅",       "办公家具",  3200.00),
    ("SSD-1TB-NVME",   "1TB NVMe固态硬盘","存储",       780.00),
    ("ROUTER-WIFI6",   "WiFi6路由器",     "网络",       450.00),
    ("DESK-LAMP-LED",  "LED护眼台灯",     "办公家具",    320.00),
    ("EXT-HDD-2TB",    "2TB移动硬盘",     "存储",        550.00),
]

# ---------------------------------------------------------------------------
# Order specs: (customer_idx, day_offset, status, [(sku, qty), ...])
# customer_idx is 0-based into CUSTOMERS
# day_offset: days before TODAY (0 = today)
# ---------------------------------------------------------------------------

ORDER_SPECS = [
    # --- Original 8 orders (preserved, mapped to customers) ---
    # ORD-20260601-001: 张三, delivered, 3 days ago
    (0,  3, "delivered", [("LAPTOP-BAG-01",2), ("MOUSE-WL-02",4)]),
    # ORD-20260602-002: 李四, shipped, 2 days ago
    (1,  2, "shipped",   [("MONITOR-27-4K",1), ("HDMI-CBL-2M",2)]),
    # ORD-20260603-003: 王五, pending, 0 days ago (today)
    (2,  0, "pending",   [("USB-HUB-7",1), ("CABLE-TYPE-C",5)]),
    # ORD-20260604-004: 赵六, pending, 1 day ago
    (3,  1, "pending",   [("MECH-KB-RGB",3), ("MOUSE-PAD-XL",3)]),
    # ORD-20260605-005: 张三, cancelled, 5 days ago
    (0,  5, "cancelled", [("WEBCAM-1080P",1)]),
    # ORD-20260606-006: 孙七, delivered, 7 days ago
    (4,  7, "delivered", [("MBP-16-M3",1)]),
    # ORD-20260607-007: 周八, shipped, 1.125 days ago
    (5,  1, "shipped",   [("CHAIR-ERG",1)]),
    # ORD-20260608-008: 李四, pending, 0 days ago
    (1,  0, "pending",   [("SSD-1TB-NVME",1)]),

    # --- 52 new orders ---
    # Pending (17 more, total 20)
    (2,  0, "pending",   [("ROUTER-WIFI6",2)]),
    (3,  0, "pending",   [("DESK-LAMP-LED",1), ("CABLE-TYPE-C",2)]),
    (5,  0, "pending",   [("EXT-HDD-2TB",1)]),
    (6,  0, "pending",   [("WEBCAM-1080P",2), ("MOUSE-PAD-XL",1)]),
    (7,  1, "pending",   [("MECH-KB-RGB",1)]),
    (8,  0, "pending",   [("LAPTOP-BAG-01",1), ("MOUSE-WL-02",1)]),
    (9,  1, "pending",   [("SSD-1TB-NVME",2)]),
    (0,  0, "pending",   [("HDMI-CBL-2M",3)]),
    (2,  2, "pending",   [("USB-HUB-7",2), ("CABLE-TYPE-C",3)]),
    (3,  3, "pending",   [("DESK-LAMP-LED",2)]),
    (5,  2, "pending",   [("ROUTER-WIFI6",1), ("EXT-HDD-2TB",1)]),
    (6,  3, "pending",   [("CHAIR-ERG",1)]),
    (7,  4, "pending",   [("MOUSE-WL-02",3), ("LAPTOP-BAG-01",1)]),
    (8,  2, "pending",   [("MONITOR-27-4K",1)]),
    (9,  3, "pending",   [("CABLE-TYPE-C",4)]),
    (4,  0, "pending",   [("MECH-KB-RGB",1), ("HDMI-CBL-2M",1)]),
    (5,  4, "pending",   [("WEBCAM-1080P",3)]),

    # Shipped (13 more, total 15)
    (4,  3, "shipped",   [("ROUTER-WIFI6",1), ("DESK-LAMP-LED",1)]),
    (6,  4, "shipped",   [("SSD-1TB-NVME",1)]),
    (7,  5, "shipped",   [("EXT-HDD-2TB",2)]),
    (9,  4, "shipped",   [("USB-HUB-7",1), ("MOUSE-WL-02",2)]),
    (0,  6, "shipped",   [("CHAIR-ERG",1)]),
    (1,  4, "shipped",   [("MECH-KB-RGB",2)]),
    (2,  5, "shipped",   [("MONITOR-27-4K",1)]),
    (3,  6, "shipped",   [("LAPTOP-BAG-01",2), ("WEBCAM-1080P",1)]),
    (8,  5, "shipped",   [("ROUTER-WIFI6",3)]),
    (5,  6, "shipped",   [("CABLE-TYPE-C",5)]),
    (6,  7, "shipped",   [("DESK-LAMP-LED",2), ("EXT-HDD-2TB",1)]),
    (9,  6, "shipped",   [("MBP-16-M3",1)]),
    (4,  5, "shipped",   [("HDMI-CBL-2M",1), ("USB-HUB-7",1)]),

    # Delivered (16 more, total 18)
    (2,  10, "delivered", [("EXT-HDD-2TB",1), ("CABLE-TYPE-C",2)]),
    (3,  12, "delivered", [("CHAIR-ERG",1)]),
    (6,  14, "delivered", [("MOUSE-WL-02",2), ("LAPTOP-BAG-01",1)]),
    (8,   9, "delivered", [("WEBCAM-1080P",1)]),
    (9,  15, "delivered", [("SSD-1TB-NVME",1), ("DESK-LAMP-LED",1)]),
    (0,  11, "delivered", [("MECH-KB-RGB",1)]),
    (5,  18, "delivered", [("MONITOR-27-4K",1)]),
    (7,  16, "delivered", [("ROUTER-WIFI6",2)]),
    (1,  20, "delivered", [("MBP-16-M3",1)]),
    (3,  22, "delivered", [("USB-HUB-7",3)]),
    (6,  21, "delivered", [("HDMI-CBL-2M",2)]),
    (8,  25, "delivered", [("MOUSE-PAD-XL",1), ("WEBCAM-1080P",1)]),
    (4,  28, "delivered", [("EXT-HDD-2TB",1)]),
    (9,  26, "delivered", [("CHAIR-ERG",1)]),
    (0,  30, "delivered", [("SSD-1TB-NVME",1), ("ROUTER-WIFI6",1)]),
    (7,  31, "delivered", [("DESK-LAMP-LED",2)]),

    # Cancelled (6 more, total 7)
    (4,  8, "cancelled", [("USB-HUB-7",1)]),
    (6,  13, "cancelled", [("MECH-KB-RGB",1)]),
    (8,   6, "cancelled", [("MOUSE-PAD-XL",2)]),
    (2,  17, "cancelled", [("HDMI-CBL-2M",1)]),
    (5,  24, "cancelled", [("CABLE-TYPE-C",3)]),
    (9,  19, "cancelled", [("ROUTER-WIFI6",1)]),
]

# ---------------------------------------------------------------------------
# Logistics data
# ---------------------------------------------------------------------------

CARRIERS = ["顺丰速运", "圆通快递", "中通快递", "京东物流", "菜鸟裹裹"]
CARRIER_CODES = ["SF", "YTO", "ZTO", "JD", "CN"]

TRACKING_LOCATIONS = [
    "北京市朝阳分拣中心",
    "北京市海淀营业点",
    "上海市浦东转运中心",
    "上海市徐汇营业点",
    "广州白云集散中心",
    "广州天河营业点",
    "深圳南山营业点",
    "深圳福田中转场",
    "成都双流中转场",
    "成都高新区营业点",
    "杭州萧山分拨中心",
    "杭州西湖营业点",
    "武汉东西湖集散中心",
    "南京江宁分拨中心",
    "西安雁塔中转场",
]

TRACKING_DESCRIPTIONS = {
    "picked_up":        "快件已揽收",
    "in_transit":       "快件已到达【{location}】",
    "out_for_delivery": "快件正在派送中，快递员：{courier}，电话：{phone}",
    "delivered":        "快件已签收，签收人：本人",
}


def _fmt_date(days_offset: int) -> str:
    """Return ISO datetime string for TODAY - days_offset."""
    dt = TODAY - timedelta(days=days_offset)
    return dt.isoformat()


def _order_date(day_offset: int) -> str:
    """Create-at timestamp for an order created `day_offset` days ago."""
    return _fmt_date(day_offset)


def _order_id(day_offset: int, seq: int) -> str:
    """Generate order ID like ORD-YYYYMMDD-NNN."""
    dt = TODAY - timedelta(days=day_offset)
    return f"ORD-{dt.strftime('%Y%m%d')}-{seq:03d}"


def _order_number(day_offset: int, seq: int) -> str:
    """Generate order number like SOYYYYMMDDNNN."""
    dt = TODAY - timedelta(days=day_offset)
    return f"SO{dt.strftime('%Y%m%d')}{seq:03d}"


def _build_shipment_events(
    order_status: str,
    ship_status: str,
    location_idx: int,
) -> list[dict]:
    """Generate a realistic tracking event timeline.

    For 'shipped' orders: picked_up → in_transit (1-2 hops)
    For 'delivered' orders: picked_up → in_transit (2-3 hops) → out_for_delivery → delivered
    """
    events = []
    base = TODAY - timedelta(hours=2)
    loc = lambda i: TRACKING_LOCATIONS[(location_idx + i) % len(TRACKING_LOCATIONS)]

    # 1. Picked up
    ts = base - timedelta(hours=48)
    events.append({
        "status": "picked_up",
        "location": loc(0),
        "description": TRACKING_DESCRIPTIONS["picked_up"],
        "event_time": ts.isoformat(),
    })

    if ship_status in ("in_transit", "shipped"):
        ts = base - timedelta(hours=24)
        events.append({
            "status": "in_transit",
            "location": loc(1),
            "description": TRACKING_DESCRIPTIONS["in_transit"].format(location=loc(1)),
            "event_time": ts.isoformat(),
        })
        ts = base - timedelta(hours=6)
        events.append({
            "status": "in_transit",
            "location": loc(2),
            "description": TRACKING_DESCRIPTIONS["in_transit"].format(location=loc(2)),
            "event_time": ts.isoformat(),
        })

    if ship_status in ("out_for_delivery", "delivered"):
        ts = base - timedelta(hours=30)
        events.append({
            "status": "in_transit",
            "location": loc(1),
            "description": TRACKING_DESCRIPTIONS["in_transit"].format(location=loc(1)),
            "event_time": ts.isoformat(),
        })
        ts = base - timedelta(hours=8)
        events.append({
            "status": "out_for_delivery",
            "location": loc(2),
            "description": TRACKING_DESCRIPTIONS["out_for_delivery"].format(
                courier="李师傅", phone="13900001111"
            ),
            "event_time": ts.isoformat(),
        })

    if ship_status == "delivered":
        ts = base - timedelta(hours=2)
        events.append({
            "status": "delivered",
            "location": loc(3),
            "description": TRACKING_DESCRIPTIONS["delivered"],
            "event_time": ts.isoformat(),
        })

    return events


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def seed(conn: sqlite3.Connection) -> None:
    """Clear all tables and insert seed data. Wrapped in a single transaction."""

    # Clear in reverse dependency order
    conn.execute("DELETE FROM shipment_events")
    conn.execute("DELETE FROM shipments")
    conn.execute("DELETE FROM order_items")
    conn.execute("DELETE FROM orders")
    conn.execute("DELETE FROM products")
    conn.execute("DELETE FROM customers")

    # --- Customers ---
    conn.executemany(
        """INSERT INTO customers (id, name, email, phone, membership_tier, points, joined_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        CUSTOMERS,
    )

    # --- Products ---
    conn.executemany(
        """INSERT INTO products (sku, name, category, unit_price) VALUES (?, ?, ?, ?)""",
        PRODUCTS,
    )

    # --- Orders + Items ---
    # Track sequential IDs per day
    day_counters: dict[int, int] = {}

    for idx, (cust_idx, day_off, status, items) in enumerate(ORDER_SPECS):
        seq = day_counters.get(day_off, 0) + 1
        day_counters[day_off] = seq

        order_id = _order_id(day_off, seq)
        order_no = _order_number(day_off, seq)
        cust_id = CUSTOMERS[cust_idx][0]
        created = _order_date(day_off)
        updated = _order_date(day_off) if status == "pending" else _fmt_date(max(0, day_off - 1))

        total = sum(
            next(p[3] for p in PRODUCTS if p[0] == sku) * qty for sku, qty in items
        )

        addr_map = {
            0: "北京市朝阳区建国路88号",
            1: "上海市浦东新区世纪大道100号",
            2: "广州市天河区天河路385号",
            3: "深圳市南山区科技园南路",
            4: "杭州市西湖区文三路478号",
            5: "成都市武侯区人民南路四段",
            6: "武汉市洪山区珞喻路1037号",
            7: "南京市鼓楼区中山北路200号",
            8: "西安市雁塔区科技路18号",
            9: "重庆市渝中区解放碑步行街",
        }
        addr = addr_map.get(cust_idx, addr_map[0])

        conn.execute(
            """INSERT INTO orders (id, order_number, customer_id, status, total_amount,
               currency, shipping_address, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, 'CNY', ?, ?, ?)""",
            (order_id, order_no, cust_id, status, round(total, 2), addr, created, updated),
        )

        for sku, qty in items:
            prod = next(p for p in PRODUCTS if p[0] == sku)
            conn.execute(
                """INSERT INTO order_items (order_id, sku, name, qty, price)
                   VALUES (?, ?, ?, ?, ?)""",
                (order_id, sku, prod[1], qty, prod[3]),
            )

    # --- Shipments + Events ---
    shipment_idx = 0
    for idx, (cust_idx, day_off, status, _items) in enumerate(ORDER_SPECS):
        if status not in ("shipped", "delivered"):
            continue

        shipment_idx += 1
        day_counters_local = day_counters.copy()  # not needed, just use idx
        seq = list(day_counters.values())[idx % len(day_counters)] if day_counters else 1
        # Recalculate order_id properly:
        # Find the seq for this day_off
        # We need to map back — simpler: recalculate
        _seq = sum(1 for j in range(idx + 1) if ORDER_SPECS[j][1] == day_off)
        order_id = _order_id(day_off, _seq)

        carrier = CARRIERS[idx % len(CARRIERS)]
        code = CARRIER_CODES[idx % len(CARRIER_CODES)]
        tracking = f"{code}{1234567890000 + idx:0>13}"

        ship_status = "in_transit" if status == "shipped" else "delivered"
        est_delivery = _fmt_date(-2) if status == "shipped" else _fmt_date(-1)

        conn.execute(
            """INSERT INTO shipments (order_id, carrier, tracking_number, status,
               estimated_delivery) VALUES (?, ?, ?, ?, ?)""",
            (order_id, carrier, tracking, ship_status, est_delivery),
        )
        ship_db_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        events = _build_shipment_events(status, ship_status, idx)
        for evt in events:
            conn.execute(
                """INSERT INTO shipment_events (shipment_id, status, location,
                   description, event_time) VALUES (?, ?, ?, ?, ?)""",
                (ship_db_id, evt["status"], evt["location"],
                 evt["description"], evt["event_time"]),
            )

    conn.commit()


def main() -> None:
    """Entry point: initialize DB and seed data."""
    database.init_db()
    conn = database.get_db()
    try:
        seed(conn)
        # Verify
        counts = {}
        for table in ["customers","products","orders","order_items","shipments","shipment_events"]:
            row = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
            counts[table] = row["n"]
        print("Seed complete:")
        for t, n in counts.items():
            print(f"  {t}: {n}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
