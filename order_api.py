"""
Order REST API — SQLite-backed server with realistic sample data.

Run with: uvicorn order_api:app --reload --port 8000
"""

import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Any

import database
import seed_data
from fastapi import FastAPI, HTTPException, Query


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize database and seed on first run."""
    database.init_db()
    if database.is_db_empty():
        seed_data.seed(database.get_db())
    yield


app = FastAPI(title="Order API", version="0.2.0", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_order_dict(row: sqlite3.Row, items: list[dict]) -> dict[str, Any]:
    """Assemble an order response dict from a DB row and its line items."""
    return {
        "id": row["id"],
        "order_number": row["order_number"],
        "customer_name": row["customer_name"],
        "customer_email": row["customer_email"],
        "status": row["status"],
        "total_amount": row["total_amount"],
        "currency": row["currency"],
        "items": items,
        "shipping_address": row["shipping_address"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _fetch_items_for_orders(order_ids: list[str]) -> dict[str, list[dict]]:
    """Batch-fetch line items for a list of order IDs.

    Returns a dict mapping order_id → list of item dicts.
    """
    if not order_ids:
        return {}
    db = database.get_db()
    try:
        placeholders = ",".join("?" for _ in order_ids)
        rows = db.execute(
            f"SELECT * FROM order_items WHERE order_id IN ({placeholders}) ORDER BY id",
            order_ids,
        ).fetchall()
        result: dict[str, list[dict]] = {oid: [] for oid in order_ids}
        for r in rows:
            result[r["order_id"]].append({
                "sku": r["sku"],
                "name": r["name"],
                "qty": r["qty"],
                "price": r["price"],
            })
        return result
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Endpoints — Orders
# ---------------------------------------------------------------------------


@app.get("/api/orders/search")
def search_orders(
    q: str = Query(..., description="Search keyword"),
    limit: int = Query(20, ge=1, le=100),
):
    """Search orders by keyword across order number, customer, item name, etc."""
    db = database.get_db()
    try:
        like = f"%{q}%"
        rows = db.execute(
            """SELECT DISTINCT o.*, c.name AS customer_name, c.email AS customer_email
               FROM orders o
               JOIN customers c ON o.customer_id = c.id
               LEFT JOIN order_items oi ON o.id = oi.order_id
               WHERE o.order_number LIKE ?1
                  OR c.name LIKE ?1
                  OR c.email LIKE ?1
                  OR CAST(o.total_amount AS TEXT) LIKE ?1
                  OR oi.name LIKE ?1
                  OR oi.sku LIKE ?1
               ORDER BY o.created_at DESC
               LIMIT ?2""",
            (like, limit),
        ).fetchall()

        order_ids = [r["id"] for r in rows]
        items_map = _fetch_items_for_orders(order_ids)
        results = [_build_order_dict(r, items_map[r["id"]]) for r in rows]
        return {"data": results, "total": len(results)}
    finally:
        db.close()


@app.get("/api/orders/stats")
def get_order_stats(
    period: str = Query("today", description="today|yesterday|this_week|this_month|last_month|all"),
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

    db = database.get_db()
    try:
        rows = db.execute(
            """SELECT status, COUNT(*) AS cnt, SUM(total_amount) AS revenue
               FROM orders
               WHERE created_at >= ?
               GROUP BY status""",
            (start.isoformat(),),
        ).fetchall()

        by_status: dict[str, int] = {}
        total_revenue = 0.0
        total_count = 0
        for r in rows:
            by_status[r["status"]] = r["cnt"]
            total_count += r["cnt"]
            if r["status"] != "cancelled":
                total_revenue += r["revenue"] or 0.0

        return {
            "period": period,
            "total_count": total_count,
            "total_revenue": round(total_revenue, 2),
            "by_status": by_status,
        }
    finally:
        db.close()


@app.get("/api/orders/by-customer")
def get_orders_by_customer(
    customer: str = Query(...),
    limit: int = Query(20, ge=1, le=100),
):
    """Get orders by customer name or email (partial match)."""
    db = database.get_db()
    try:
        like = f"%{customer}%"
        rows = db.execute(
            """SELECT o.*, c.name AS customer_name, c.email AS customer_email
               FROM orders o
               JOIN customers c ON o.customer_id = c.id
               WHERE c.name LIKE ? OR c.email LIKE ?
               ORDER BY o.created_at DESC
               LIMIT ?""",
            (like, like, limit),
        ).fetchall()

        order_ids = [r["id"] for r in rows]
        items_map = _fetch_items_for_orders(order_ids)
        results = [_build_order_dict(r, items_map[r["id"]]) for r in rows]
        return {"data": results, "total": len(results)}
    finally:
        db.close()


@app.get("/api/orders/{order_id}")
def get_order(order_id: str):
    """Fetch a single order by ID."""
    db = database.get_db()
    try:
        row = db.execute(
            """SELECT o.*, c.name AS customer_name, c.email AS customer_email
               FROM orders o
               JOIN customers c ON o.customer_id = c.id
               WHERE o.id = ?""",
            (order_id,),
        ).fetchone()

        if row is None:
            raise HTTPException(status_code=404, detail=f"Order '{order_id}' not found")

        items = db.execute(
            "SELECT * FROM order_items WHERE order_id = ? ORDER BY id",
            (order_id,),
        ).fetchall()
        item_dicts = [
            {"sku": it["sku"], "name": it["name"], "qty": it["qty"], "price": it["price"]}
            for it in items
        ]
        return _build_order_dict(row, item_dicts)
    finally:
        db.close()


@app.get("/api/orders")
def list_orders(
    status: str = Query("all", description="pending|shipped|delivered|cancelled|all"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    start_date: str | None = Query(None, description="YYYY-MM-DD"),
    end_date: str | None = Query(None, description="YYYY-MM-DD"),
):
    """List orders with optional status and date-range filtering."""
    db = database.get_db()
    try:
        query = """SELECT o.*, c.name AS customer_name, c.email AS customer_email
                   FROM orders o
                   JOIN customers c ON o.customer_id = c.id
                   WHERE 1=1"""
        params: list[Any] = []

        if status != "all":
            query += " AND o.status = ?"
            params.append(status)

        if start_date and end_date:
            query += " AND o.created_at BETWEEN ? AND ?"
            params.append(start_date)
            try:
                end_dt = datetime.fromisoformat(end_date).replace(hour=23, minute=59, second=59)
                params.append(end_dt.isoformat())
            except (ValueError, TypeError):
                params.append(end_date + "T23:59:59")

        # Count total
        count_query = query.replace(
            "SELECT o.*, c.name AS customer_name, c.email AS customer_email",
            "SELECT COUNT(*) AS cnt",
        )
        total = db.execute(count_query, params).fetchone()["cnt"]

        query += " ORDER BY o.created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        rows = db.execute(query, params).fetchall()
        order_ids = [r["id"] for r in rows]
        items_map = _fetch_items_for_orders(order_ids)
        results = [_build_order_dict(r, items_map[r["id"]]) for r in rows]

        return {"data": results, "total": total, "offset": offset, "limit": limit}
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Endpoints — Logistics (NEW)
# ---------------------------------------------------------------------------


@app.get("/api/orders/{order_id}/shipment")
def get_shipment(order_id: str):
    """Get logistics/shipment info for an order, including full tracking timeline."""
    db = database.get_db()
    try:
        ship = db.execute(
            "SELECT * FROM shipments WHERE order_id = ?", (order_id,)
        ).fetchone()

        if ship is None:
            raise HTTPException(
                status_code=404,
                detail=f"No shipment found for order '{order_id}'. "
                        "The order may not have shipped yet.",
            )

        events = db.execute(
            "SELECT * FROM shipment_events WHERE shipment_id = ? ORDER BY event_time",
            (ship["id"],),
        ).fetchall()

        return {
            "order_id": ship["order_id"],
            "carrier": ship["carrier"],
            "tracking_number": ship["tracking_number"],
            "status": ship["status"],
            "estimated_delivery": ship["estimated_delivery"],
            "updated_at": ship["updated_at"],
            "events": [
                {
                    "status": e["status"],
                    "location": e["location"],
                    "description": e["description"],
                    "event_time": e["event_time"],
                }
                for e in events
            ],
        }
    finally:
        db.close()


@app.get("/api/shipments/{tracking_number}")
def track_by_number(tracking_number: str):
    """Look up a shipment by its tracking number."""
    db = database.get_db()
    try:
        ship = db.execute(
            "SELECT * FROM shipments WHERE tracking_number = ?", (tracking_number,)
        ).fetchone()

        if ship is None:
            raise HTTPException(
                status_code=404,
                detail=f"Tracking number '{tracking_number}' not found.",
            )

        events = db.execute(
            "SELECT * FROM shipment_events WHERE shipment_id = ? ORDER BY event_time",
            (ship["id"],),
        ).fetchall()

        return {
            "order_id": ship["order_id"],
            "carrier": ship["carrier"],
            "tracking_number": ship["tracking_number"],
            "status": ship["status"],
            "estimated_delivery": ship["estimated_delivery"],
            "updated_at": ship["updated_at"],
            "events": [
                {
                    "status": e["status"],
                    "location": e["location"],
                    "description": e["description"],
                    "event_time": e["event_time"],
                }
                for e in events
            ],
        }
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Endpoints — Customers / Membership (NEW)
# ---------------------------------------------------------------------------


@app.get("/api/customers/search")
def search_customers(
    q: str = Query(..., description="Search by name, email, or phone"),
    limit: int = Query(20, ge=1, le=100),
):
    """Search customers by name, email, or phone number (partial match)."""
    db = database.get_db()
    try:
        like = f"%{q}%"
        rows = db.execute(
            """SELECT * FROM customers
               WHERE name LIKE ? OR email LIKE ? OR phone LIKE ?
               LIMIT ?""",
            (like, like, like, limit),
        ).fetchall()

        return {
            "data": [
                {
                    "id": r["id"],
                    "name": r["name"],
                    "email": r["email"],
                    "phone": r["phone"],
                    "membership_tier": r["membership_tier"],
                    "points": r["points"],
                    "joined_at": r["joined_at"],
                }
                for r in rows
            ],
            "total": len(rows),
        }
    finally:
        db.close()


@app.get("/api/customers/{customer_id}")
def get_customer(customer_id: int):
    """Get full customer profile including membership tier, points, and order summary."""
    db = database.get_db()
    try:
        cust = db.execute(
            "SELECT * FROM customers WHERE id = ?", (customer_id,)
        ).fetchone()

        if cust is None:
            raise HTTPException(
                status_code=404, detail=f"Customer '{customer_id}' not found."
            )

        # Order summary
        summary = db.execute(
            """SELECT COUNT(*) AS total_orders,
                      SUM(CASE WHEN status != 'cancelled' THEN total_amount ELSE 0 END) AS total_spent
               FROM orders WHERE customer_id = ?""",
            (customer_id,),
        ).fetchone()

        this_month_start = datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        this_month_orders = db.execute(
            "SELECT COUNT(*) AS cnt FROM orders WHERE customer_id = ? AND created_at >= ?",
            (customer_id, this_month_start.isoformat()),
        ).fetchone()["cnt"]

        return {
            "id": cust["id"],
            "name": cust["name"],
            "email": cust["email"],
            "phone": cust["phone"],
            "membership_tier": cust["membership_tier"],
            "points": cust["points"],
            "joined_at": cust["joined_at"],
            "order_summary": {
                "total_orders": summary["total_orders"],
                "total_spent": round(summary["total_spent"] or 0, 2),
                "this_month_orders": this_month_orders,
            },
        }
    finally:
        db.close()
