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

import analytics_service
import api_client
from kb_service import get_faq_retriever
from orchestrator_mcp_tool import handle_customer_message_tool

# ---------------------------------------------------------------------------
# FAQ 鈥?loaded once at startup
# ---------------------------------------------------------------------------

FAQ_PATH = Path(__file__).parent.parent / "data" / "faq.json"


def _load_faq() -> list[dict]:
    """Load the FAQ database from the JSON file."""
    with open(FAQ_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


FAQ = _load_faq()
FAQ_RETRIEVER = get_faq_retriever(str(FAQ_PATH))

# Pre-compute category list for fast lookup
FAQ_CATEGORIES = [row["category"] for row in FAQ_RETRIEVER.categories()]

# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------

mcp = FastMCP(
    name="customer-service",
    version="0.2.0",
    instructions=(
        "This server provides customer-service operations for the 瀹㈡湇鏅鸿兘浣?.0 platform. "
        "It has four tool groups:\n"
        "1. Orchestrator runtime 鈥?handle_customer_message. Use this as the only "
        "customer-facing entry point for complete routing and response composition.\n"
        "2. Knowledge Base 鈥?search_faq, get_faq_categories, get_faq_by_id. "
        "Use these to answer customer questions about policies, products, and procedures.\n"
        "3. Ticket Management 鈥?create_ticket, get_ticket, list_tickets, update_ticket, "
        "search_tickets. Follow the ITIL ticket lifecycle: new 鈫?assigned 鈫?in_progress 鈫?"
        "pending 鈫?resolved 鈫?closed. Priority levels: P1 (critical) through P4 (low).\n"
        "4. After-Sales / Returns 鈥?create_return, get_return, list_returns, "
        "update_return_status. Return types: return (閫€璐?, exchange (鎹㈣揣), refund (閫€娆?. "
        "Return statuses: pending 鈫?approved 鈫?in_transit 鈫?received 鈫?refunded 鈫?completed."
    ),
)


# ---------------------------------------------------------------------------
# Orchestrator runtime tool
# ---------------------------------------------------------------------------


@mcp.tool(
    annotations={
        "openWorldHint": True,
        "title": "Handle a customer message through the orchestrator",
    },
)
async def handle_customer_message(
    message: str,
    customer_id: int = 0,
    order_id: str = "",
    conversation_id: str = "",
    actor_subject: str = "mcp-orchestrator",
    actor_role: str = "orchestrator",
    verification_token: str = "",
    idempotency_key: str = "",
) -> str:
    """Run the sole customer-facing orchestrator for one customer message.

    This is the MCP entry point that enforces ADR-0001 in executable form:
    callers send raw customer text here, and the runtime performs intent
    analysis, sub-agent dispatch, tool calls, and response composition.
    """
    return handle_customer_message_tool(
        message=message,
        customer_id=customer_id,
        order_id=order_id,
        conversation_id=conversation_id,
        actor_subject=actor_subject,
        actor_role=actor_role,
        verification_token=verification_token,
        idempotency_key=idempotency_key,
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
    """Search the FAQ knowledge base with semantic RAG retrieval.

    The retriever uses a local embedding model when available and falls back
    to an in-process lexical index when optional model dependencies are absent.
    """
    results = FAQ_RETRIEVER.search(query, limit=limit, category=category)
    if not results:
        all_cats = ", ".join(FAQ_CATEGORIES)
        return (
            f"No FAQ entries found for '{query}'. "
            f"Try different keywords or a broader search. "
            f"Available categories: {all_cats}"
        )
    return json.dumps(results, ensure_ascii=False, indent=2)


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
    return json.dumps(FAQ_RETRIEVER.categories(), ensure_ascii=False, indent=2)


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
    entry = FAQ_RETRIEVER.get_by_id(faq_id)
    if entry is not None:
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
        type: Ticket type 鈥?"incident" (鏁呴殰/闂), "service_request" (鏈嶅姟璇锋眰),
              "change_request" (鍙樻洿璇锋眰), or "problem" (闂绠＄悊).
        priority: "P1" (绱ф€?, "P2" (楂?, "P3" (鏅€?, or "P4" (浣?.
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
        status: Filter by status 鈥?"new", "assigned", "in_progress",
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
        status: New status 鈥?"new", "assigned", "in_progress", "pending",
                "resolved", or "closed".
        assignee: Reassign to a specific person.
        priority: New priority 鈥?"P1", "P2", "P3", or "P4".
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
        reason: The reason for the return (e.g., "鍟嗗搧璐ㄩ噺闂", "涓嶆兂瑕佷簡").
        type: "return" (閫€璐?, "exchange" (鎹㈣揣), or "refund" (浠呴€€娆?.
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
        status: Filter by status 鈥?"pending", "approved", "rejected",
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
        status: New status 鈥?"pending", "approved", "rejected", "in_transit",
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
# Analytics tools
# ---------------------------------------------------------------------------


@mcp.tool(
    annotations={
        "readOnlyHint": True,
        "openWorldHint": True,
        "title": "Get daily customer-service usage analytics",
    },
)
async def get_usage_analytics(date: str = "yesterday") -> str:
    """Return aggregate usage analytics for a report date.

    This internal-only tool exposes metadata counts and quality signals. It does
    not include raw customer messages, full customer replies, or PII.
    """
    data = await api_client.get_usage_analytics(date)
    return json.dumps(data, ensure_ascii=False, indent=2)


@mcp.tool(
    annotations={
        "openWorldHint": True,
        "title": "Generate a local Markdown daily usage report",
    },
)
async def generate_daily_usage_report(date: str = "yesterday", output_dir: str = "reports/daily") -> str:
    """Generate a local Markdown analytics report for a report date."""
    data = await api_client.get_usage_analytics(date)
    path = analytics_service.write_markdown_report(data, output_dir)
    return json.dumps({"date": data["date"], "report_path": str(path)}, ensure_ascii=False, indent=2)
# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main():
    """Run the MCP server over stdio (default for Claude Code)."""
    mcp.run()


if __name__ == "__main__":
    main()

