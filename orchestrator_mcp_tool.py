"""Framework-neutral MCP tool adapter for customer messages."""

from __future__ import annotations

import json

import database
from orchestrator_api import respond_to_customer_message
from security import Actor, load_verification


def handle_customer_message_tool(
    message: str,
    customer_id: int = 0,
    order_id: str = "",
    conversation_id: str = "",
    actor_subject: str = "mcp-orchestrator",
    actor_role: str = "orchestrator",
    verification_token: str = "",
    idempotency_key: str = "",
) -> str:
    """Return the MCP tool payload for a customer message."""
    actor = Actor(actor_subject, actor_role, {})
    verification = None
    if verification_token:
        with database.session_scope() as session:
            verification = load_verification(session, verification_token)
    try:
        result = respond_to_customer_message(
            {
                "message": message,
                "customer_id": customer_id or None,
                "order_id": order_id or None,
                "conversation_id": conversation_id or None,
            },
            actor=actor,
            verification=verification,
            idempotency_key=idempotency_key,
        )
    except Exception as exc:
        result = {
            "status": "denied",
            "customer_reply": "当前调用没有足够的后端权限或身份核验，未执行任何业务写入。",
            "error": str(exc),
        }
    return json.dumps(result, ensure_ascii=False, indent=2)
