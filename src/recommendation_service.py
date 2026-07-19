"""Proactive recommendation service for the cs-profit-engine (Task 4).

Pure functional service that converts mined demand opportunities into
customer-facing recommendations with话术 (script) and expected conversion
rate, plus a funnel-event recorder with 24-hour deduplication. Follows the
same functional style as ``analytics_service.py`` — every public function
takes a SQLAlchemy ``Session`` and returns plain data.

Permission checks live in the L1 ``recommendation_agent`` wrapper; this
module performs only data-plane work.

Flow (per SubTask 4.1):
  1. Filter ``opportunity_score > OPPORTUNITY_THRESHOLD``.
  2. Sort by score desc (ties broken by target_sku asc) for determinism.
  3. Take the first ``MAX_RECOMMENDATIONS``.
  4. Persist a ``Recommendation`` row per opportunity (recommendation_id
     of the form ``rec_<uuid4_hex[:16]>``).
  5. Build话术 (script) from type + target_name.
  6. Compute expected conversion rate from user_value_tier + score.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import desc
from sqlalchemy.orm import Session

import user_profile_service as ups
from models import FunnelEvent, Recommendation, now

# Spec constants — SubTask 4.1 / 4.2.
OPPORTUNITY_THRESHOLD = 0.6  # opportunity_score STRICTLY GREATER THAN 0.6 triggers a recommendation
MAX_RECOMMENDATIONS = 3      # ≤ 3 recommendations per generate_recommendations call
DEDUP_WINDOW_HOURS = 24      # 24-hour dedup window for funnel events

# Expected-conversion-rate composition (per SubTask 4.1).
#   base = opportunity_score * 0.5
#   value-tier boost: vip +0.15 / high +0.10 / medium +0.05 / low +0.
#   clamp to [0, 0.95].
CONVERSION_BASE_WEIGHT = 0.5
CONVERSION_TIER_BOOST: dict[str, float] = {
    "vip": 0.15,
    "high": 0.10,
    "medium": 0.05,
    "low": 0.0,
}
CONVERSION_MIN = 0.0
CONVERSION_MAX = 0.95

# Funnel event types recorded across the conversion funnel.
FUNNEL_EVENT_TYPES: frozenset[str] = frozenset(
    {"exposure", "click", "consult", "order"}
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_recommendations(
    session: Session,
    user_id: str,
    conversation_id: str,
    opportunities: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Generate ≤ ``MAX_RECOMMENDATIONS`` recommendations from mined opportunities.

    ``opportunities`` is the list produced by
    ``demand_mining_service.mine_demand`` — each item has ``type``,
    ``target_sku``, ``target_name``, ``opportunity_score``, ``reason``.

    Returns a list of dicts with keys: ``recommendation_id``,
    ``target_sku``, ``target_name``, ``recommend_type``, ``content``,
    ``script``, ``expected_conversion_rate``, ``opportunity_score``.
    Each generated recommendation is persisted as a ``Recommendation``
    row before the function returns.
    """
    qualified = [
        opp
        for opp in opportunities
        if float(opp.get("opportunity_score", 0.0)) > OPPORTUNITY_THRESHOLD
    ]
    # Sort by score desc; ties broken by target_sku asc for deterministic
    # output across DB engines and test runs.
    qualified.sort(
        key=lambda o: (
            -float(o.get("opportunity_score", 0.0)),
            str(o.get("target_sku", "")),
        )
    )
    selected = qualified[:MAX_RECOMMENDATIONS]

    user_value_tier = _resolve_value_tier(session, user_id)

    recommendations: list[dict[str, Any]] = []
    for opp in selected:
        recommend_type = str(opp.get("type", ""))
        target_sku = str(opp.get("target_sku", ""))
        target_name = str(opp.get("target_name", ""))
        opportunity_score = float(opp.get("opportunity_score", 0.0))

        script = _build_script(recommend_type, target_name)
        content = str(opp.get("reason", ""))
        expected_conversion_rate = _expected_conversion_rate(
            user_value_tier=user_value_tier,
            opportunity_score=opportunity_score,
        )
        recommendation_id = f"rec_{uuid4().hex[:16]}"

        session.add(
            Recommendation(
                recommendation_id=recommendation_id,
                user_id=user_id,
                conversation_id=conversation_id,
                recommend_type=recommend_type,
                target_ref=target_sku,
                content=content,
                script=script,
                expected_conversion_rate=expected_conversion_rate,
                opportunity_score=opportunity_score,
                status="pending",
            )
        )
        session.flush()

        recommendations.append(
            {
                "recommendation_id": recommendation_id,
                "target_sku": target_sku,
                "target_name": target_name,
                "recommend_type": recommend_type,
                "content": content,
                "script": script,
                "expected_conversion_rate": expected_conversion_rate,
                "opportunity_score": opportunity_score,
            }
        )
    return recommendations


