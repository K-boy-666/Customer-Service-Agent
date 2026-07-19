"""L1 MCP tool wrapper for the attribution service (Task 5).

Exposes ``attribute_order`` / ``compute_roi`` / ``list_attributions`` /
``get_attribution_summary`` (plus the order-event subscription entry
point ``attribute_order_if_in_window``) as MCP-callable tools guarded
by ``require_permission`` with permission ``analytics:write`` (L1 write).

The wrapper never replies to customers directly; it returns internal
JSON for the Orchestrator to consume (per ADR-0002 protocol — output
uses processing result + customer reply + internal notes, but this
agent's ``customer_reply`` is intentionally empty because the
Orchestrator is the only customer-facing surface).

Mirrors the framework-neutral adapter style of ``recommendation_agent.py``.
"""

from __future__ import annotations

import json
from typing import Any

from starlette.exceptions import HTTPException

import attribution_service as attr
import database
from security import Actor, require_permission

# L1 write permission — see ROLE_PERMISSIONS["analytics"] in security.py.
PERMISSION = "analytics:write"

# Default actor identity for MCP tool calls. Callers may override
# ``actor_subject`` to attribute the call to a specific Orchestrator
# instance or correlation id.
DEFAULT_ACTOR_SUBJECT = "mcp-analytics"
DEFAULT_ACTOR_ROLE = "analytics"


def attribute_order_tool(
    order_id: str,
    model: str = attr.DEFAULT_MODEL,
    actor_subject: str = DEFAULT_ACTOR_SUBJECT,
    actor_role: str = DEFAULT_ACTOR_ROLE,
) -> str:
    """Attribute an order's revenue across preceding touch points (L1 write).

    Returns a JSON string with shape:
    ``{"status": "success"|"denied"|"failed", "attributions": [...], ...}``.
    """
    actor = Actor(actor_subject, actor_role, {})
    try:
        with database.session_scope() as session:
            require_permission(actor, PERMISSION)
            result = attr.attribute_order(
                session=session,
                order_id=order_id,
                model=model,
            )
            return json.dumps(
                {
                    "status": "success",
                    "attributions": result,
                    "customer_reply": "",  # Orchestrator composes the customer-facing reply.
                    "internal_notes": (
                        f"Attributed order={order_id} under model={model} "
                        f"across {len(result)} touch point(s)."
                    ),
                },
                ensure_ascii=False,
                indent=2,
            )
    except HTTPException as exc:
        return _error_payload(exc)
    except Exception as exc:
        return _error_payload(exc)


def attribute_order_if_in_window_tool(
    order_id: str,
    model: str = attr.DEFAULT_MODEL,
    actor_subject: str = DEFAULT_ACTOR_SUBJECT,
    actor_role: str = DEFAULT_ACTOR_ROLE,
) -> str:
    """Order-event subscription hook: attribute only within the 24h window.

    Returns a JSON string with shape:
    ``{"status": "success"|"denied"|"failed", "attributions": [...], "attributed": bool, ...}``.
    """
    actor = Actor(actor_subject, actor_role, {})
    try:
        with database.session_scope() as session:
            require_permission(actor, PERMISSION)
            result = attr.attribute_order_if_in_window(
                session=session,
                order_id=order_id,
                model=model,
            )
            return json.dumps(
                {
                    "status": "success",
                    "attributions": result,
                    "attributed": bool(result),
                    "customer_reply": "",
                    "internal_notes": (
                        f"Order {order_id} "
                        f"{'attributed' if result else 'skipped (outside 24h window or no touch points)'}"
                        f" under model={model}."
                    ),
                },
                ensure_ascii=False,
                indent=2,
            )
    except HTTPException as exc:
        return _error_payload(exc)
    except Exception as exc:
        return _error_payload(exc)


def compute_roi_tool(
    start: str,
    end: str,
    model: str = attr.DEFAULT_MODEL,
    actor_subject: str = DEFAULT_ACTOR_SUBJECT,
    actor_role: str = DEFAULT_ACTOR_ROLE,
) -> str:
    """Compute ROI (attributed revenue / service cost) for a date range (L1 write).

    Returns a JSON string with shape:
    ``{"status": "success"|"denied"|"failed", "roi": {...}, ...}``.
    """
    actor = Actor(actor_subject, actor_role, {})
    try:
        with database.session_scope() as session:
            require_permission(actor, PERMISSION)
            result = attr.compute_roi(
                session=session,
                start=start,
                end=end,
                model=model,
            )
            return json.dumps(
                {
                    "status": "success",
                    "roi": result,
                    "customer_reply": "",
                    "internal_notes": (
                        f"ROI computed for [{start}, {end}] under model={model}: "
                        f"revenue={result['attributed_revenue']:.2f} "
                        f"cost={result['service_cost']['total']:.2f} "
                        f"roi={result['roi']:.4f}."
                    ),
                },
                ensure_ascii=False,
                indent=2,
            )
    except HTTPException as exc:
        return _error_payload(exc)
    except Exception as exc:
        return _error_payload(exc)


def list_attributions_tool(
    start: str | None = None,
    end: str | None = None,
    model: str | None = None,
    user_id: str | None = None,
    order_id: str | None = None,
    limit: int = 100,
    actor_subject: str = DEFAULT_ACTOR_SUBJECT,
    actor_role: str = DEFAULT_ACTOR_ROLE,
) -> str:
    """List attribution records filtered by multiple dimensions (L1 write — internal use only).

    Returns a JSON string with shape:
    ``{"status": "success"|"denied"|"failed", "attributions": [...], ...}``.
    """
    actor = Actor(actor_subject, actor_role, {})
    try:
        with database.session_scope() as session:
            require_permission(actor, PERMISSION)
            rows = attr.list_attributions(
                session=session,
                start=start,
                end=end,
                model=model,
                user_id=user_id,
                order_id=order_id,
                limit=limit,
            )
            return json.dumps(
                {
                    "status": "success",
                    "attributions": rows,
                    "customer_reply": "",
                    "internal_notes": (
                        f"Returned {len(rows)} attribution record(s) "
                        f"matching filters (model={model}, user_id={user_id}, order_id={order_id})."
                    ),
                },
                ensure_ascii=False,
                indent=2,
            )
    except HTTPException as exc:
        return _error_payload(exc)
    except Exception as exc:
        return _error_payload(exc)


def get_attribution_summary_tool(
    start: str,
    end: str,
    actor_subject: str = DEFAULT_ACTOR_SUBJECT,
    actor_role: str = DEFAULT_ACTOR_ROLE,
) -> str:
    """Multi-model attribution summary across a date range (L1 write — internal use only).

    Returns a JSON string with shape:
    ``{"status": "success"|"denied"|"failed", "summary": {...}, ...}``.
    """
    actor = Actor(actor_subject, actor_role, {})
    try:
        with database.session_scope() as session:
            require_permission(actor, PERMISSION)
            result = attr.get_attribution_summary(
                session=session,
                start=start,
                end=end,
            )
            return json.dumps(
                {
                    "status": "success",
                    "summary": result,
                    "customer_reply": "",
                    "internal_notes": (
                        f"Attribution summary for [{start}, {end}]: "
                        f"{result['total_orders']} order(s), "
                        f"{result['total_revenue']:.2f} total revenue across "
                        f"{len(result['models'])} models."
                    ),
                },
                ensure_ascii=False,
                indent=2,
            )
    except HTTPException as exc:
        return _error_payload(exc)
    except Exception as exc:
        return _error_payload(exc)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _error_payload(exc: BaseException) -> str:
    """Render an exception as a JSON tool payload.

    ``HTTPException`` with 401/403 is treated as a permission denial so
    the Orchestrator can branch on ``status="denied"``. Any other
    exception is reported as ``status="failed"`` with the error string.
    """
    if isinstance(exc, HTTPException) and exc.status_code in {401, 403}:
        payload: dict[str, Any] = {
            "status": "denied",
            "error": str(exc.detail),
            "customer_reply": "当前调用没有足够的后端权限，未执行任何归因写入。",
        }
    else:
        payload = {
            "status": "failed",
            "error": str(exc),
            "customer_reply": "归因服务处理时遇到内部异常，请稍后重试或人工跟进。",
        }
    return json.dumps(payload, ensure_ascii=False, indent=2)
