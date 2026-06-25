"""
HTTP client for the local order REST API.

Reads API_BASE_URL and optional API_KEY from environment variables.
All functions return dicts ready for JSON serialization.
"""

import os
import uuid
from typing import Any

import httpx

BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")
API_KEY = os.getenv("API_KEY", "")
IDENTITY_VERIFICATION = os.getenv("IDENTITY_VERIFICATION", "")


def _headers() -> dict[str, str]:
    """Build request headers, including auth if API_KEY is set."""
    h = {"Accept": "application/json"}
    if API_KEY:
        h["Authorization"] = f"Bearer {API_KEY}"
    if IDENTITY_VERIFICATION:
        h["X-Identity-Verification"] = IDENTITY_VERIFICATION
    return h


async def _get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Send a GET request to the order API. Returns the parsed JSON body."""
    url = f"{BASE_URL.rstrip('/')}{path}"
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url, headers=_headers(), params=params)
        if resp.status_code in (401, 403):
            return {"error": "permission_denied", "status_code": resp.status_code, "detail": resp.text}
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
async def get_usage_analytics(date: str = "yesterday") -> dict[str, Any]:
    """Get aggregate customer-service usage analytics for a report date."""
    params: dict[str, Any] = {}
    if date:
        params["date"] = date
    return await _get("/api/analytics/usage", params)


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


# ---------------------------------------------------------------------------
# Internal helpers 鈥?POST / PATCH
# ---------------------------------------------------------------------------


async def _post(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Send a POST request to the order API. Returns the parsed JSON body."""
    url = f"{BASE_URL.rstrip('/')}{path}"
    headers = _headers()
    headers["Idempotency-Key"] = str(uuid.uuid4())
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(url, headers=headers, params=params)
        if resp.status_code in (401, 403):
            return {"error": "permission_denied", "status_code": resp.status_code, "detail": resp.text}
        resp.raise_for_status()
        return resp.json()


async def _patch(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Send a PATCH request to the order API. Returns the parsed JSON body."""
    url = f"{BASE_URL.rstrip('/')}{path}"
    headers = _headers()
    headers["Idempotency-Key"] = str(uuid.uuid4())
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.patch(url, headers=headers, params=params)
        if resp.status_code in (401, 403):
            return {"error": "permission_denied", "status_code": resp.status_code, "detail": resp.text}
        resp.raise_for_status()
        return resp.json()


# ---------------------------------------------------------------------------
# Ticket functions
# ---------------------------------------------------------------------------


async def create_ticket(
    title: str,
    type: str = "incident",
    priority: str = "P3",
    description: str = "",
    customer_id: int | None = None,
    order_id: str | None = None,
) -> dict[str, Any]:
    """Create a new work ticket."""
    params: dict[str, Any] = {
        "title": title,
        "type": type,
        "priority": priority,
        "description": description,
    }
    if customer_id:
        params["customer_id"] = customer_id
    if order_id:
        params["order_id"] = order_id
    return await _post("/api/tickets", params)


async def get_ticket(ticket_id: int) -> dict[str, Any] | None:
    """Get a ticket by ID with its notes. Returns None if not found."""
    try:
        return await _get(f"/api/tickets/{ticket_id}")
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            return None
        raise


async def list_tickets(
    status: str | None = None,
    assignee: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> dict[str, Any]:
    """List tickets with optional filtering."""
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    if status:
        params["status"] = status
    if assignee:
        params["assignee"] = assignee
    return await _get("/api/tickets", params)


async def update_ticket(
    ticket_id: int,
    status: str | None = None,
    assignee: str | None = None,
    priority: str | None = None,
    note: str | None = None,
) -> dict[str, Any] | None:
    """Update a ticket. Returns None if not found."""
    params: dict[str, Any] = {}
    if status:
        params["status"] = status
    if assignee is not None:
        params["assignee"] = assignee
    if priority:
        params["priority"] = priority
    if note:
        params["note"] = note
    try:
        return await _patch(f"/api/tickets/{ticket_id}", params)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            return None
        raise


async def add_ticket_note(
    ticket_id: int, content: str, author: str = "system"
) -> dict[str, Any]:
    """Add a note to a ticket."""
    return await _post(
        f"/api/tickets/{ticket_id}/notes",
        {"content": content, "author": author},
    )


async def search_tickets(query: str, limit: int = 20) -> list[dict[str, Any]]:
    """Search tickets by keyword across title, description, and ticket number."""
    data = await _get("/api/tickets/search", {"q": query, "limit": limit})
    return data if isinstance(data, list) else data.get("data", [])


# ---------------------------------------------------------------------------
# Return / After-Sales functions
# ---------------------------------------------------------------------------


async def create_return(
    order_id: str,
    reason: str,
    type: str = "return",
    description: str = "",
    customer_id: int | None = None,
) -> dict[str, Any]:
    """Create a return/exchange/refund request."""
    params: dict[str, Any] = {
        "order_id": order_id,
        "type": type,
        "reason": reason,
        "description": description,
    }
    if customer_id:
        params["customer_id"] = customer_id
    return await _post("/api/returns", params)


async def get_return(return_id: int) -> dict[str, Any] | None:
    """Get a return request by ID. Returns None if not found."""
    try:
        return await _get(f"/api/returns/{return_id}")
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            return None
        raise


async def list_returns(
    status: str | None = None,
    customer_id: int | None = None,
    limit: int = 20,
    offset: int = 0,
) -> dict[str, Any]:
    """List returns with optional filtering."""
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    if status:
        params["status"] = status
    if customer_id:
        params["customer_id"] = customer_id
    return await _get("/api/returns", params)


async def update_return_status(
    return_id: int, status: str, note: str | None = None
) -> dict[str, Any] | None:
    """Update a return request status. Returns None if not found."""
    params: dict[str, Any] = {"status": status}
    if note:
        params["note"] = note
    try:
        return await _patch(f"/api/returns/{return_id}", params)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            return None
        raise


async def get_order_returns(order_id: str) -> list[dict[str, Any]]:
    """Get all return requests for a specific order."""
    data = await _get(f"/api/orders/{order_id}/returns")
    return data if isinstance(data, list) else data.get("data", [])


# ---------------------------------------------------------------------------
# Satisfaction Survey functions
# ---------------------------------------------------------------------------


async def submit_satisfaction(
    rating: int,
    feedback: str = "",
    customer_id: int | None = None,
    order_id: str | None = None,
) -> dict[str, Any]:
    """Submit a customer satisfaction survey rating (1-5)."""
    params: dict[str, Any] = {
        "rating": rating,
        "feedback": feedback,
    }
    if customer_id:
        params["customer_id"] = customer_id
    if order_id:
        params["order_id"] = order_id
    return await _post("/api/surveys", params)

