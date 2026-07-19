"""Agent-assist suggestion service for the cs-profit-engine (Task 8.2).

Generates real-time suggestions for human agents handling handed-off
conversations: reply scripts, knowledge (FAQ) entries, and cross-sell
opportunities. Each suggestion can be recorded as an
``AgentAssistEvent`` row so the attribution pipeline (Task 5) can credit
revenue to the suggestions a human agent actually adopted.

Functional style: every public function takes a SQLAlchemy ``Session``
(where data access is needed) and returns plain data. Permission checks
live in the API layer; this module performs only data-plane work.

Suggestion types (per spec):
- ``script``     — reply话术 derived from the latest user recommendation
                  or synthesised from the mining intent.
- ``knowledge``  — top FAQ match retrieved via ``kb_service`` (lexical /
                  embedding backend auto-selected by the retriever).
- ``cross_sell`` — cross-sell opportunity from ``mining_result.opportunities``,
                  ordered by ``opportunity_score`` desc.

The suggestion list is intentionally capped at one entry per type so
the agent's UI is not flooded; the caller can fetch more via
``list_user_recommendations`` / ``FaqRetrievalService.search`` directly.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import desc
from sqlalchemy.orm import Session

import recommendation_service as recs
from kb_service import get_faq_retriever
from models import AgentAssistEvent

# Default FAQ path — same convention as ``orchestrator_runtime.LocalCustomerServiceTools``.
DEFAULT_FAQ_PATH = Path(__file__).parent.parent / "data" / "faq.json"

# Assist suggestion types (per spec). The order is stable so callers can
# rely on the first script/knowledge/cross_sell triad.
ASSIST_TYPE_SCRIPT = "script"
ASSIST_TYPE_KNOWLEDGE = "knowledge"
ASSIST_TYPE_CROSS_SELL = "cross_sell"
ASSIST_TYPES: frozenset[str] = frozenset(
    {ASSIST_TYPE_SCRIPT, ASSIST_TYPE_KNOWLEDGE, ASSIST_TYPE_CROSS_SELL}
)

# How many FAQ entries to retrieve for the knowledge suggestion. We only
# surface the top match in the suggestion, but the retriever scores the
# whole corpus first.
KNOWLEDGE_SEARCH_LIMIT = 1

# How many recent recommendations to scan for the script suggestion.
SCRIPT_RECOMMENDATION_LOOKBACK = 5

# Cache the FAQ retriever instance per process — loading + tokenising the
# FAQ corpus on every call is wasteful; the retriever is read-only and
# safe to share across threads (it has no mutable state after init).
_faq_retriever_cache: Any = None
_faq_retriever_path: str | None = None


def generate_assist_suggestions(
    session: Session,
    conversation_id: str,
    user_id: str,
    mining_result: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Generate agent-assist suggestions for a handed-off conversation.

    Returns at most one suggestion per ``assist_type`` (script / knowledge
    / cross_sell), in that fixed order. Each suggestion dict has keys:
    ``assist_type``, ``content``, ``metadata``. When a type has no usable
    signal (e.g. no recommendations yet, no FAQ match, no opportunities),
    it is omitted from the list rather than emitting an empty placeholder
    — the agent UI then renders only the suggestions that exist.

    ``mining_result`` is the dict produced by
    ``demand_mining_service.mine_demand`` (intent / opportunities / etc.).
    When ``None``, the function still attempts script + knowledge
    suggestions from historical data; cross-sell is skipped.
    """
    suggestions: list[dict[str, Any]] = []

    script_suggestion = _build_script_suggestion(session, user_id, mining_result)
    if script_suggestion is not None:
        suggestions.append(script_suggestion)

    knowledge_suggestion = _build_knowledge_suggestion(mining_result)
    if knowledge_suggestion is not None:
        suggestions.append(knowledge_suggestion)

    cross_sell_suggestion = _build_cross_sell_suggestion(mining_result)
    if cross_sell_suggestion is not None:
        suggestions.append(cross_sell_suggestion)

    return suggestions