def record_funnel_event(
    session: Session,
    recommendation_id: str,
    user_id: str,
    session_id: str,
    event_type: str,
    order_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> bool:
    """Record a funnel event with 24-hour deduplication.

    Same ``(recommendation_id, event_type)`` pair within
    ``DEDUP_WINDOW_HOURS`` is recorded only once. Returns ``True`` when a
    new row was written, ``False`` when the event was deduped.

    ``event_type`` is one of ``exposure`` / ``click`` / ``consult`` /
    ``order``. ``order_id`` is recorded only for the ``order`` event but
    is accepted for any event_type so callers do not need conditional
    logic.
    """
    cutoff = now() - timedelta(hours=DEDUP_WINDOW_HOURS)
    existing = (
        session.query(FunnelEvent)
        .filter(
            FunnelEvent.recommendation_id == recommendation_id,
            FunnelEvent.event_type == event_type,
            FunnelEvent.created_at >= cutoff,
        )
        .first()
    )
    if existing is not None:
        return False

    session.add(
        FunnelEvent(
            recommendation_id=recommendation_id,
            user_id=user_id,
            session_id=session_id,
            event_type=event_type,
            order_id=order_id,
            payload=dict(payload or {}),
        )
    )
    session.flush()
    return True


def is_recommendation_exposed_recently(
    session: Session, recommendation_id: str, hours: int = DEDUP_WINDOW_HOURS
) -> bool:
    """Return ``True`` when an ``exposure`` event exists for ``recommendation_id``
    within the last ``hours`` window (default 24h).
    """
    cutoff = now() - timedelta(hours=hours)
    existing = (
        session.query(FunnelEvent)
        .filter(
            FunnelEvent.recommendation_id == recommendation_id,
            FunnelEvent.event_type == "exposure",
            FunnelEvent.created_at >= cutoff,
        )
        .first()
    )
    return existing is not None


def get_recommendation(session: Session, recommendation_id: str) -> dict[str, Any] | None:
    """Return a recommendation by id, or ``None`` if not found."""
    row = (
        session.query(Recommendation)
        .filter_by(recommendation_id=recommendation_id)
        .one_or_none()
    )
    if row is None:
        return None
    return _recommendation_to_dict(row)


def list_user_recommendations(
    session: Session, user_id: str, limit: int = 20
) -> list[dict[str, Any]]:
    """Return the user's most recent recommendations (newest first)."""
    rows = (
        session.query(Recommendation)
        .filter_by(user_id=user_id)
        .order_by(desc(Recommendation.created_at), desc(Recommendation.id))
        .limit(max(0, int(limit)))
        .all()
    )
    return [_recommendation_to_dict(row) for row in rows]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _resolve_value_tier(session: Session, user_id: str) -> str:
    """Return the user's value tier, defaulting to ``"low"`` when no profile.

    Mirrors the fallback in ``demand_mining_service._resolve_value_tier``:
    a missing ``UserProfile`` (or an unknown tier string) falls back to
    ``"low"`` so the recommendation path never blocks on missing profile
    data and never inflates a score with an unknown tier.
    """
    profile = ups.get_profile(session, user_id)
    if profile is None:
        return "low"
    value = profile.get("value") or {}
    tier = value.get("tier") or "low"
    return tier if tier in CONVERSION_TIER_BOOST else "low"


def _build_script(recommend_type: str, target_name: str) -> str:
    """Generate the话术 (script) for a recommendation by type.

    Templates per spec:
    - cross_sell: 为您推荐搭配商品 {target_name}，与您咨询的商品常常一起购买，可享受更完整体验。
    - up_sell: 基于您的需求，{target_name} 是升级款，性能更强，更适合您的使用场景。
    - coupon: 为您奉上 {target_name} 专属优惠券，限时使用。
    """
    if recommend_type == "cross_sell":
        return (
            f"为您推荐搭配商品 {target_name}，与您咨询的商品常常一起购买，可享受更完整体验。"
        )
    if recommend_type == "up_sell":
        return (
            f"基于您的需求，{target_name} 是升级款，性能更强，更适合您的使用场景。"
        )
    if recommend_type == "coupon":
        return f"为您奉上 {target_name} 专属优惠券，限时使用。"
    return f"为您推荐 {target_name}。"


def _expected_conversion_rate(
    user_value_tier: str, opportunity_score: float
) -> float:
    """Compute the expected conversion rate, clamped to ``[0, 0.95]``.

    Composition (per spec):
    - base = opportunity_score * 0.5
    - value-tier boost: vip +0.15 / high +0.10 / medium +0.05 / low +0.
    - clamp to [0, 0.95].

    Unknown tiers fall back to the ``low`` boost (0.0) so a malformed
    profile never inflates a conversion estimate.
    """
    base = float(opportunity_score) * CONVERSION_BASE_WEIGHT
    tier_boost = CONVERSION_TIER_BOOST.get(user_value_tier, 0.0)
    rate = base + tier_boost
    return max(CONVERSION_MIN, min(CONVERSION_MAX, rate))


def _recommendation_to_dict(row: Recommendation) -> dict[str, Any]:
    return {
        "recommendation_id": row.recommendation_id,
        "user_id": row.user_id,
        "conversation_id": row.conversation_id,
        "recommend_type": row.recommend_type,
        "target_ref": row.target_ref,
        "content": row.content,
        "script": row.script,
        "expected_conversion_rate": row.expected_conversion_rate,
        "opportunity_score": row.opportunity_score,
        "status": row.status,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }
