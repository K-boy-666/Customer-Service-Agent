"""Proactive human-handoff upgrade for the cs-profit-engine (Task 8.1).

Extends the existing ``human-handoff-agent`` with a *proactive* trigger:
when a VIP user's demand-mining result reports
``intent_confidence < 0.7``, the orchestrator prepares a complete
handoff payload (user profile + recent recommendations + conversation
summary) so the human agent can pick up the conversation with full
context — without the customer having to ask.

The module is purely functional (no global state, no I/O of its own).
Every public function takes a SQLAlchemy ``Session`` (where data access
is needed) and returns plain data, mirroring the convention used by
``user_profile_service`` / ``recommendation_service`` / etc.

Trigger rule (per spec SubTask 8.1 / Requirement "智能人机协同"):
    user_value_tier == "vip" AND intent_confidence < 0.7
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import desc
from sqlalchemy.orm import Session

import demand_mining_service as dms
import recommendation_service as recs
import user_profile_service as ups
from models import CustomerServiceUsageEvent

# Proactive-handoff trigger thresholds (per spec).
PROACTIVE_HANDOFF_VALUE_TIER = "vip"
PROACTIVE_HANDOFF_CONFIDENCE_THRESHOLD = 0.7

# How many recent recommendations to surface in the handoff payload.
HANDOFF_RECOMMENDATION_LIMIT = 5

# How many recent usage events to scan when building the conversation
# summary. Bounded so a long-running conversation does not blow up the
# payload size; 10 turns is plenty for a human agent's first glance.
CONVERSATION_SUMMARY_EVENT_LIMIT = 10


def should_proactively_handoff(
    user_value_tier: str,
    intent_confidence: float,
) -> bool:
    """Return ``True`` when the proactive-handoff rule fires.

    Rule (per spec): ``user_value_tier == "vip"`` AND
    ``intent_confidence < 0.7``. Non-vip users never trigger the
    proactive path even at very low confidence — they go through the
    standard reactive handoff flow when they explicitly request it or
    when the L3 emergency triggers fire.

    A ``None`` / empty tier string is treated as non-vip; a missing
    confidence is treated as 0.0 (which would trigger for a vip user,
    matching the "we don't know what the customer wants, hand off"
    semantics).
    """
    tier = user_value_tier or ""
    confidence = float(intent_confidence) if intent_confidence is not None else 0.0
    return tier == PROACTIVE_HANDOFF_VALUE_TIER and confidence < PROACTIVE_HANDOFF_CONFIDENCE_THRESHOLD


def evaluate_proactive_handoff(
    session: Session,
    user_id: str,
    mining_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate the proactive-handoff rule for a user.

    Flow:
    1. Read the user's 360° profile via ``user_profile_service.get_profile``
       to obtain ``user_value_tier``. A missing profile yields
       ``user_value_tier="low"`` (mirroring
       ``demand_mining_service._resolve_value_tier``) so the rule never
       blocks on missing profile data.
    2. Resolve ``intent_confidence`` from ``mining_result`` when supplied;
       otherwise re-run ``demand_mining_service.mine_demand`` with an
       empty context (which classifies the empty message as
       ``intent:general`` / confidence 0.4 — not enough to trigger on
       its own, but provides a deterministic fallback when the caller
       has no mining context yet).
    3. Apply :func:`should_proactively_handoff`. When it fires, build the
       handoff payload via :func:`build_handoff_payload` (the
       conversation_id is best-effort: the caller may not have one
       yet, in which case the payload's ``conversation_summary`` is
       empty and the recommendations are still surfaced from history).

    Returns ``{"should_handoff": bool, "reason": str, "payload": dict | None}``.
    The ``reason`` is a stable machine-readable code so callers can route
    on it; a human-readable description is intentionally omitted to keep
    the payload small and stable across i18n.
    """
    profile = ups.get_profile(session, user_id)
    if profile is None:
        user_value_tier = "low"
    else:
        value = profile.get("value") or {}
        user_value_tier = str(value.get("tier") or "low")

    if mining_result is not None:
        intent_confidence = float(mining_result.get("intent_confidence") or 0.0)
    else:
        # Fallback: re-mine with an empty context. classify_intent("")
        # returns ("intent:general", 0.4) — a non-triggering default.
        fallback_mining = dms.mine_demand(
            session,
            user_id,
            {"message": "", "mentioned_skus": []},
        )
        intent_confidence = float(fallback_mining.get("intent_confidence") or 0.0)

    if not should_proactively_handoff(user_value_tier, intent_confidence):
        return {
            "should_handoff": False,
            "reason": "no_trigger",
            "payload": None,
        }

    # Proactive trigger fired — build the payload. The conversation_id
    # is best-effort: the caller may not have one yet (the orchestrator
    # is evaluating *before* dispatching). When empty, the
    # conversation_summary is empty and recommendations come from
    # user-level history.
    payload = build_handoff_payload(
        session,
        user_id=user_id,
        conversation_id="",
    )
    return {
        "should_handoff": True,
        "reason": "vip_low_confidence",
        "payload": payload,
    }