def record_assist_event(
    session: Session,
    conversation_id: str,
    agent_id: str,
    assist_type: str,
    content: str,
    adopted: bool = False,
) -> int:
    """Record an ``AgentAssistEvent`` row and return its primary key.

    ``assist_type`` should be one of :data:`ASSIST_TYPES` (script /
    knowledge / cross_sell) but the column is free-form ``String(40)`` so
    future assist types do not require a migration. ``adopted`` is
    stored as ``1`` / ``0`` to match the column's integer type.

    The caller is responsible for committing the surrounding
    ``session_scope`` — this function only ``flush``es so the generated
    primary key is readable immediately.
    """
    event = AgentAssistEvent(
        conversation_id=conversation_id,
        agent_id=agent_id,
        assist_type=assist_type,
        content=content,
        adopted=1 if adopted else 0,
    )
    session.add(event)
    session.flush()
    return int(event.id)


def list_assist_events(
    session: Session,
    conversation_id: str,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Return assist events for ``conversation_id`` newest-first.

    ``limit`` is clamped to a non-negative integer; the default of 50
    mirrors the dashboard page-size convention.
    """
    rows = (
        session.query(AgentAssistEvent)
        .filter_by(conversation_id=conversation_id)
        .order_by(desc(AgentAssistEvent.id))
        .limit(max(0, int(limit)))
        .all()
    )
    return [_event_to_dict(row) for row in rows]


def adopt_assist_suggestion(
    session: Session,
    event_id: int,
) -> bool:
    """Mark an assist event as adopted; return whether a row was updated.

    Idempotent: re-adopting an already-adopted event returns ``True``
    (the row exists and is in the desired state). Returns ``False`` only
    when ``event_id`` does not match any row.
    """
    event = session.get(AgentAssistEvent, event_id)
    if event is None:
        return False
    event.adopted = 1
    session.flush()
    return True


# ---------------------------------------------------------------------------
# Internal helpers — suggestion builders
# ---------------------------------------------------------------------------


def _build_script_suggestion(
    session: Session,
    user_id: str,
    mining_result: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Build a script (话术) suggestion from the latest recommendation.

    Prefers the latest persisted ``Recommendation.script`` for the user
    (so the agent sees the same话术 the AI would have surfaced). When no
    recommendation exists yet, falls back to a minimal script synthesised
    from the mining intent so the agent always has *something* to say.
    """
    recs_list = recs.list_user_recommendations(
        session,
        user_id=user_id,
        limit=SCRIPT_RECOMMENDATION_LOOKBACK,
    )
    metadata: dict[str, Any] = {}
    if recs_list:
        latest = recs_list[0]
        script_text = str(latest.get("script") or "")
        if not script_text:
            return None
        metadata = {
            "source": "recommendation",
            "recommendation_id": latest.get("recommendation_id"),
            "recommend_type": latest.get("recommend_type"),
            "target_sku": latest.get("target_ref"),
        }
    else:
        intent = str((mining_result or {}).get("intent") or "")
        if not intent:
            return None
        script_text = _synthesise_script_from_intent(intent)
        metadata = {"source": "intent", "intent": intent}

    return {
        "assist_type": ASSIST_TYPE_SCRIPT,
        "content": script_text,
        "metadata": metadata,
    }


def _build_knowledge_suggestion(
    mining_result: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Build a knowledge (FAQ) suggestion from the mining intent.

    Uses the global :func:`kb_service.get_faq_retriever` so the lexical /
    embedding backend selection is consistent with the consultation
    agent's FAQ tool. When no FAQ matches (retriever returned no entry
    above threshold), returns ``None`` so the suggestion is omitted.
    """
    intent = str((mining_result or {}).get("intent") or "")
    query = _faq_query_from_intent(intent)
    if not query:
        return None
    try:
        retriever = _get_faq_retriever()
    except Exception:
        # FAQ corpus unavailable — skip knowledge suggestion rather
        # than blocking the whole assist pipeline.
        return None
    entries = retriever.search(query, limit=KNOWLEDGE_SEARCH_LIMIT)
    if not entries:
        return None
    entry = entries[0]
    content = str(entry.get("answer") or "")
    if not content:
        return None
    return {
        "assist_type": ASSIST_TYPE_KNOWLEDGE,
        "content": content,
        "metadata": {
            "faq_id": entry.get("id"),
            "category": entry.get("category"),
            "question": entry.get("question"),
            "relevance": entry.get("relevance"),
            "retriever": entry.get("retriever"),
        },
    }


def _build_cross_sell_suggestion(
    mining_result: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Build a cross-sell suggestion from ``mining_result.opportunities``.

    Picks the highest-scoring ``cross_sell`` opportunity (ties broken by
    target_sku asc). Up-sell opportunities are intentionally not surfaced
    here — the script suggestion already carries the up-sell话术 when a
    recommendation was generated. When no opportunity exists, returns
    ``None``.
    """
    if not mining_result:
        return None
    opportunities = list(mining_result.get("opportunities") or [])
    cross_sell = [
        o for o in opportunities if str(o.get("type") or "") == "cross_sell"
    ]
    if not cross_sell:
        return None
    cross_sell.sort(
        key=lambda o: (-float(o.get("opportunity_score") or 0.0), str(o.get("target_sku") or ""))
    )
    top = cross_sell[0]
    target_sku = str(top.get("target_sku") or "")
    target_name = str(top.get("target_name") or "")
    reason = str(top.get("reason") or "")
    content = (
        f"交叉销售机会：{target_name}（SKU: {target_sku}）。"
        f"理由：{reason}"
    ) if reason else (
        f"交叉销售机会：{target_name}（SKU: {target_sku}）。"
    )
    return {
        "assist_type": ASSIST_TYPE_CROSS_SELL,
        "content": content,
        "metadata": {
            "target_sku": target_sku,
            "target_name": target_name,
            "opportunity_score": float(top.get("opportunity_score") or 0.0),
            "reason": reason,
        },
    }


# ---------------------------------------------------------------------------
# Internal helpers — utility
# ---------------------------------------------------------------------------


def _synthesise_script_from_intent(intent: str) -> str:
    """Synthesise a minimal话术 from the mining intent.

    Used only as a fallback when the user has no persisted
    ``Recommendation`` rows yet (e.g. a brand-new conversation). The
    templates cover the four opportunity-bearing intents plus a default
    so the agent always has *some* opening line.
    """
    if intent == "intent:product_inquiry":
        return "您好，关于这款商品我可以为您详细介绍规格、价格和适用场景，请问您最关心哪一方面？"
    if intent == "intent:upgrade_inquiry":
        return "您好，根据您的咨询，我为您对比升级款与当前款的差异，您看可以吗？"
    if intent == "intent:after_sales_return":
        return "您好，我理解您遇到售后问题，我先核对订单信息，再为您发起申请，请稍等。"
    if intent == "intent:complaint":
        return "您好，我先认真倾听您的问题，再为您跟进处理，感谢您的耐心。"
    if intent == "intent:logistics_inquiry":
        return "您好，我立刻为您查询物流状态，请稍等。"
    return "您好，我是您的专属客服，请问有什么可以帮您？"


def _faq_query_from_intent(intent: str) -> str:
    """Map a mining intent to an FAQ search query.

    The mining intent labels (``intent:product_inquiry`` etc.) are not
    FAQ keywords directly; this helper expands each intent into a
    keyword the FAQ retriever can match (e.g. ``product_inquiry`` →
    ``"产品"`` so the lexical scorer hits product-related entries).
    """
    mapping = {
        "intent:after_sales_return": "退货",
        "intent:product_inquiry": "产品",
        "intent:upgrade_inquiry": "升级",
        "intent:logistics_inquiry": "物流",
        "intent:complaint": "投诉",
    }
    if intent in mapping:
        return mapping[intent]
    return ""


def _get_faq_retriever() -> Any:
    """Return a process-wide cached ``FaqRetrievalService``.

    ``kb_service.get_faq_retriever`` is itself ``lru_cache``-d on the
    faq_path string, so this thin wrapper mainly exists to default the
    path and keep imports lazy (so ``agent_assist_service`` does not
    pay the FAQ-load cost at module import).
    """
    global _faq_retriever_cache, _faq_retriever_path
    path = str(DEFAULT_FAQ_PATH)
    if _faq_retriever_cache is None or _faq_retriever_path != path:
        _faq_retriever_cache = get_faq_retriever(path)
        _faq_retriever_path = path
    return _faq_retriever_cache


def _event_to_dict(row: AgentAssistEvent) -> dict[str, Any]:
    return {
        "id": int(row.id),
        "conversation_id": row.conversation_id,
        "agent_id": row.agent_id,
        "assist_type": row.assist_type,
        "content": row.content,
        "adopted": int(row.adopted),
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }
