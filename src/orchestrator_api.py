"""Framework-neutral API adapter for the customer-service orchestrator.

Per Task 6 (cs-profit-engine Orchestrator integration), this module also
embeds the profit-engine hooks into the main response path:

- SubTask 6.1: async demand-mining hook (ThreadPoolExecutor, non-blocking).
- SubTask 6.2: synchronous recommendation generation when
  ``opportunity_score > 0.6`` (2s timeout protection).
- SubTask 6.3: async revenue-attribution touch-point recording.
- SubTask 6.5: mining result + recommendations appended to the latest
  ``customer_service_usage_events.intents`` JSON field (option B — no
  schema change).

All hook failures are caught and logged via structlog; the main customer
response is never blocked or altered by hook side effects.
"""

from __future__ import annotations

import concurrent.futures
from typing import Any

import structlog

import database
import human_handoff_upgrade as hhu
from models import CustomerServiceUsageEvent, OrderItem
from orchestrator_runtime import CustomerServiceOrchestrator
from profit_engine_hooks import (
    attach_agent_assist_to_result,
    run_attribution_async,
    run_demand_mining_async,
    submit_recommendation_generation,
)
from recommendation_service import OPPORTUNITY_THRESHOLD
from security import Actor, Verification, require_permission

logger = structlog.get_logger(__name__)

# Per spec: 2s timeout protection for the synchronous recommendation
# generation path (SubTask 6.2) and the demand-mining wait (SubTask 6.1
# "不阻塞主响应"). If either exceeds the budget, the hook is abandoned
# and the main response is returned without recommendations.
MINING_TIMEOUT_SECONDS = 2.0
RECOMMENDATION_TIMEOUT_SECONDS = 2.0


def respond_to_customer_message(
    payload: dict[str, Any],
    actor: Actor | None = None,
    verification: Verification | None = None,
    idempotency_key: str = "",
    request_id: str = "",
) -> dict[str, Any]:
    """Handle an API-style request payload with the orchestrator runtime.

    Runs the synchronous orchestrator runtime first (the customer-facing
    response), then fires the profit-engine hooks. Hook failures are
    caught and logged; the returned dict is always the runtime result,
    optionally enriched with a ``recommendations`` key when the sync
    recommendation path produced output.
    """
    if actor is not None:
        require_permission(actor, "orchestrator:invoke")
    runtime = CustomerServiceOrchestrator(
        actor=actor,
        verification=verification,
        idempotency_key=idempotency_key,
        request_id=request_id,
    )
    result = runtime.handle_message(
        message=str(payload.get("message", "")),
        customer_id=payload.get("customer_id"),
        order_id=payload.get("order_id") or None,
        conversation_id=payload.get("conversation_id") or None,
        actor=actor,
        verification=verification,
        idempotency_key=idempotency_key,
        request_id=request_id,
    )

    # Profit-engine hooks. The wrapper is a final safety net: the hooks
    # themselves catch all exceptions, but we guard again here so a bug
    # in the orchestration logic can never reach the customer response.
    try:
        _run_profit_engine_hooks(payload, result)
    except Exception:
        logger.exception("profit_engine_hooks_failed")

    return result


# ---------------------------------------------------------------------------
# Hook orchestration
# ---------------------------------------------------------------------------


def _run_profit_engine_hooks(payload: dict[str, Any], result: dict[str, Any]) -> None:
    """Fire demand-mining + recommendation + attribution hooks.

    Flow (per SubTask 6.1 / 6.2 / 6.3 / 6.5):
    1. Resolve user_id / order_id / conversation_id from the payload and
       result. Skip the whole pipeline when there is no user_id (mining
       and attribution both require one).
    2. Extract ``mentioned_skus`` from the order context (if any) so the
       mining service has source SKUs to cross-sell / up-sell against.
    3. Fire the async demand-mining hook and wait up to 2s for the
       result. On timeout / error, still record an attribution touch
       point for the conversation and return.
    4. When any opportunity has ``opportunity_score > 0.6``, submit
       recommendation generation to the executor and wait up to 2s
       (SubTask 6.2 sync path with timeout protection).
    5. Fire the async attribution hook (fire-and-forget). Link it to the
       first generated recommendation_id when present.
    6. Append the mining result + recommendations to the latest usage
       event's ``intents`` JSON field (SubTask 6.5 option B).
    7. Surface ``recommendations`` / ``mining_intent`` on the result so
       MCP / REST callers can expose them (non-breaking extra keys).
    """
    message = str(payload.get("message") or "")
    customer_id = payload.get("customer_id")
    order_id_raw = payload.get("order_id") or result.get("order_id")
    order_id: str | None = order_id_raw if isinstance(order_id_raw, str) and order_id_raw else None
    conversation_id = result.get("conversation_id") or payload.get("conversation_id")
    if not conversation_id:
        return

    user_id = str(customer_id) if customer_id is not None else ""
    if not user_id:
        # Mining and attribution both require a user_id; nothing to do.
        return

    mentioned_skus = _extract_mentioned_skus(order_id)

    mining_future = run_demand_mining_async(
        user_id=user_id,
        conversation_context={
            "message": message,
            "order_id": order_id,
            "conversation_id": conversation_id,
            "mentioned_skus": mentioned_skus,
        },
    )

    mining_result: dict[str, Any] = {}
    try:
        mining_result = mining_future.result(timeout=MINING_TIMEOUT_SECONDS)
    except concurrent.futures.TimeoutError:
        logger.warning(
            "demand_mining_hook_timeout",
            conversation_id=conversation_id,
            timeout_seconds=MINING_TIMEOUT_SECONDS,
        )
    except Exception:
        logger.exception("demand_mining_hook_error", conversation_id=conversation_id)

    # If mining did not return a usable result, still record the
    # attribution touch point for the conversation and bail out.
    if not mining_result:
        run_attribution_async(
            user_id=user_id,
            conversation_id=conversation_id,
            agent_id="customer-service-orchestrator",
            touch_type="conversation",
            order_id=order_id,
        )
        return

    opportunities = list(mining_result.get("opportunities") or [])
    recommendations: list[dict[str, Any]] = []
    if any(float(o.get("opportunity_score", 0.0)) > OPPORTUNITY_THRESHOLD for o in opportunities):
        recommendations = _generate_recommendations_with_timeout(
            user_id=user_id,
            conversation_id=conversation_id,
            opportunities=opportunities,
        )

    # SubTask 8.1 — proactive handoff evaluation. When a vip user's
    # mining result reports intent_confidence < 0.7, mark the result as
    # needing human handoff and inject the prepared payload (user
    # profile + recent recommendations + conversation summary) into the
    # handoff_package so the human agent has full context on pickup.
    # Failures are caught so a bug in the evaluator never breaks the
    # main response path.
    try:
        _evaluate_and_apply_proactive_handoff(
            result=result,
            user_id=user_id,
            conversation_id=conversation_id,
            mining_result=mining_result,
        )
    except Exception:
        logger.exception(
            "proactive_handoff_evaluation_failed",
            conversation_id=conversation_id,
            user_id=user_id,
        )

    # SubTask 6.5 (option B): append mining result + recommendations to
    # the latest usage event's intents JSON field.
    #
    # This synchronous UPDATE runs BEFORE the fire-and-forget attribution
    # hook is dispatched. SQLite shared-cache in-memory mode uses
    # table-level locking (SQLITE_LOCKED), which ``busy_timeout`` does
    # NOT mitigate — running the UPDATE before the worker thread starts
    # its own write avoids "database table is locked" collisions between
    # the main thread and the attribution worker.
    _append_mining_result_to_usage_event(
        conversation_id=conversation_id,
        mining_result=mining_result,
        recommendations=recommendations,
    )

    # Fire-and-forget attribution. Link the touch point to the first
    # recommendation when one was generated so the funnel can be traced.
    recommendation_id = recommendations[0]["recommendation_id"] if recommendations else None
    run_attribution_async(
        user_id=user_id,
        conversation_id=conversation_id,
        agent_id="customer-service-orchestrator",
        recommendation_id=recommendation_id,
        touch_type="conversation",
        order_id=order_id,
    )

    # SubTask 8.2 — agent-assist suggestions. When the conversation is
    # in handoff state (either the current turn set needs_human or a
    # prior turn did), surface script / knowledge / cross-sell
    # suggestions on the result so the human agent's UI can render
    # them. No-op when the AI is still handling the conversation.
    attach_agent_assist_to_result(
        result=result,
        user_id=user_id,
        conversation_id=conversation_id,
        mining_result=mining_result,
    )

    # Surface recommendations on the result so callers can expose them.
    # Non-breaking: existing callers ignore extra keys.
    if recommendations:
        result["recommendations"] = recommendations
        result["mining_intent"] = mining_result.get("intent")
        result["mining_intent_confidence"] = mining_result.get("intent_confidence")


