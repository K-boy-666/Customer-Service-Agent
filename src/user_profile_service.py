"""Unified user profile service.

Aggregates multi-platform user identity, behavior, and conversation history
into a 360° user view: basic attributes + recent intent tags + value tier.

All functions take a SQLAlchemy ``Session`` and follow the same functional
style as ``analytics_service.py``. Permission checks are left to the API
layer (Task 6 / Task 9); this module performs only data-plane work.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import desc
from sqlalchemy.orm import Session

from models import (
    CustomerServiceUsageEvent,
    Order,
    UserIdentity,
    UserIntentTag,
    UserProfile,
    UserValueScore,
    now,
)

# Identity-type match priority (highest first), per SubTask 2.2.
IDENTITY_TYPE_PRIORITY: tuple[str, ...] = ("phone", "email", "open_id", "customer_id")

# Intent-tag retention window for the 360° view.
INTENT_TAG_WINDOW_DAYS = 30

# RFM R buckets: (max_age_days_inclusive, score).
RFM_R_BUCKETS: tuple[tuple[float, float], ...] = (
    (30.0, 100.0),
    (90.0, 70.0),
    (180.0, 40.0),
    (float("inf"), 10.0),
)

# RFM F buckets: (min_order_count_inclusive, score).
RFM_F_BUCKETS: tuple[tuple[int, float], ...] = (
    (11, 100.0),
    (6, 85.0),
    (2, 60.0),
    (1, 30.0),
)

# RFM M buckets: (min_total_amount_inclusive, score).
RFM_M_BUCKETS: tuple[tuple[float, float], ...] = (
    (5000.0, 100.0),
    (1000.0, 75.0),
    (100.0, 50.0),
    (0.0, 20.0),
)

# Interaction count buckets: (min_count_inclusive, base_weight).
INTERACTION_COUNT_BUCKETS: tuple[tuple[int, float], ...] = (
    (11, 80.0),
    (4, 50.0),
    (1, 20.0),
)
INTERACTION_RECENT_BONUS = 20.0  # +20 if any interaction in last 7 days
INTERACTION_MAX = 100.0

VALUE_WEIGHTS = {"r": 0.3, "f": 0.3, "m": 0.2, "interaction": 0.2}
VALUE_MAX_SCORE = 100.0

VIP_THRESHOLD = 85.0
HIGH_THRESHOLD = 60.0
MEDIUM_THRESHOLD = 30.0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_or_create_profile(session: Session, user_id: str) -> UserProfile:
    """Return the ``UserProfile`` for ``user_id``, creating an empty row if missing."""
    profile = session.query(UserProfile).filter_by(user_id=user_id).one_or_none()
    if profile is not None:
        return profile
    profile = UserProfile(user_id=user_id)
    session.add(profile)
    session.flush()
    return profile


def get_profile(session: Session, user_id: str) -> dict[str, Any] | None:
    """Return a 360° user view: basic attrs + recent intent tags + value tier.

    Returns ``None`` when ``user_id`` has no ``UserProfile``. If no value score
    has been persisted yet, one is computed (and stored) on demand so the
    returned view is always complete.
    """
    profile = session.query(UserProfile).filter_by(user_id=user_id).one_or_none()
    if profile is None:
        return None

    cutoff = now() - timedelta(days=INTENT_TAG_WINDOW_DAYS)
    intent_rows = (
        session.query(UserIntentTag)
        .filter(
            UserIntentTag.user_id == user_id,
            UserIntentTag.created_at >= cutoff,
        )
        .order_by(desc(UserIntentTag.created_at))
        .all()
    )
    intent_tags = [
        {
            "tag": row.tag,
            "source": row.source,
            "confidence": row.confidence,
            "created_at": row.created_at.isoformat(),
        }
        for row in intent_rows
    ]

    value = _latest_value_score(session, user_id)
    if value is None:
        value = compute_value_score(session, user_id)

    return {
        "user_id": profile.user_id,
        "display_name": profile.display_name,
        "primary_customer_id": profile.primary_customer_id,
        "aggregated_attrs": dict(profile.aggregated_attrs or {}),
        "intent_tags": intent_tags,
        "value": value,
    }


def update_profile(session: Session, user_id: str, attrs: dict[str, Any]) -> None:
    """Update basic profile attributes.

    Recognised top-level keys:
    - ``display_name`` (str): writes to the ``display_name`` column.
    - ``primary_customer_id`` (int | None): writes to the column; pass ``None``
      to clear.
    - ``aggregated_attrs`` (dict): deep-merged into the existing JSON column.
    Any other key is merged into ``aggregated_attrs`` as a top-level entry,
    so callers can freely extend the profile with custom attributes.
    """
    profile = get_or_create_profile(session, user_id)
    merged_attrs: dict[str, Any] = dict(profile.aggregated_attrs or {})
    for key, value in attrs.items():
        if key == "display_name":
            profile.display_name = "" if value is None else str(value)
        elif key == "primary_customer_id":
            profile.primary_customer_id = int(value) if value is not None else None
        elif key == "aggregated_attrs" and isinstance(value, dict):
            merged_attrs.update(value)
        else:
            merged_attrs[key] = value
    profile.aggregated_attrs = merged_attrs
    session.flush()


def merge_identity(
    session: Session,
    platform: str,
    identity_type: str,
    identity_value: str,
    primary_customer_id: int | None = None,
) -> str:
    """Merge a multi-platform identity, returning the resolved ``user_id``.

    Match order (SubTask 2.2):
    1. Exact ``(platform, identity_type, identity_value)`` in ``user_identity``.
    2. Same ``identity_value`` under any identity_type, searched in priority
       order ``phone > email > open_id > customer_id``. The first hit wins;
       the new ``(platform, identity_type, identity_value)`` row is linked to
       that existing ``user_id``.
    3. Fall back to creating a new ``UserProfile`` (``user_id`` of the form
       ``u_<uuid4_hex[:16]>``) plus a ``UserIdentity`` row.

    If ``primary_customer_id`` is supplied and the resolved profile has a
    null ``primary_customer_id``, the column is updated.
    """
    # Step 1: exact match.
    existing = (
        session.query(UserIdentity)
        .filter_by(
            platform=platform,
            identity_type=identity_type,
            identity_value=identity_value,
        )
        .one_or_none()
    )
    if existing is not None:
        _maybe_set_primary_customer_id(session, existing.user_id, primary_customer_id)
        return existing.user_id

    # Step 2: priority match across identity types (ignores platform).
    for candidate_type in IDENTITY_TYPE_PRIORITY:
        candidate = (
            session.query(UserIdentity)
            .filter_by(
                identity_type=candidate_type,
                identity_value=identity_value,
            )
            .first()
        )
        if candidate is not None:
            session.add(
                UserIdentity(
                    user_id=candidate.user_id,
                    platform=platform,
                    identity_type=identity_type,
                    identity_value=identity_value,
                )
            )
            session.flush()
            _maybe_set_primary_customer_id(session, candidate.user_id, primary_customer_id)
            return candidate.user_id

    # Step 3: create new profile.
    user_id = f"u_{uuid4().hex[:16]}"
    session.add(
        UserProfile(
            user_id=user_id,
            primary_customer_id=primary_customer_id,
        )
    )
    session.flush()
    session.add(
        UserIdentity(
            user_id=user_id,
            platform=platform,
            identity_type=identity_type,
            identity_value=identity_value,
        )
    )
    session.flush()
    return user_id


def update_intent_tag(
    session: Session,
    user_id: str,
    tag: str,
    source: str = "conversation",
    confidence: float = 0.0,
) -> None:
    """Upsert an intent tag for a user.

    If a ``(user_id, tag, source)`` row already exists, its ``confidence`` is
    overwritten and ``created_at`` is refreshed to ``now()`` (so the 30-day
    window for the 360° view rolls forward with the latest signal). Otherwise
    a new row is inserted.

    The 5-second persistence SLA (SubTask 2.3) is the caller's responsibility
    — this function is synchronous and commits nothing on its own; it relies
    on the surrounding ``session_scope`` (or explicit commit) to land the row.
    """
    profile = get_or_create_profile(session, user_id)
    existing = (
        session.query(UserIntentTag)
        .filter_by(user_id=profile.user_id, tag=tag, source=source)
        .one_or_none()
    )
    if existing is not None:
        existing.confidence = float(confidence)
        existing.created_at = now()
        session.flush()
        return
    session.add(
        UserIntentTag(
            user_id=profile.user_id,
            tag=tag,
            source=source,
            confidence=float(confidence),
        )
    )
    session.flush()


def compute_value_score(session: Session, user_id: str) -> dict[str, Any]:
    """Compute and persist the RFM + interaction value score for a user.

    Returns ``{score, tier, rfm_r, rfm_f, rfm_m, interaction_weight}``.
    A new ``UserValueScore`` row is inserted on every call so history is
    preserved; callers wanting the latest snapshot should read via
    ``get_profile`` (which falls back to this function when no snapshot
    exists).
    """
    profile = session.query(UserProfile).filter_by(user_id=user_id).one_or_none()
    customer_id = profile.primary_customer_id if profile is not None else None

    rfm_r = _compute_rfm_r(session, customer_id)
    rfm_f = _compute_rfm_f(session, customer_id)
    rfm_m = _compute_rfm_m(session, customer_id)
    interaction_weight = _compute_interaction_weight(session, customer_id)

    raw_score = (
        VALUE_WEIGHTS["r"] * rfm_r
        + VALUE_WEIGHTS["f"] * rfm_f
        + VALUE_WEIGHTS["m"] * rfm_m
        + VALUE_WEIGHTS["interaction"] * interaction_weight
    )
    score = min(raw_score, VALUE_MAX_SCORE)
    tier = _tier_for(score)

    session.add(
        UserValueScore(
            user_id=user_id,
            score=score,
            tier=tier,
            rfm_r=rfm_r,
            rfm_f=rfm_f,
            rfm_m=rfm_m,
            interaction_weight=interaction_weight,
        )
    )
    session.flush()

    return {
        "score": score,
        "tier": tier,
        "rfm_r": rfm_r,
        "rfm_f": rfm_f,
        "rfm_m": rfm_m,
        "interaction_weight": interaction_weight,
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _maybe_set_primary_customer_id(
    session: Session, user_id: str, primary_customer_id: int | None
) -> None:
    if primary_customer_id is None:
        return
    profile = session.query(UserProfile).filter_by(user_id=user_id).one_or_none()
    if profile is None:
        return
    if profile.primary_customer_id is None:
        profile.primary_customer_id = primary_customer_id
        session.flush()


def _latest_value_score(session: Session, user_id: str) -> dict[str, Any] | None:
    row = (
        session.query(UserValueScore)
        .filter_by(user_id=user_id)
        .order_by(desc(UserValueScore.computed_at), desc(UserValueScore.id))
        .first()
    )
    if row is None:
        return None
    return {
        "score": row.score,
        "tier": row.tier,
        "rfm_r": row.rfm_r,
        "rfm_f": row.rfm_f,
        "rfm_m": row.rfm_m,
        "interaction_weight": row.interaction_weight,
        "computed_at": row.computed_at.isoformat(),
    }


def _compute_rfm_r(session: Session, customer_id: int | None) -> float:
    if customer_id is None:
        return 0.0
    latest = (
        session.query(Order)
        .filter(Order.customer_id == customer_id)
        .order_by(desc(Order.created_at))
        .first()
    )
    if latest is None:
        return 0.0
    age_days = _days_since(latest.created_at)
    for max_days, score in RFM_R_BUCKETS:
        if age_days <= max_days:
            return score
    return 0.0


def _compute_rfm_f(session: Session, customer_id: int | None) -> float:
    if customer_id is None:
        return 0.0
    count = session.query(Order).filter(Order.customer_id == customer_id).count()
    if count == 0:
        return 0.0
    for min_count, score in RFM_F_BUCKETS:
        if count >= min_count:
            return score
    return 0.0


def _compute_rfm_m(session: Session, customer_id: int | None) -> float:
    if customer_id is None:
        return 0.0
    rows = (
        session.query(Order)
        .filter(Order.customer_id == customer_id)
        .with_entities(Order.total_amount)
        .all()
    )
    amounts = [row[0] for row in rows if row[0] is not None]
    if not amounts:
        return 0.0
    total = sum(amounts)
    for min_amount, score in RFM_M_BUCKETS:
        if total >= min_amount:
            return score
    return 0.0


def _compute_interaction_weight(session: Session, customer_id: int | None) -> float:
    if customer_id is None:
        return 0.0
    count = (
        session.query(CustomerServiceUsageEvent)
        .filter(CustomerServiceUsageEvent.customer_id == customer_id)
        .count()
    )
    if count == 0:
        return 0.0
    base = 0.0
    for min_count, weight in INTERACTION_COUNT_BUCKETS:
        if count >= min_count:
            base = weight
            break
    cutoff = now() - timedelta(days=7)
    recent = (
        session.query(CustomerServiceUsageEvent)
        .filter(
            CustomerServiceUsageEvent.customer_id == customer_id,
            CustomerServiceUsageEvent.created_at >= cutoff,
        )
        .first()
    )
    if recent is not None:
        base += INTERACTION_RECENT_BONUS
    return min(base, INTERACTION_MAX)


def _tier_for(score: float) -> str:
    if score > VIP_THRESHOLD:
        return "vip"
    if score >= HIGH_THRESHOLD:
        return "high"
    if score >= MEDIUM_THRESHOLD:
        return "medium"
    return "low"


def _days_since(value: Any) -> float:
    """Return days between ``value`` (ISO str or datetime) and ``now()``.

    ``Order.created_at`` is stored as an ISO-format ``String(40)``; this helper
    parses it and returns a non-negative float. Unparseable input is treated
    as infinitely old so it falls into the lowest R bucket.
    """
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value)
        except ValueError:
            return float("inf")
    elif isinstance(value, datetime):
        dt = value
    else:
        return float("inf")
    if dt.tzinfo is not None:
        dt = dt.replace(tzinfo=None)
    delta = now() - dt
    return max(delta.total_seconds() / 86400.0, 0.0)
