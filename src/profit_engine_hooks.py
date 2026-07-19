"""Async hooks for the cs-profit-engine Orchestrator integration (Task 6).

Provides ``ThreadPoolExecutor``-backed async hooks for demand mining and
revenue attribution. Each hook opens its own SQLAlchemy session via
``database.session_scope()`` so it is independent of the caller's session
and never blocks the customer-facing response path.

Per spec (SubTask 6.1 / 6.3):
- Hooks MUST NOT raise into the caller. All exceptions are caught and
  logged via structlog.
- Hooks return a ``concurrent.futures.Future`` so the caller can
  optionally ``.result(timeout=...)`` (the recommendation path uses a
  2s timeout to stay synchronous with the main response per SubTask 6.2).
- The shared executor is lazy and reusable; tests call
  ``shutdown_executor_for_tests`` in tearDown to avoid lingering threads
  across engine resets.

Why ThreadPoolExecutor (not asyncio):
- The orchestrator runtime is fully synchronous; introducing asyncio
  would require an event loop in every caller (MCP / REST / direct).
- ``ThreadPoolExecutor`` composes with sync code and lets the caller
  ``.result(timeout=...)`` to enforce the 2s SLA on recommendation
  generation without nested event loops.
"""

from __future__ import annotations

import concurrent.futures
import threading
from typing import Any

import structlog
from sqlalchemy import desc

import attribution_service as attrs
import database
import demand_mining_service as dms
from degradation import degradation_policy
from models import CustomerServiceUsageEvent

logger = structlog.get_logger(__name__)

# A single shared thread pool is sufficient for the orchestrator's
# fire-and-forget hooks. Threads are reused; the small pool size bounds
# concurrent DB sessions opened by the hooks (each hook opens one session).
_MAX_WORKERS = 4
_executor_lock = threading.Lock()
_executor: concurrent.futures.ThreadPoolExecutor | None = None


def _get_executor() -> concurrent.futures.ThreadPoolExecutor:
    """Return the lazily-initialised shared executor.

    Initialisation is lock-protected so concurrent callers on the first
    request do not create multiple pools.
    """
    global _executor
    with _executor_lock:
        if _executor is None:
            _executor = concurrent.futures.ThreadPoolExecutor(
                max_workers=_MAX_WORKERS,
                thread_name_prefix="profit-engine-hook",
            )
        return _executor


def run_demand_mining_async(
    user_id: str,
    conversation_context: dict[str, Any],
) -> concurrent.futures.Future[dict[str, Any]]:
    """Run demand mining asynchronously; return a ``Future``.

    The Future's result is the dict returned by
    ``demand_mining_service.mine_demand``. On any exception the Future
    resolves to an empty dict ``{}`` (never raises) so callers can
    ``.result(timeout=...)`` without try/except for the success path.

    ``conversation_context`` is forwarded verbatim to ``mine_demand``;
    expected keys: ``message``, ``order_id``, ``mentioned_skus``,
    ``conversation_id`` (the last is for logging only).
    """
    conversation_id = conversation_context.get("conversation_id") or ""

    def _work() -> dict[str, Any]:
        try:
            with database.session_scope() as session:
                return dms.mine_demand(session, user_id, conversation_context)
        except Exception:
            logger.exception(
                "demand_mining_hook_failed",
                user_id=user_id,
                conversation_id=conversation_id,
            )
            return {}

    return _get_executor().submit(_work)


def run_attribution_async(
    user_id: str,
    conversation_id: str,
    agent_id: str,
    recommendation_id: str | None = None,
    touch_type: str = "conversation",
    order_id: str | None = None,
) -> concurrent.futures.Future[dict[str, Any]]:
    """Record a revenue touch point asynchronously; return a ``Future``.

    The hook always records a ``TouchPoint`` for the conversation turn.
    When ``order_id`` is supplied, it also attempts to attribute the
    order via ``attribution_service.attribute_order_if_in_window``
    (24-hour window, last_touch model by default).

    Returns a Future resolving to a dict
    ``{touch_point_id, attribution_records}``. On any exception the
    Future resolves to ``{}`` (never raises) so the orchestrator's main
    response path is never blocked.
    """
    def _work() -> dict[str, Any]:
        try:
            with database.session_scope() as session:
                touch_id = attrs.record_touch_point(
                    session,
                    user_id=user_id,
                    conversation_id=conversation_id,
                    agent_id=agent_id,
                    recommendation_id=recommendation_id,
                    touch_type=touch_type,
                )
                attribution_records: list[dict[str, Any]] = []
                if order_id:
                    attribution_records = attrs.attribute_order_if_in_window(
                        session, order_id=order_id
                    )
                return {
                    "touch_point_id": touch_id,
                    "attribution_records": attribution_records,
                }
        except Exception:
            logger.exception(
                "attribution_hook_failed",
                user_id=user_id,
                conversation_id=conversation_id,
                order_id=order_id,
            )
            return {}

    return _get_executor().submit(_work)


def submit_recommendation_generation(
    user_id: str,
    conversation_id: str,
    opportunities: list[dict[str, Any]],
) -> concurrent.futures.Future[list[dict[str, Any]]]:
    """Submit recommendation generation to the executor; return a Future.

    Per SubTask 6.2, recommendation generation is synchronous from the
    caller's perspective — the orchestrator waits for the result in the
    main response path so recommendations can be surfaced to the
    customer / agent. Running the work in the shared ThreadPoolExecutor
    lets the caller enforce the 2s SLA via ``.result(timeout=2.0)``;
    on timeout the caller treats the result as an empty list.

    The work function catches all exceptions and returns an empty list
    so the Future never raises into the caller.

    Task 7.2 integration: when ``degradation_policy`` reports the system
    is shedding load, recommendation generation is skipped entirely and
    a pre-resolved empty Future is returned. This keeps the caller's
    ``.result(timeout=...)`` contract intact (it still gets ``[]``) but
    spends zero thread-pool budget on the lowest-priority work.
    """
    if degradation_policy.should_skip_recommendation():
        logger.info(
            "recommendation_skipped_due_to_degradation",
            user_id=user_id,
            conversation_id=conversation_id,
        )
        empty: concurrent.futures.Future[list[dict[str, Any]]] = concurrent.futures.Future()
        empty.set_result([])
        return empty

    def _work() -> list[dict[str, Any]]:
        try:
            # Import here to avoid a circular import at module load time.
            from recommendation_service import generate_recommendations

            with database.session_scope() as session:
                return generate_recommendations(
                    session,
                    user_id=user_id,
                    conversation_id=conversation_id,
                    opportunities=opportunities,
                )
        except Exception:
            logger.exception(
                "recommendation_generation_failed",
                user_id=user_id,
                conversation_id=conversation_id,
            )
            return []

    return _get_executor().submit(_work)


def shutdown_executor_for_tests() -> None:
    """Shutdown the shared executor. Tests call this in tearDown.

    Prevents lingering worker threads from outliving a test's DB engine
    reset, which would otherwise produce noisy tracebacks when a hook
    tries to use a disposed engine.

    ``wait=True`` is required: a worker that is still mid-DB-write when
    the test disposes the engine leaves SQLAlchemy's
    ``SingletonThreadPool`` in an inconsistent state (observed as
    ``RuntimeError: Set changed size during iteration`` from
    ``pool.dispose()``). Waiting for in-flight hooks to finish keeps
    teardown deterministic.
    """
    global _executor
    with _executor_lock:
        if _executor is not None:
            _executor.shutdown(wait=True)
            _executor = None


# ---------------------------------------------------------------------------
# SubTask 8.2 — agent-assist suggestion integration
# ---------------------------------------------------------------------------


def attach_agent_assist_to_result(
    result: dict[str, Any],
    user_id: str,
    conversation_id: str,
    mining_result: dict[str, Any] | None,
) -> None:
    """Attach agent-assist suggestions to ``result`` when in handoff state.

    Per SubTask 8.2 spec: after the mining result is available, *if* the
    conversation has been transferred to a human agent (current turn or
    a prior turn flagged ``needs_human``), call
    ``agent_assist_service.generate_assist_suggestions`` and attach the
    suggestions to ``result["agent_assist"]``. When the conversation is
    not in handoff state, this is a no-op — the customer is still being
    served by the AI and agent-assist suggestions would be noise.

    Handoff state detection (per spec: "通过 conversation_state 或会话
    上下文判断"):
    1. ``result["needs_human"] == True`` — the current orchestrator turn
       decided handoff is required (e.g. L3 trigger, complaint, or the
       proactive vip-low-confidence rule from Task 8.1).
    2. The most recent ``CustomerServiceUsageEvent`` for the conversation
       has ``needs_human == 1`` — a prior turn already transferred the
       conversation and the human agent is now responding to a follow-up
       message.

    Either signal triggers suggestion generation. All exceptions are
    caught and logged so the main response is never affected by an
    agent-assist failure (mirroring the rest of the hook contract).

    Synchronous by design: the suggestions must be available on the
    response the human agent's UI receives, so the function blocks on
    the (fast, in-memory) suggestion builders. The FAQ retriever is
    process-cached so there is no per-call I/O for the corpus load.
    """
    if not conversation_id or not user_id:
        return

    if not _is_conversation_in_handoff(result, conversation_id):
        return

    if mining_result is None:
        # No mining result → no actionable suggestions to surface.
        # The orchestrator may still surface an empty list, but per the
        # spec we only attach when there's something to show.
        return

    try:
        # Lazy import avoids a top-level cycle: agent_assist_service
        # imports recommendation_service which is imported lazily inside
        # submit_recommendation_generation for the same reason.
        import agent_assist_service as assist

        with database.session_scope() as session:
            suggestions = assist.generate_assist_suggestions(
                session,
                conversation_id=conversation_id,
                user_id=user_id,
                mining_result=mining_result,
            )
        if suggestions:
            result["agent_assist"] = suggestions
    except Exception:
        logger.exception(
            "agent_assist_attach_failed",
            conversation_id=conversation_id,
            user_id=user_id,
        )


def _is_conversation_in_handoff(
    result: dict[str, Any],
    conversation_id: str,
) -> bool:
    """Return ``True`` when the conversation is in human-handoff state.

    Two signals, either of which suffices:
    1. The orchestrator's current-turn ``needs_human`` flag is set.
    2. The most recent ``CustomerServiceUsageEvent`` for the conversation
       has ``needs_human == 1`` (a prior turn already transferred).

    DB lookup errors degrade to ``False`` so a transient DB hiccup does
    not falsely trigger suggestion generation on a conversation the AI
    is still handling.
    """
    if bool(result.get("needs_human")):
        return True

    try:
        with database.session_scope() as session:
            row = (
                session.query(CustomerServiceUsageEvent)
                .filter_by(conversation_id=conversation_id)
                .order_by(desc(CustomerServiceUsageEvent.id))
                .first()
            )
            if row is not None and int(row.needs_human) == 1:
                return True
    except Exception:
        logger.exception(
            "agent_assist_handoff_check_failed",
            conversation_id=conversation_id,
        )
    return False
