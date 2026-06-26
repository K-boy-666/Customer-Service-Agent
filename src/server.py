"""
MCP server for querying a local order REST API.

Exposes 6 read-only tools for searching, listing, and inspecting orders.
Run with: python server.py   (stdio transport, for Claude Desktop / Claude Code)
"""

import json
import os

# The order-server is internal read-only support. It must never forward scoped
# customer verification tokens; protected customer flows go through the orchestrator.
os.environ.pop("IDENTITY_VERIFICATION", None)

from fastmcp import FastMCP

import api_client

mcp = FastMCP(
    name="order-server",
    version="0.1.0",
    instructions=(
        "This server queries a local order database. "
        "Use search_orders to find orders by keyword when you don't have an exact ID. "
        "Use get_order to fetch full details once you have an order ID. "
        "Use list_orders to browse with optional status filtering. "
        "Use get_order_stats first for an overview of what's in the system. "
        "For logistics: get_shipment (by order ID) or track_by_number (by tracking number). "
        "For membership: get_customer (by customer ID) or search_customers (by name/email/phone)."
    ),
)


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool(
    annotations={
        "readOnlyHint": True,
        "openWorldHint": True,
        "title": "Search orders",
    },
)
async def search_orders(query: str, limit: int = 20) -> str:
    """Search orders by keyword — matches across order number, customer name,
    and other text fields.  Returns up to `limit` results.

    Use this as a first step when you don't have an exact order ID.  Once you
    find the right order, call get_order with its ID for full details.
    """
    results = await api_client.search_orders(query, limit)
    if not results:
        return f"No orders matching '{query}'."
    return json.dumps(results, ensure_ascii=False, indent=2)


@mcp.tool(
    annotations={
        "readOnlyHint": True,
        "openWorldHint": True,
        "title": "Get order by ID",
    },
)
async def get_order(order_id: str) -> str:
    """Fetch a single order by its unique ID.  Returns the full order record
    including line items, totals, customer info, and status.

    If you don't know the ID, use search_orders or list_orders first.
    """
    order = await api_client.get_order(order_id)
    if order is None:
        return f"Order '{order_id}' not found. Use search_orders to find valid IDs."
    return json.dumps(order, ensure_ascii=False, indent=2)


@mcp.tool(
    annotations={
        "readOnlyHint": True,
        "openWorldHint": True,
        "title": "List orders",
    },
)
async def list_orders(status: str = "all", limit: int = 20, offset: int = 0) -> str:
    """List orders with optional status filtering and pagination.

    `status` accepts values like "pending", "shipped", "delivered", "cancelled",
    or "all" (default) to include every status.
    Use `offset` to page through results (e.g. 0, then 20, then 40).
    """
    data = await api_client.list_orders(status, limit, offset)
    orders = data if isinstance(data, list) else data.get("data", [])
    total = data if isinstance(data, list) else data.get("total", len(orders))

    if not orders:
        return f"No orders found (status={status}, offset={offset})."

    out = {
        "total": total,
        "returned": len(orders),
        "offset": offset,
        "orders": orders,
    }
    return json.dumps(out, ensure_ascii=False, indent=2)


@mcp.tool(
    annotations={
        "readOnlyHint": True,
        "openWorldHint": True,
        "title": "Get orders by date range",
    },
)
async def get_orders_by_date(
    start_date: str, end_date: str, limit: int = 50
) -> str:
    """Get orders created within a date range.  Dates must be ISO format
    (YYYY-MM-DD, e.g. "2026-01-01").  The range is inclusive.

    Use this when you need orders from a specific time window — for example
    "all orders from last week" or "orders placed in Q1".
    """
    results = await api_client.get_orders_by_date(start_date, end_date, limit)
    if not results:
        return f"No orders between {start_date} and {end_date}."
    return json.dumps(results, ensure_ascii=False, indent=2)


