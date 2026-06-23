"""
MCP server for customer-service operations.

Exposes 12 tools across three domains:
  - Knowledge Base (FAQ search)
  - Ticket Management (CRUD)
  - After-Sales / Returns (create, query, update)

Run with: python server_customer.py   (stdio transport, for Claude Code)
Requires: the REST API at localhost:8000 and faq.json in the same directory.
"""

import json
import os
from pathlib import Path

from fastmcp import FastMCP

import api_client

# ---------------------------------------------------------------------------
# FAQ — loaded once at startup
# ---------------------------------------------------------------------------

FAQ_PATH = Path(__file__).parent / "faq.json"


def _load_faq() -> list[dict]:
    """Load the FAQ database from the JSON file."""
    with open(FAQ_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


FAQ = _load_faq()

# Pre-compute category list for fast lookup
FAQ_CATEGORIES = sorted({entry["category"] for entry in FAQ})

# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------

mcp = FastMCP(
    name="customer-service",
    version="0.2.0",
    instructions=(
        "This server provides customer-service operations for the 客服智能体2.0 platform. "
        "It has three tool groups:\n"
        "1. Knowledge Base — search_faq, get_faq_categories, get_faq_by_id. "
        "Use these to answer customer questions about policies, products, and procedures.\n"
        "2. Ticket Management — create_ticket, get_ticket, list_tickets, update_ticket, "
        "search_tickets. Follow the ITIL ticket lifecycle: new → assigned → in_progress → "
        "pending → resolved → closed. Priority levels: P1 (critical) through P4 (low).\n"
        "3. After-Sales / Returns — create_return, get_return, list_returns, "
        "update_return_status. Return types: return (退货), exchange (换货), refund (退款). "
        "Return statuses: pending → approved → in_transit → received → refunded → completed."
    ),
)


# ---------------------------------------------------------------------------
# Knowledge Base tools
# ---------------------------------------------------------------------------


@mcp.tool(
    annotations={
        "readOnlyHint": True,
        "openWorldHint": True,
        "title": "Search FAQ knowledge base",
    },
)
async def search_faq(query: str, category: str = "", limit: int = 10) -> str:
    """Search the FAQ knowledge base by keyword.

    Matches against question text, answer text, and keyword tags.
    Use this to answer customer questions about return policy, warranty,
    shipping, membership, payment, and product specifications.

    Args:
        query: Search keyword(s) in Chinese or English.
        category: Optional category filter (e.g., "退货政策", "产品咨询").
                  Call get_faq_categories first to see available categories.
        limit: Max results to return (default 10).
    """
    q = query.lower()
    results = []

    for entry in FAQ:
        # Category filter
        if category and entry["category"] != category:
            continue

        # Score: match in question > keywords > answer
        score = 0
        if q in entry["question"].lower():
            score += 10
        for kw in entry.get("keywords", []):
            if q in kw.lower():
                score += 5
        if q in entry["answer"].lower():
            score += 2

        if score > 0:
            results.append((score, entry))

    # Sort by score descending, take top N
    results.sort(key=lambda x: x[0], reverse=True)
    top = results[:limit]

    if not top:
        all_cats = ", ".join(FAQ_CATEGORIES)
        return (
            f"No FAQ entries found for '{query}'. "
            f"Try different keywords or a broader search. "
            f"Available categories: {all_cats}"
        )

    out = []
    for score, entry in top:
        out.append({
            "id": entry["id"],
            "category": entry["category"],
            "question": entry["question"],
            "answer": entry["answer"],
            "relevance": score,
        })

    return json.dumps(out, ensure_ascii=False, indent=2)


@mcp.tool(
    annotations={
        "readOnlyHint": True,
        "openWorldHint": True,
        "title": "List all FAQ categories",
    },
)
async def get_faq_categories() -> str:
    """Return all available FAQ categories with entry counts.

    Use this first to understand what topics the knowledge base covers,
    then use search_faq with a category filter for targeted results.
    """
    counts = {}
    for entry in FAQ:
        cat = entry["category"]
        counts[cat] = counts.get(cat, 0) + 1

    out = [
        {"category": cat, "entry_count": counts[cat]}
        for cat in FAQ_CATEGORIES
    ]
    return json.dumps(out, ensure_ascii=False, indent=2)


@mcp.tool(
    annotations={
        "readOnlyHint": True,
        "openWorldHint": True,
        "title": "Get a specific FAQ entry by ID",
    },
)
async def get_faq_by_id(faq_id: str) -> str:
    """Retrieve a single FAQ entry by its ID (e.g., "faq-001").

    Use this when you already know the FAQ ID from a previous search_faq result,
    or when you need the full details of a specific FAQ entry.
    """
    for entry in FAQ:
        if entry["id"] == faq_id:
            return json.dumps(entry, ensure_ascii=False, indent=2)
    return f"FAQ entry '{faq_id}' not found. Use search_faq to find relevant entries."


# ---------------------------------------------------------------------------
# Ticket Management tools
# ---------------------------------------------------------------------------


@mcp.tool(
    annotations={
        "openWorldHint": True,
        "title": "Create a new work ticket",
    },
)
async def create_ticket(
    title: str,
    description: str = "",
    type: str = "incident",
    priority: str = "P3",
    customer_id: int = 0,
    order_id: str = "",
) -> str:
    """Create a new work ticket in the ticketing system.

    Args:
        title: Short title for the ticket (required).
        description: Detailed description of the issue or request.
        type: Ticket type — "incident" (故障/问题), "service_request" (服务请求),
              "change_request" (变更请求), or "problem" (问题管理).
        priority: "P1" (紧急), "P2" (高), "P3" (普通), or "P4" (低).
        customer_id: Optional customer ID to associate with this ticket.
        order_id: Optional order ID to associate with this ticket.

    Returns the created ticket with its ticket_number.
    """
    params = {
        "title": title,
        "type": type,
        "priority": priority,
        "description": description,
    }
    if customer_id:
        params["customer_id"] = customer_id
    if order_id:
        params["order_id"] = order_id

    result = await api_client.create_ticket(**params)
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool(
    annotations={
        "readOnlyHint": True,
        "openWorldHint": True,
        "title": "Get a ticket by ID",
    },
)
async def get_ticket(ticket_id: int) -> str:
    """Fetch a single ticket by its numeric ID, including all notes.

    Use this to see the full history and status of a ticket.
    """
    ticket = await api_client.get_ticket(ticket_id)
    if ticket is None:
        return f"Ticket {ticket_id} not found. Use list_tickets or search_tickets to find valid IDs."
    return json.dumps(ticket, ensure_ascii=False, indent=2)


@mcp.tool(
    annotations={
        "readOnlyHint": True,
        "openWorldHint": True,
        "title": "List tickets with optional filters",
    },
)
async def list_tickets(
    status: str = "",
    assignee: str = "",
    limit: int = 20,
    offset: int = 0,
) -> str:
    """List tickets with optional status and assignee filtering.

    Args:
        status: Filter by status — "new", "assigned", "in_progress",
                "pending", "resolved", or "closed". Empty means all.
        assignee: Filter by assignee name (partial match not supported).
        limit: Max results per page (default 20).
        offset: Pagination offset (0-based).
    """
    params = {"limit": limit, "offset": offset}
    if status:
        params["status"] = status
    if assignee:
        params["assignee"] = assignee

    data = await api_client.list_tickets(**params)
    if not data.get("data"):
        return f"No tickets found (status={status or 'all'}, offset={offset})."
    return json.dumps(data, ensure_ascii=False, indent=2)


@mcp.tool(
    annotations={
        "openWorldHint": True,
        "title": "Update a ticket",
    },
)
async def update_ticket(
    ticket_id: int,
    status: str = "",
    assignee: str = "",
    priority: str = "",
    note: str = "",
) -> str:
    """Update a ticket's status, assignee, priority, and/or add a note.

    At least one of status/assignee/priority/note must be provided.

    Args:
        ticket_id: The numeric ticket ID.
        status: New status — "new", "assigned", "in_progress", "pending",
                "resolved", or "closed".
        assignee: Reassign to a specific person.
        priority: New priority — "P1", "P2", "P3", or "P4".
        note: Optional note to add to the ticket (e.g., status change reason).

    Returns the update confirmation.
    """
    params = {}
    if status:
        params["status"] = status
    if assignee:
        params["assignee"] = assignee
    if priority:
        params["priority"] = priority
    if note:
        params["note"] = note

    if not params:
        return "No changes specified. Provide at least one of: status, assignee, priority, note."

    result = await api_client.update_ticket(ticket_id, **params)
    if result is None:
        return f"Ticket {ticket_id} not found."
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool(
    annotations={
        "readOnlyHint": True,
        "openWorldHint": True,
        "title": "Search tickets by keyword",
    },
)
async def search_tickets(query: str, limit: int = 20) -> str:
    """Search tickets by keyword across title, description, and ticket number.

    Use this when you don't have a specific ticket ID but need to find
    tickets related to a customer, order, or topic.
    """
    results = await api_client.search_tickets(query, limit)
    if not results:
        return f"No tickets matching '{query}'."
    return json.dumps(results, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# After-Sales / Returns tools
# ---------------------------------------------------------------------------


@mcp.tool(
    annotations={
        "openWorldHint": True,
        "title": "Create a return/exchange/refund request",
    },
)
async def create_return(
    order_id: str,
    reason: str,
    type: str = "return",
    description: str = "",
    customer_id: int = 0,
) -> str:
    """Create a return, exchange, or refund request for an order.

    Args:
        order_id: The order ID (e.g., "ORD-20260601-001"). Required.
        reason: The reason for the return (e.g., "商品质量问题", "不想要了").
        type: "return" (退货), "exchange" (换货), or "refund" (仅退款).
              Default is "return".
        description: Detailed description of the issue.
        customer_id: Optional customer ID.

    Returns the created return request with its return_number (RMA format).
    """
    params = {
        "order_id": order_id,
        "reason": reason,
        "type": type,
        "description": description,
    }
    if customer_id:
        params["customer_id"] = customer_id

    result = await api_client.create_return(**params)
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool(
    annotations={
        "readOnlyHint": True,
        "openWorldHint": True,
        "title": "Get a return request by ID",
    },
)
async def get_return(return_id: int) -> str:
    """Fetch a single return request by its numeric ID.

    Returns full details including type, reason, status, and refund amount.
    """
    ret = await api_client.get_return(return_id)
    if ret is None:
        return f"Return {return_id} not found. Use list_returns to find valid IDs."
    return json.dumps(ret, ensure_ascii=False, indent=2)


@mcp.tool(
    annotations={
        "readOnlyHint": True,
        "openWorldHint": True,
        "title": "List return requests",
    },
)
async def list_returns(
    status: str = "",
    customer_id: int = 0,
    limit: int = 20,
    offset: int = 0,
) -> str:
    """List return requests with optional filtering.

    Args:
        status: Filter by status — "pending", "approved", "rejected",
                "in_transit", "received", "refunded", or "completed".
                Empty means all.
        customer_id: Filter by customer ID.
        limit: Max results per page (default 20).
        offset: Pagination offset (0-based).
    """
    params = {"limit": limit, "offset": offset}
    if status:
        params["status"] = status
    if customer_id:
        params["customer_id"] = customer_id

    data = await api_client.list_returns(**params)
    if not data.get("data"):
        return f"No returns found (status={status or 'all'}, offset={offset})."
    return json.dumps(data, ensure_ascii=False, indent=2)


@mcp.tool(
    annotations={
        "openWorldHint": True,
        "title": "Update return request status",
    },
)
async def update_return_status(
    return_id: int,
    status: str,
    note: str = "",
) -> str:
    """Update the status of a return request.

    Use this to advance a return through its lifecycle.

    Args:
        return_id: The numeric return ID.
        status: New status — "pending", "approved", "rejected", "in_transit",
                "received", "refunded", or "completed".
        note: Optional note explaining the status change.

    Returns the update confirmation.
    """
    result = await api_client.update_return_status(return_id, status, note or None)
    if result is None:
        return f"Return {return_id} not found."
    return json.dumps(result, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Satisfaction Survey tools
# ---------------------------------------------------------------------------


@mcp.tool(
    annotations={
        "openWorldHint": True,
        "title": "Submit a customer satisfaction survey",
    },
)
async def submit_satisfaction(
    rating: int,
    feedback: str = "",
    customer_id: int = 0,
    order_id: str = "",
) -> str:
    """Submit a customer satisfaction survey with a 1-5 star rating.

    Use this at the end of a customer conversation when the customer
    provides a satisfaction rating.

    Args:
        rating: Rating from 1 (worst) to 5 (best). Required.
        feedback: Optional customer feedback or improvement suggestions.
        customer_id: Optional customer ID.
        order_id: Optional related order ID.

    Returns the created survey record with survey_number.
    """
    params = {
        "rating": rating,
        "feedback": feedback,
    }
    if customer_id:
        params["customer_id"] = customer_id
    if order_id:
        params["order_id"] = order_id

    result = await api_client.submit_satisfaction(**params)
    return json.dumps(result, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main():
    """Run the MCP server over stdio (default for Claude Code)."""
    mcp.run()


if __name__ == "__main__":
    main()