def _extract_mentioned_skus(order_id: str | None) -> list[str]:
    """Return the SKUs of the order's items, used as mining source SKUs.

    The orchestrator does not extract SKUs from free text today; using
    the order context gives the mining service real source products to
    cross-sell / up-sell against when the customer is discussing a
    specific order. Returns an empty list on any error (mining handles
    empty mentioned_skus by returning empty opportunities).
    """
    if not order_id:
        return []
    try:
        with database.session_scope() as session:
            return [
                row.sku
                for row in session.query(OrderItem).filter_by(order_id=order_id).all()
            ]
    except Exception:
        logger.exception("extract_mentioned_skus_failed", order_id=order_id)
        return []


def _generate_recommendations_with_timeout(
    user_id: str,
    conversation_id: str,
    opportunities: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Generate recommendations synchronously with a 2s timeout guard.

    Per SubTask 6.2: recommendation generation is synchronous (in the
    main response path) but protected by a 2s timeout. Running the work
    in the shared ThreadPoolExecutor lets us enforce the timeout via
    ``Future.result(timeout=...)``; on timeout or error, returns an
    empty list so the main response is never blocked.
    """
    future = submit_recommendation_generation(
        user_id=user_id,
        conversation_id=conversation_id,
        opportunities=opportunities,
    )
    try:
        return future.result(timeout=RECOMMENDATION_TIMEOUT_SECONDS)
    except concurrent.futures.TimeoutError:
        logger.warning(
            "recommendation_generation_timeout",
            conversation_id=conversation_id,
            timeout_seconds=RECOMMENDATION_TIMEOUT_SECONDS,
        )
        future.cancel()
        return []
    except Exception:
        logger.exception("recommendation_generation_error", conversation_id=conversation_id)
        return []


def _append_mining_result_to_usage_event(
    conversation_id: str,
    mining_result: dict[str, Any],
    recommendations: list[dict[str, Any]],
) -> None:
    """Append the mining result + recommendations to the latest usage event.

    Per SubTask 6.5 option B: store mining output in the existing
    ``customer_service_usage_events.intents`` JSON column rather than
    adding a new column. The mining result is appended as a single dict
    entry with ``intent="profit_engine:mining_result"`` so existing
    intent entries are preserved and downstream analytics can identify
    the profit-engine payload by its intent key.

    The runtime writes the usage event at the end of ``handle_message``;
    this function queries the most recent event for the conversation
    (by id desc) and appends to its intents list. Errors are caught and
    logged — the main response is never affected.
    """
    if not mining_result and not recommendations:
        return
    try:
        with database.session_scope() as session:
            event = (
                session.query(CustomerServiceUsageEvent)
                .filter_by(conversation_id=conversation_id)
                .order_by(CustomerServiceUsageEvent.id.desc())
                .first()
            )
            if event is None:
                return
            intents = list(event.intents or [])
            intents.append(
                {
                    "intent": "profit_engine:mining_result",
                    "intent_confidence": mining_result.get("intent_confidence"),
                    "suggested_agent": "recommendation-agent",
                    "opportunities": list(mining_result.get("opportunities") or []),
                    "recommendations": recommendations,
                    "reason": (
                        f"mined {len(mining_result.get('opportunities') or [])} opportunities; "
                        f"generated {len(recommendations)} recommendations"
                    ),
                }
            )
            event.intents = intents
    except Exception:
        logger.exception(
            "append_mining_result_to_usage_event_failed",
            conversation_id=conversation_id,
        )


def _evaluate_and_apply_proactive_handoff(
    result: dict[str, Any],
    user_id: str,
    conversation_id: str,
    mining_result: dict[str, Any],
) -> None:
    """SubTask 8.1 — evaluate the proactive-handoff rule on the mining result.

    When ``human_handoff_upgrade.evaluate_proactive_handoff`` reports the
    rule fired (vip user + ``intent_confidence < 0.7``), set
    ``result["needs_human"] = True`` and merge the prepared payload into
    ``result["handoff_package"]`` under the ``proactive_handoff`` key so
    the human agent's UI can render the user profile, recent
    recommendations, and conversation summary on pickup.

    Existing ``handoff_package`` keys (set by the orchestrator runtime
    when the L3 / complaint path triggered earlier in the turn) are
    preserved — the proactive payload is additive, not replacing.
    """
    with database.session_scope() as session:
        evaluation = hhu.evaluate_proactive_handoff(
            session,
            user_id=user_id,
            mining_result=mining_result,
        )
    if not evaluation.get("should_handoff"):
        return

    result["needs_human"] = True
    existing_package = dict(result.get("handoff_package") or {})
    existing_package["proactive_handoff"] = {
        "reason": evaluation.get("reason"),
        "payload": evaluation.get("payload"),
    }
    result["handoff_package"] = existing_package
    result["proactive_handoff_reason"] = evaluation.get("reason")
