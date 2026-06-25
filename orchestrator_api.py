"""Framework-neutral API adapter for the customer-service orchestrator."""

from __future__ import annotations

from typing import Any

from orchestrator_runtime import CustomerServiceOrchestrator
from security import Actor, Verification, require_permission


def respond_to_customer_message(
    payload: dict[str, Any],
    actor: Actor | None = None,
    verification: Verification | None = None,
    idempotency_key: str = "",
    request_id: str = "",
) -> dict[str, Any]:
    """Handle an API-style request payload with the orchestrator runtime."""
    if actor is not None:
        require_permission(actor, "orchestrator:invoke")
    runtime = CustomerServiceOrchestrator(
        actor=actor,
        verification=verification,
        idempotency_key=idempotency_key,
        request_id=request_id,
    )
    return runtime.handle_message(
        message=str(payload.get("message", "")),
        customer_id=payload.get("customer_id"),
        order_id=payload.get("order_id") or None,
        conversation_id=payload.get("conversation_id") or None,
        actor=actor,
        verification=verification,
        idempotency_key=idempotency_key,
        request_id=request_id,
    )