def build_handoff_payload(
    session: Session,
    user_id: str,
    conversation_id: str,
) -> dict[str, Any]:
    """Build the handoff payload for a user / conversation.

    Composition (per spec SubTask 8.1):
    - ``user_profile``: ``user_profile_service.get_profile`` output.
      When the profile is missing, an empty dict is returned so the
      human agent still gets a (sparse) payload rather than ``None``.
    - ``recommendations``: the user's most recent ``HANDOFF_RECOMMENDATION_LIMIT``
      recommendations (via ``recommendation_service.list_user_recommendations``)
      so the human agent can pick up where the AI left off.
    - ``conversation_summary``: a short text summary built from the
      last ``CONVERSATION_SUMMARY_EVENT_LIMIT`` ``CustomerServiceUsageEvent``
      rows for the conversation, including status, intents, and whether
      the conversation has been flagged ``needs_human``. Empty string
      when ``conversation_id`` is falsy or no events exist.
    """
    profile = ups.get_profile(session, user_id)
    user_profile = profile if profile is not None else {}

    recommendations = recs.list_user_recommendations(
        session,
        user_id=user_id,
        limit=HANDOFF_RECOMMENDATION_LIMIT,
    )

    conversation_summary = _build_conversation_summary(
        session,
        conversation_id=conversation_id,
    )

    return {
        "user_profile": user_profile,
        "recommendations": recommendations,
        "conversation_summary": conversation_summary,
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_conversation_summary(
    session: Session,
    conversation_id: str,
) -> str:
    """Build a short text summary of the recent conversation history.

    Pulls the last ``CONVERSATION_SUMMARY_EVENT_LIMIT``
    ``CustomerServiceUsageEvent`` rows for ``conversation_id`` and
    formats them as a multi-line string. Returns an empty string when
    ``conversation_id`` is falsy or no events exist.

    The summary intentionally captures *structured* fields
    (``status``, ``emotional_level``, ``needs_human``, ``intents``,
    ``dispatched_agents``) rather than free text — the human agent can
    scan these at a glance and the format is stable across i18n.
    """
    if not conversation_id:
        return ""
    rows = (
        session.query(CustomerServiceUsageEvent)
        .filter_by(conversation_id=conversation_id)
        .order_by(desc(CustomerServiceUsageEvent.id))
        .limit(CONVERSATION_SUMMARY_EVENT_LIMIT)
        .all()
    )
    if not rows:
        return ""

    # Reverse to chronological order for the summary (oldest first).
    rows = list(reversed(rows))
    lines: list[str] = []
    for idx, event in enumerate(rows, start=1):
        intent_labels = [
            str(intent.get("intent", ""))
            for intent in (event.intents or [])
            if isinstance(intent, dict)
        ]
        agents = list(event.dispatched_agents or [])
        lines.append(
            f"#{idx} status={event.status}"
            f" emotion={event.emotional_level or 'n/a'}"
            f" needs_human={event.needs_human}"
            f" intents=[{', '.join(intent_labels)}]"
            f" agents=[{', '.join(agents)}]"
        )
    return "\n".join(lines)