@mcp.tool(
    annotations={
        "readOnlyHint": True,
        "openWorldHint": True,
        "title": "Get orders by customer",
    },
)
async def get_orders_by_customer(customer: str, limit: int = 20) -> str:
    """Get orders placed by a specific customer.  `customer` can be a name,
    email, or customer ID — the API matches partial inputs.

    If you only have a partial name, this still works.  Use search_orders
    as an alternative when you're not sure whether the text is a customer name
    or an order number.
    """
    results = await api_client.get_orders_by_customer(customer, limit)
    if not results:
        return f"No orders found for customer '{customer}'."
    return json.dumps(results, ensure_ascii=False, indent=2)


@mcp.tool(
    annotations={
        "readOnlyHint": True,
        "openWorldHint": True,
        "title": "Get order statistics",
    },
)
async def get_order_stats(period: str = "today") -> str:
    """Get aggregate order statistics for a time period.

    `period` accepts: "today", "yesterday", "this_week", "this_month",
    "last_month", or "all".
    Returns total count, total revenue, and a breakdown by order status.
    Call this first for a high-level overview before drilling into details.
    """
    stats = await api_client.get_order_stats(period)
    return json.dumps(stats, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Logistics / Shipment tools
# ---------------------------------------------------------------------------


@mcp.tool(
    annotations={
        "readOnlyHint": True,
        "openWorldHint": True,
        "title": "Get shipment tracking by order",
    },
)
async def get_shipment(order_id: str) -> str:
    """Get logistics tracking information for an order. Returns carrier,
    tracking number, current shipment status, estimated delivery date,
    and a chronological list of all tracking events (location + description + time).

    Use this when a customer asks where their package is, e.g. "我的快递到哪了"
    or "物流信息是什么". Requires a known order ID — use search_orders or
    get_orders_by_customer first if you don't have one.
    """
    shipment = await api_client.get_shipment(order_id)
    if shipment is None:
        return (
            f"No shipment found for order '{order_id}'. "
            "The order may not have shipped yet, or the order ID may be incorrect. "
            "Use get_order to check the order status first."
        )
    return json.dumps(shipment, ensure_ascii=False, indent=2)


@mcp.tool(
    annotations={
        "readOnlyHint": True,
        "openWorldHint": True,
        "title": "Track by tracking number",
    },
)
async def track_by_number(tracking_number: str) -> str:
    """Look up a shipment directly by its tracking number (运单号).
    Returns carrier, status, estimated delivery, and full event timeline.

    Use this when a customer gives you a tracking number directly,
    e.g. "帮我查一下 SF1234567890123".
    """
    shipment = await api_client.track_by_number(tracking_number)
    if shipment is None:
        return (
            f"Tracking number '{tracking_number}' not found. "
            "Please verify the number and try again."
        )
    return json.dumps(shipment, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Customer / Membership tools
# ---------------------------------------------------------------------------


@mcp.tool(
    annotations={
        "readOnlyHint": True,
        "openWorldHint": True,
        "title": "Get customer profile",
    },
)
async def get_customer(customer_id: str) -> str:
    """Get a customer's full profile including membership tier, points balance,
    join date, and order summary (total orders, total spent, this month's orders).

    Use this when a customer asks about their membership, e.g. "我是什么会员等级"
    or "我有多少积分". The customer_id can be found from order records —
    use search_customers if you need to find a customer by name/email/phone first.
    """
    customer = await api_client.get_customer(customer_id)
    if customer is None:
        return (
            f"Customer '{customer_id}' not found. "
            "Use search_customers to find the customer by name, email, or phone."
        )
    return json.dumps(customer, ensure_ascii=False, indent=2)


@mcp.tool(
    annotations={
        "readOnlyHint": True,
        "openWorldHint": True,
        "title": "Search customers",
    },
)
async def search_customers(query: str, limit: int = 20) -> str:
    """Search for customers by name, email, or phone number (partial match).
    Returns matching customer profiles with membership tier and points.

    Use this when a customer provides their name/email/phone but you don't
    have their customer ID yet. Once you find the customer, use get_customer
    for full details, or get_orders_by_customer for their order history.
    """
    results = await api_client.search_customers(query, limit)
    if not results:
        return f"No customers matching '{query}'."
    return json.dumps(results, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main():
    """Run the MCP server over stdio (default for Claude Desktop / Claude Code)."""
    mcp.run()


if __name__ == "__main__":
    main()
