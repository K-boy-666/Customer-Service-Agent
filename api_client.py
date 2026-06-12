"""
HTTP client for the local order REST API.

Reads API_BASE_URL and optional API_KEY from environment variables.
All functions return dicts ready for JSON serialization.
"""

import os
from typing import Any

import httpx

BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")
API_KEY = os.getenv("API_KEY", "")


def _headers() -> dict[str, str]:
    """Build request headers, including auth if API_KEY is set."""
    h = {"Accept": "application/json"}
    if API_KEY:
        h["Authorization"] = f"Bearer {API_KEY}"
    return h


async def _get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Send a GET request to the order API. Returns the parsed JSON body."""
    url = f"{BASE_URL.rstrip('/')}{path}"
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url, headers=_headers(), params=params)
        resp.raise_for_status()
        return resp.json()


async def search_orders(query: str, limit: int = 20) -> list[dict[str, Any]]:
    """Search orders by keyword across order number, customer name, etc."""
    data = await _get("/api/orders/search", {"q": query, "limit": limit})
    # Support both { data: [...] } and bare [...] responses
    return data if isinstance(data, list) else data.get("data", [])


async def get_order(order_id: str) -> dict[str, Any] | None:
    """Fetch a single order by its ID. Returns None if not found."""
    try:
        return await _get(f"/api/orders/{order_id}")
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            return None
        raise


async def list_orders(
    status: str = "all", limit: int = 20, offset: int = 0
) -> dict[str, Any]:
    """List orders, optionally filtered by status."""
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    if status != "all":
        params["status"] = status
    return await _get("/api/orders", params)


async def get_orders_by_date(
    start_date: str, end_date: str, limit: int = 50
) -> list[dict[str, Any]]:
    """Get orders within a date range (ISO format: YYYY-MM-DD)."""
    data = await _get(
        "/api/orders",
        {"start_date": start_date, "end_date": end_date, "limit": limit},
    )
    return data if isinstance(data, list) else data.get("data", [])


async def get_orders_by_customer(
    customer: str, limit: int = 20
) -> list[dict[str, Any]]:
    """Get orders by customer name or ID."""
    data = await _get("/api/orders/by-customer", {"customer": customer, "limit": limit})
    return data if isinstance(data, list) else data.get("data", [])


async def get_order_stats(period: str = "today") -> dict[str, Any]:
    """Get aggregate stats: total count, revenue, breakdown by status."""
    return await _get("/api/orders/stats", {"period": period})


# ---------------------------------------------------------------------------
# Logistics / Shipment functions
# ---------------------------------------------------------------------------


async def get_shipment(order_id: str) -> dict[str, Any] | None:
    """Get logistics tracking info for an order. Returns None if 404."""
    try:
        return await _get(f"/api/orders/{order_id}/shipment")
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            return None
        raise


async def track_by_number(tracking_number: str) -> dict[str, Any] | None:
    """Look up a shipment by its tracking number. Returns None if 404."""
    try:
        return await _get(f"/api/shipments/{tracking_number}")
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            return None
        raise


# ---------------------------------------------------------------------------
# Customer / Membership functions
# ---------------------------------------------------------------------------


async def get_customer(customer_id: str) -> dict[str, Any] | None:
    """Get customer profile + membership info. Returns None if 404."""
    try:
        return await _get(f"/api/customers/{customer_id}")
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            return None
        raise


async def search_customers(query: str, limit: int = 20) -> list[dict[str, Any]]:
    """Search for customers by name, email, or phone number."""
    data = await _get("/api/customers/search", {"q": query, "limit": limit})
    return data if isinstance(data, list) else data.get("data", [])
