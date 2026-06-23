"""
Order REST API — FastAPI app with in-memory sample data.

Run with: uvicorn order_api:app --reload --port 8000

Replace the ORDERS list with your database (SQLite, Postgres, etc.).
"""

from datetime import datetime, timedelta
from typing import Any

from fastapi import FastAPI, HTTPException, Query

app = FastAPI(title="Order API", version="0.1.0")

# ---------------------------------------------------------------------------
# Sample data — replace with your database
# ---------------------------------------------------------------------------

NOW = datetime.now()

ORDERS: list[dict[str, Any]] = [
    {
        "id": "ORD-20260601-001",
        "order_number": "SO20260601001",
        "customer_name": "张三",
        "customer_email": "zhangsan@example.com",
        "status": "delivered",
        "total_amount": 1280.00,
        "currency": "CNY",
        "items": [
            {"sku": "LAPTOP-BAG-01", "name": "笔记本电脑包", "qty": 2, "price": 180.00},
            {"sku": "MOUSE-WL-02", "name": "无线鼠标", "qty": 4, "price": 230.00},
        ],
        "shipping_address": "北京市朝阳区建国路88号",
        "created_at": (NOW - timedelta(days=3)).isoformat(),
        "updated_at": (NOW - timedelta(days=1)).isoformat(),
    },
    {
        "id": "ORD-20260602-002",
        "order_number": "SO20260602002",
        "customer_name": "李四",
        "customer_email": "lisi@example.com",
        "status": "shipped",
        "total_amount": 5600.00,
        "currency": "CNY",
        "items": [
            {"sku": "MONITOR-27-4K", "name": "27寸4K显示器", "qty": 1, "price": 4200.00},
            {"sku": "HDMI-CBL-2M", "name": "HDMI线2米", "qty": 2, "price": 700.00},
        ],
        "shipping_address": "上海市浦东新区世纪大道100号",
        "created_at": (NOW - timedelta(days=2)).isoformat(),
        "updated_at": (NOW - timedelta(hours=12)).isoformat(),
    },
    {
        "id": "ORD-20260603-003",
        "order_number": "SO20260603003",
        "customer_name": "王五",
        "customer_email": "wangwu@example.com",
        "status": "pending",
        "total_amount": 350.50,
        "currency": "CNY",
        "items": [
            {"sku": "USB-HUB-7", "name": "7口USB集线器", "qty": 1, "price": 180.50},
            {"sku": "CABLE-TYPE-C", "name": "Type-C数据线", "qty": 5, "price": 170.00},
        ],
        "shipping_address": "广州市天河区天河路385号",
        "created_at": (NOW - timedelta(hours=6)).isoformat(),
        "updated_at": (NOW - timedelta(hours=6)).isoformat(),
    },
    {
        "id": "ORD-20260604-004",
        "order_number": "SO20260604004",
        "customer_name": "赵六",
        "customer_email": "zhaoliu@example.com",
        "status": "pending",
        "total_amount": 8999.00,
        "currency": "CNY",
        "items": [
            {"sku": "MECH-KB-RGB", "name": "机械键盘RGB", "qty": 3, "price": 1999.00},
            {"sku": "MOUSE-PAD-XL", "name": "超大鼠标垫", "qty": 3, "price": 1000.00},
        ],
        "shipping_address": "深圳市南山区科技园南路",
        "created_at": (NOW - timedelta(days=1)).isoformat(),
        "updated_at": (NOW - timedelta(days=1)).isoformat(),
    },
    {
        "id": "ORD-20260605-005",
        "order_number": "SO20260605005",
        "customer_name": "张三",
        "customer_email": "zhangsan@example.com",
        "status": "cancelled",
        "total_amount": 250.00,
        "currency": "CNY",
        "items": [
            {"sku": "WEBCAM-1080P", "name": "1080P网络摄像头", "qty": 1, "price": 250.00},
        ],
        "shipping_address": "北京市朝阳区建国路88号",
        "created_at": (NOW - timedelta(days=5)).isoformat(),
        "updated_at": (NOW - timedelta(days=4)).isoformat(),
    },
    {
        "id": "ORD-20260606-006",
        "order_number": "SO20260606006",
        "customer_name": "孙七",
        "customer_email": "sunqi@example.com",
        "status": "delivered",
        "total_amount": 15900.00,
        "currency": "CNY",
        "items": [
            {"sku": "MBP-16-M3", "name": "MacBook Pro 16 M3", "qty": 1, "price": 15900.00},
        ],
        "shipping_address": "杭州市西湖区文三路478号",
        "created_at": (NOW - timedelta(days=7)).isoformat(),
        "updated_at": (NOW - timedelta(days=5)).isoformat(),
    },
    {
        "id": "ORD-20260607-007",
        "order_number": "SO20260607007",
        "customer_name": "周八",
        "customer_email": "zhouba@example.com",
        "status": "shipped",
        "total_amount": 3200.00,
        "currency": "CNY",
        "items": [
            {"sku": "CHAIR-ERG", "name": "人体工学椅", "qty": 1, "price": 3200.00},
        ],
        "shipping_address": "成都市武侯区人民南路四段",
        "created_at": (NOW - timedelta(days=1, hours=3)).isoformat(),
        "updated_at": (NOW - timedelta(hours=3)).isoformat(),
    },
    {
        "id": "ORD-20260608-008",
        "order_number": "SO20260608008",
        "customer_name": "李四",
        "customer_email": "lisi@example.com",
        "status": "pending",
        "total_amount": 780.00,
        "currency": "CNY",
        "items": [
            {"sku": "SSD-1TB-NVME", "name": "1TB NVMe固态硬盘", "qty": 1, "price": 780.00},
        ],
        "shipping_address": "上海市浦东新区世纪大道100号",
        "created_at": (NOW - timedelta(hours=2)).isoformat(),
        "updated_at": (NOW - timedelta(hours=2)).isoformat(),
    },
]

