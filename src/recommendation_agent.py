"""L1 MCP tool wrapper for the recommendation service (Task 4).

Exposes ``generate_recommendations`` / ``record_funnel_event`` /
``list_user_recommendations`` as MCP-callable tools guarded by
``require_permission`` with permission ``recommendation:write`` (L1 write).
The wrapper never replies to customers directly; it returns internal JSON
for the Orchestrator to consume (per ADR-0002 protocol — output uses
processing result + customer reply + internal notes, but this agent's
``customer_reply`` is intentionally empty because the Orchestrator is the
only customer-facing surface).

Mirrors the framework-neutral adapter style of
``orchestrator_mcp_tool.py``.
"""

from __future__ import annotations

import json
from typing import Any

from starlette.exceptions import HTTPException

import database
import recommendation_service as recs
from security import Actor, require_permission

# L1 write permission — see ROLE_PERMISSIONS["recommendation"] in security.py.
PERMISSION = "recommendation:write"

# Default actor identity for MCP tool calls. Callers may override
# ``actor_subject`` to attribute the call to a specific Orchestrator
# instance or correlation id.
DEFAULT_ACTOR_SUBJECT = "mcp-recommendation"
DEFAULT_ACTOR_ROLE = "recommendation"


def generate_recommendations_tool(
    user_id: str,
    conversation_id: str,
    opportunities: list[dict[str, Any]],
    actor_subject: str = DEFAULT_ACTOR_SUBJECT,
    actor_role: str = DEFAULT_ACTOR_ROLE,
) -> str:
    """Generate ≤ 3 recommendations from mined opportunities (L1 write).

    Returns a JSON string with shape:
    ``{"status": "success"|"denied"|"failed", "recommendations": [...], ...}``.
    """
    actor = Actor(actor_subject, actor_role, {})
    try:
        with database.session_scope() as session:
            require_permission(actor, PERMISSION)
            result = recs.generate_recommendations(
                session=session,
                user_id=user_id,
                conversation_id=conversation_id,
                opportunities=opportunities,
            )
            return json.dumps(
                {
                    "status": "success",
                    "recommendations": result,
                    "customer_reply": "",  # Orchestrator composes the customer-facing reply.
                    "internal_notes": (
                        f"Generated {len(result)} recommendations for "
                        f"user={user_id} conversation={conversation_id}."
                    ),
                },
                ensure_ascii=False,
                indent=2,
            )
    except HTTPException as exc:
        return _error_payload(exc)
    except Exception as exc:
        return _error_payload(exc)


def record_funnel_event_tool(
    recommendation_id: str,
    user_id: str,
    session_id: str,
    event_type: str,
    order_id: str | None = None,
    payload: dict[str, Any] | None = None,
    actor_subject: str = DEFAULT_ACTOR_SUBJECT,
    actor_role: str = DEFAULT_ACTOR_ROLE,
) -> str:
    """Record a funnel event with 24-hour dedup (L1 write).

    Returns a JSON string with shape:
    ``{"status": "success"|"denied"|"failed", "written": bool, ...}``.
    """
    actor = Actor(actor_subject, actor_role, {})
    try:
        with database.session_scope() as session:
            require_permission(actor, PERMISSION)
            written = recs.record_funnel_event(
                session=session,
                recommendation_id=recommendation_id,
                user_id=user_id,
                session_id=session_id,
                event_type=event_type,
                order_id=order_id,
                payload=payload,
            )
            return json.dumps(
                {
                    "status": "success",
                    "written": written,
                    "deduped": not written,
                    "customer_reply": "",
                    "internal_notes": (
                        f"Funnel event {event_type} for rec={recommendation_id} "
                        f"{'written' if written else 'deduped (24h)'}"
                    ),
                },
                ensure_ascii=False,
                indent=2,
            )
    except HTTPException as exc:
        return _error_payload(exc)
    except Exception as exc:
        return _error_payload(exc)


def list_user_recommendations_tool(
    user_id: str,
    limit: int = 20,
    actor_subject: str = DEFAULT_ACTOR_SUBJECT,
    actor_role: str = DEFAULT_ACTOR_ROLE,
) -> str:
    """List a user's recent recommendations (L1 write — internal use only).

    Returns a JSON string with shape:
    ``{"status": "success"|"denied"|"failed", "recommendations": [...], ...}``.
    """
    actor = Actor(actor_subject, actor_role, {})
    try:
        with database.session_scope() as session:
            require_permission(actor, PERMISSION)
            rows = recs.list_user_recommendations(
                session=session, user_id=user_id, limit=limit
            )
            return json.dumps(
                {
                    "status": "success",
                    "recommendations": rows,
                    "customer_reply": "",
                    "internal_notes": (
                        f"Returned {len(rows)} recommendations for user={user_id}."
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
            "customer_reply": "当前调用没有足够的后端权限，未执行任何推荐写入。",
        }
    else:
        payload = {
            "status": "failed",
            "error": str(exc),
            "customer_reply": "推荐服务处理时遇到内部异常，请稍后重试或人工跟进。",
        }
    return json.dumps(payload, ensure_ascii=False, indent=2)