VALID_STATUSES = {"pending", "shipped", "delivered", "cancelled"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _match_order(order: dict, query: str) -> bool:
    """Check if an order matches a search query across all text fields."""
    q = query.lower()
    return (
        q in order["order_number"].lower()
        or q in order["customer_name"].lower()
        or q in order["customer_email"].lower()
        or q in str(order["total_amount"])
        or any(q in item["name"].lower() for item in order["items"])
        or any(q in item["sku"].lower() for item in order["items"])
    )


def _filter_by_date(orders: list[dict], start: str, end: str) -> list[dict]:
    """Filter orders by created_at date range (inclusive)."""
    try:
        start_dt = datetime.fromisoformat(start)
        end_dt = datetime.fromisoformat(end).replace(hour=23, minute=59, second=59)
    except (ValueError, TypeError):
        return orders
    return [
        o
        for o in orders
        if start_dt <= datetime.fromisoformat(o["created_at"]) <= end_dt
    ]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/api/orders/search")
def search_orders(
    q: str = Query(..., description="Search keyword"),
    limit: int = Query(20, ge=1, le=100),
):
    """Search orders by keyword across order number, customer, item name, etc."""
    results = [o for o in ORDERS if _match_order(o, q)]
    return {"data": results[:limit], "total": len(results)}


@app.get("/api/orders/stats")
def get_order_stats(
    period: str = Query("today", description="today|yesterday|this_week|this_month|all"),
):
    """Get aggregate order statistics."""
    now = datetime.now()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    if period == "yesterday":
        start = start - timedelta(days=1)
    elif period == "this_week":
        start = start - timedelta(days=start.weekday())
    elif period == "this_month":
        start = start.replace(day=1)
    elif period == "last_month":
        first = start.replace(day=1)
        start = (first - timedelta(days=1)).replace(day=1)
    elif period == "all":
        start = datetime(2000, 1, 1)

    filtered = [o for o in ORDERS if datetime.fromisoformat(o["created_at"]) >= start]

    by_status: dict[str, int] = {}
    total_revenue = 0.0
    for o in filtered:
        by_status[o["status"]] = by_status.get(o["status"], 0) + 1
        if o["status"] != "cancelled":
            total_revenue += o["total_amount"]

    return {
        "period": period,
        "total_count": len(filtered),
        "total_revenue": round(total_revenue, 2),
        "by_status": by_status,
    }


@app.get("/api/orders/by-customer")
def get_orders_by_customer(
    customer: str = Query(...),
    limit: int = Query(20, ge=1, le=100),
):
    """Get orders by customer name or email (partial match)."""
    q = customer.lower()
    results = [
        o
        for o in ORDERS
        if q in o["customer_name"].lower() or q in o["customer_email"].lower()
    ]
    return {"data": results[:limit], "total": len(results)}


@app.get("/api/orders/{order_id}")
def get_order(order_id: str):
    """Fetch a single order by ID, including line items and totals."""
    for o in ORDERS:
        if o["id"] == order_id:
            return o
    raise HTTPException(status_code=404, detail=f"Order '{order_id}' not found")


@app.get("/api/orders")
def list_orders(
    status: str = Query("all", description="pending|shipped|delivered|cancelled|all"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    start_date: str | None = Query(None, description="YYYY-MM-DD"),
    end_date: str | None = Query(None, description="YYYY-MM-DD"),
):
    """List orders with optional status and date-range filtering."""
    results = ORDERS

    if status != "all":
        results = [o for o in results if o["status"] == status]

    if start_date and end_date:
        results = _filter_by_date(results, start_date, end_date)

    total = len(results)
    paged = results[offset : offset + limit]

    return {"data": paged, "total": total, "offset": offset, "limit": limit}
