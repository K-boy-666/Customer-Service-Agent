"""Revenue attribution service for the cs-profit-engine (Task 5).

Pure functional service that records customer-service touch points and
attributes order revenue across them using one of four models:

- ``first_touch`` — all revenue credited to the earliest touch point.
- ``last_touch`` — all revenue credited to the latest touch point.
- ``linear`` — revenue split evenly across all touch points.
- ``time_decay`` — exponential decay with a 7-day half-life so touch
  points closer to the conversion time receive more credit.

Each attribution result is persisted as one ``AttributionRecord`` row per
touch point. The module also provides ROI computation (attributed revenue
vs. human + AI service cost) and a multi-model summary used by the value
dashboard.

Follows the same functional style as ``recommendation_service.py`` — every
public function takes a SQLAlchemy ``Session`` and returns plain data.
Permission checks live in the L1 ``analytics_agent`` wrapper; this module
performs only data-plane work.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from models import (
    AgentAssistEvent,
    AttributionRecord,
    CustomerServiceUsageEvent,
    Order,
    Recommendation,
    TouchPoint,
    UserProfile,
)

# Spec constants — SubTask 5.1 / 5.2.
ATTRIBUTION_WINDOW_HOURS = 24  # only touch points within 24h before order
ATTRIBUTION_MODELS: tuple[str, ...] = (
    "first_touch",
    "last_touch",
    "linear",
    "time_decay",
)
DEFAULT_MODEL = "last_touch"

# Time-decay half-life (per SubTask 5.1 spec).
TIME_DECAY_HALF_LIFE_DAYS = 7

# ROI unit costs (per SubTask 5.3 spec).
HUMAN_COST_PER_ASSIST_EVENT = 5.0   # ¥5 per AgentAssistEvent
AI_COST_PER_USAGE_EVENT = 0.1       # ¥0.1 per CustomerServiceUsageEvent

# Top-N rankings returned by compute_roi.
TOP_N_AGENTS = 5
TOP_N_SCRIPTS = 5


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def record_touch_point(
    session: Session,
    user_id: str,
    conversation_id: str,
    agent_id: str,
    recommendation_id: str | None = None,
    touch_type: str = "conversation",
) -> int:
    """Record a customer-service touch point and return its primary key.

    A touch point represents any meaningful customer-agent interaction
    (a conversation turn, an exposed recommendation, an adopted assist
    suggestion). Touch points are the unit of attribution — when an
    order is attributed, every touch point inside the 24-hour window
    receives a share of the revenue based on the chosen model.
    """
    touch = TouchPoint(
        user_id=user_id,
        conversation_id=conversation_id,
        agent_id=agent_id,
        recommendation_id=recommendation_id,
        touch_type=touch_type,
    )
    session.add(touch)
    session.flush()
    return int(touch.id)


def attribute_order(
    session: Session,
    order_id: str,
    model: str = DEFAULT_MODEL,
) -> list[dict[str, Any]]:
    """Attribute an order's revenue across its preceding touch points.

    Steps (per SubTask 5.1 spec):
    1. Resolve the order (``Order`` row) and its ``user_id`` by reverse
       lookup through ``UserProfile.primary_customer_id``. If no profile
       maps to ``order.customer_id``, return an empty list — there are no
       touch points to credit.
    2. Select every ``TouchPoint`` for that user whose ``touch_time``
       falls in ``[order.created_at - 24h, order.created_at]``.
    3. Distribute ``total_amount`` across the touch points using the
       chosen ``model``.
    4. Persist one ``AttributionRecord`` per touch point with an
       ``attribution_id`` of the form ``attr_<uuid4_hex[:16]>``.
    5. Return the persisted records as plain dicts.

    Returns an empty list when the order does not exist, when no user
    profile maps to its ``customer_id``, or when there are no touch
    points inside the 24-hour window.
    """
    if model not in ATTRIBUTION_MODELS:
        raise ValueError(
            f"unknown attribution model: {model!r}; "
            f"expected one of {ATTRIBUTION_MODELS}"
        )

    order = session.query(Order).filter_by(id=order_id).one_or_none()
    if order is None:
        return []

    user_id = _resolve_user_id_for_customer(session, order.customer_id)
    if user_id is None:
        return []

    conversion_time = _parse_datetime(order.created_at)
    if conversion_time is None:
        return []

    window_start = conversion_time - timedelta(hours=ATTRIBUTION_WINDOW_HOURS)
    touch_points = (
        session.query(TouchPoint)
        .filter(
            TouchPoint.user_id == user_id,
            TouchPoint.touch_time >= window_start,
            TouchPoint.touch_time <= conversion_time,
        )
        .order_by(TouchPoint.touch_time.asc(), TouchPoint.id.asc())
        .all()
    )
    if not touch_points:
        return []

    total_amount = float(order.total_amount)
    allocations = _allocate(total_amount, touch_points, conversion_time, model)

    records: list[dict[str, Any]] = []
    for touch, weight, attributed_amount in allocations:
        attribution_id = f"attr_{uuid4().hex[:16]}"
        record = AttributionRecord(
            attribution_id=attribution_id,
            order_id=order.id,
            user_id=user_id,
            conversation_id=touch.conversation_id,
            agent_id=touch.agent_id,
            recommendation_id=touch.recommendation_id,
            model=model,
            attributed_amount=attributed_amount,
            total_order_amount=total_amount,
            weight=weight,
        )
        session.add(record)
        session.flush()
        records.append(_attribution_record_to_dict(record))
    return records


def attribute_order_if_in_window(
    session: Session,
    order_id: str,
    model: str = DEFAULT_MODEL,
) -> list[dict[str, Any]]:
    """Subscribe-style entry point: attribute only if the order is recent.

    Used by the order-event subscription (SubTask 5.2). The order is
    attributed only when at least one touch point exists within 24 hours
    *before* the order's ``created_at``. Outside that window, returns an
    empty list without writing any records.
    """
    order = session.query(Order).filter_by(id=order_id).one_or_none()
    if order is None:
        return []

    user_id = _resolve_user_id_for_customer(session, order.customer_id)
    if user_id is None:
        return []

    conversion_time = _parse_datetime(order.created_at)
    if conversion_time is None:
        return []

    window_start = conversion_time - timedelta(hours=ATTRIBUTION_WINDOW_HOURS)
    latest = (
        session.query(TouchPoint)
        .filter(
            TouchPoint.user_id == user_id,
            TouchPoint.touch_time >= window_start,
            TouchPoint.touch_time <= conversion_time,
        )
        .order_by(desc(TouchPoint.touch_time))
        .first()
    )
    if latest is None:
        return []

    return attribute_order(session, order_id, model=model)


def compute_roi(
    session: Session,
    start: str,
    end: str,
    model: str = DEFAULT_MODEL,
) -> dict[str, Any]:
    """Compute ROI for a date range under a given attribution model.

    Composition (per SubTask 5.3 spec):
    - ``attributed_revenue`` = sum of ``AttributionRecord.attributed_amount``
      where ``model=model`` and ``attributed_at`` ∈ [start, end].
    - ``service_cost.human`` = count of ``AgentAssistEvent`` rows in the
      window × ¥5.0.
    - ``service_cost.ai`` = count of ``CustomerServiceUsageEvent`` rows
      in the window × ¥0.1.
    - ``service_cost.total`` = human + ai.
    - ``roi`` = (revenue - total_cost) / total_cost; ``0.0`` when cost is
      zero (avoids division-by-zero).
    - ``top_agents`` = top 5 ``agent_id`` by attributed revenue, desc.
    - ``top_scripts`` = top 5 ``(recommendation_id, script)`` by attributed
      revenue, desc — joins ``AttributionRecord`` to ``Recommendation`` to
      surface the script text.
    """
    start_dt = _parse_datetime(start)
    end_dt = _parse_datetime(end)
    if start_dt is None or end_dt is None:
        raise ValueError(f"invalid ROI date range: start={start!r} end={end!r}")

    revenue_rows = (
        session.query(AttributionRecord)
        .filter(
            AttributionRecord.model == model,
            AttributionRecord.attributed_at >= start_dt,
            AttributionRecord.attributed_at <= end_dt,
        )
        .all()
    )
    attributed_revenue = sum(float(r.attributed_amount) for r in revenue_rows)

    human_event_count = (
        session.query(func.count(AgentAssistEvent.id))
        .filter(
            AgentAssistEvent.created_at >= start_dt,
            AgentAssistEvent.created_at <= end_dt,
        )
        .scalar()
        or 0
    )
    ai_event_count = (
        session.query(func.count(CustomerServiceUsageEvent.id))
        .filter(
            CustomerServiceUsageEvent.created_at >= start_dt,
            CustomerServiceUsageEvent.created_at <= end_dt,
        )
        .scalar()
        or 0
    )
    human_cost = float(human_event_count) * HUMAN_COST_PER_ASSIST_EVENT
    ai_cost = float(ai_event_count) * AI_COST_PER_USAGE_EVENT
    total_cost = human_cost + ai_cost
    roi = 0.0 if total_cost == 0 else (attributed_revenue - total_cost) / total_cost

    top_agents = _top_agents(revenue_rows, TOP_N_AGENTS)
    top_scripts = _top_scripts(session, revenue_rows, TOP_N_SCRIPTS)

    return {
        "attributed_revenue": attributed_revenue,
        "service_cost": {
            "human": human_cost,
            "ai": ai_cost,
            "total": total_cost,
        },
        "roi": roi,
        "top_agents": top_agents,
        "top_scripts": top_scripts,
    }


def list_attributions(
    session: Session,
    start: str | None = None,
    end: str | None = None,
    model: str | None = None,
    user_id: str | None = None,
    order_id: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Return attribution records filtered by the supplied dimensions.

    ``start`` / ``end`` are inclusive ISO datetime bounds on
    ``AttributionRecord.attributed_at``. ``limit`` is clamped to a
    non-negative integer; the default of 100 mirrors the dashboard
    page-size convention.
    """
    query = session.query(AttributionRecord)
    if start is not None:
        start_dt = _parse_datetime(start)
        if start_dt is not None:
            query = query.filter(AttributionRecord.attributed_at >= start_dt)
    if end is not None:
        end_dt = _parse_datetime(end)
        if end_dt is not None:
            query = query.filter(AttributionRecord.attributed_at <= end_dt)
    if model is not None:
        query = query.filter(AttributionRecord.model == model)
    if user_id is not None:
        query = query.filter(AttributionRecord.user_id == user_id)
    if order_id is not None:
        query = query.filter(AttributionRecord.order_id == order_id)

    rows = (
        query.order_by(desc(AttributionRecord.attributed_at), desc(AttributionRecord.id))
        .limit(max(0, int(limit)))
        .all()
    )
    return [_attribution_record_to_dict(row) for row in rows]


def get_attribution_summary(
    session: Session,
    start: str,
    end: str,
) -> dict[str, Any]:
    """Multi-model attribution summary across a date range.

    For each of the four supported models, returns ``attributed_revenue``
    (sum of ``attributed_amount``) and ``record_count`` for records whose
    ``attributed_at`` falls in ``[start, end]``. ``total_orders`` and
    ``total_revenue`` aggregate across all models — distinct orders are
    counted once, and ``total_revenue`` is the sum of those distinct
    orders' ``total_order_amount`` to avoid double-counting.
    """
    start_dt = _parse_datetime(start)
    end_dt = _parse_datetime(end)
    if start_dt is None or end_dt is None:
        raise ValueError(
            f"invalid attribution summary date range: start={start!r} end={end!r}"
        )

    models_summary: dict[str, dict[str, Any]] = {}
    for model in ATTRIBUTION_MODELS:
        rows = (
            session.query(AttributionRecord)
            .filter(
                AttributionRecord.model == model,
                AttributionRecord.attributed_at >= start_dt,
                AttributionRecord.attributed_at <= end_dt,
            )
            .all()
        )
        models_summary[model] = {
            "attributed_revenue": sum(float(r.attributed_amount) for r in rows),
            "record_count": len(rows),
        }

    # Distinct orders across all models in the window — each order counted
    # once regardless of how many models attributed it. We pull all records
    # in the window and deduplicate in Python so the summary works on both
    # SQLite and MySQL (PostgreSQL's DISTINCT ON is not portable).
    window_rows = (
        session.query(AttributionRecord)
        .filter(
            AttributionRecord.attributed_at >= start_dt,
            AttributionRecord.attributed_at <= end_dt,
        )
        .all()
    )
    order_totals: dict[str, float] = {}
    for row in window_rows:
        # setdefault keeps the first seen total_order_amount per order so
        # repeated attributions under different models do not inflate the
        # total revenue.
        order_totals.setdefault(row.order_id, float(row.total_order_amount))
    total_orders = len(order_totals)
    total_revenue = sum(order_totals.values())

    return {
        "models": models_summary,
        "total_orders": total_orders,
        "total_revenue": total_revenue,
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _resolve_user_id_for_customer(session: Session, customer_id: int) -> str | None:
    """Reverse-lookup ``user_id`` from ``customer_id`` via ``UserProfile``.

    Per the SubTask 5.1 spec: ``customer_id → UserProfile.primary_customer_id``
    yields the ``user_id`` whose touch points should be credited. When no
    profile maps to the customer, returns ``None`` and the caller treats
    the order as having no touch points to attribute.
    """
    profile = (
        session.query(UserProfile)
        .filter_by(primary_customer_id=customer_id)
        .one_or_none()
    )
    if profile is None:
        return None
    return profile.user_id


def _parse_datetime(value: Any) -> datetime | None:
    """Parse an ISO datetime or date string into a naive ``datetime``.

    ``Order.created_at`` is stored as ``String(40)`` ISO datetime; ROI
    inputs are ISO date or datetime strings. Returns ``None`` for empty
    or unparseable input so callers can short-circuit gracefully.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value)
        except ValueError:
            return None
    else:
        return None
    if dt.tzinfo is not None:
        dt = dt.replace(tzinfo=None)
    return dt


def _allocate(
    total_amount: float,
    touch_points: list[TouchPoint],
    conversion_time: datetime,
    model: str,
) -> list[tuple[TouchPoint, float, float]]:
    """Allocate ``total_amount`` across touch points per ``model``.

    Returns a list of ``(touch_point, weight, attributed_amount)`` tuples
    in the same order as ``touch_points`` (which is ``touch_time asc``).
    Weights are normalised to sum to 1.0 for each model so the
    attributed amounts sum to ``total_amount`` exactly.
    """
    n = len(touch_points)
    if n == 0:
        return []

    if model == "first_touch":
        allocations: list[tuple[TouchPoint, float, float]] = []
        for i, touch in enumerate(touch_points):
            if i == 0:
                allocations.append((touch, 1.0, total_amount))
            else:
                allocations.append((touch, 0.0, 0.0))
        return allocations

    if model == "last_touch":
        allocations = []
        for i, touch in enumerate(touch_points):
            if i == n - 1:
                allocations.append((touch, 1.0, total_amount))
            else:
                allocations.append((touch, 0.0, 0.0))
        return allocations

    if model == "linear":
        share = total_amount / n
        weight = 1.0 / n
        return [(touch, weight, share) for touch in touch_points]

    if model == "time_decay":
        # weight_i = 0.5 ** ((conversion_time - touch_time_i) / half_life)
        weights = []
        for touch in touch_points:
            touch_time = touch.touch_time
            if touch_time.tzinfo is not None:
                touch_time = touch_time.replace(tzinfo=None)
            delta_days = (conversion_time - touch_time).total_seconds() / 86400.0
            # Negative deltas (touch after conversion) shouldn't normally
            # occur because the query filters by touch_time <= conversion,
            # but clamp to 0 to keep the math well-defined.
            delta_days = max(delta_days, 0.0)
            weights.append(0.5 ** (delta_days / TIME_DECAY_HALF_LIFE_DAYS))
        weight_sum = sum(weights)
        if weight_sum <= 0:
            # Degenerate: distribute evenly as a safety net.
            share = total_amount / n
            weight = 1.0 / n
            return [(touch, weight, share) for touch in touch_points]
        return [
            (touch, w / weight_sum, total_amount * (w / weight_sum))
            for touch, w in zip(touch_points, weights, strict=True)
        ]

    # Unreachable — model is validated by the caller.
    raise ValueError(f"unknown attribution model: {model!r}")


def _top_agents(records: list[AttributionRecord], top_n: int) -> list[dict[str, Any]]:
    """Rank ``agent_id`` by attributed revenue, return top-N (desc).

    ``agent_id`` is nullable in the schema; rows with a null agent_id are
    excluded from the ranking. Ties are broken by ``agent_id`` asc for
    deterministic output across DB engines.
    """
    totals: dict[str, float] = {}
    for r in records:
        if not r.agent_id:
            continue
        totals[r.agent_id] = totals.get(r.agent_id, 0.0) + float(r.attributed_amount)
    ranked = sorted(totals.items(), key=lambda kv: (-kv[1], kv[0]))[:top_n]
    return [{"agent_id": agent_id, "revenue": revenue} for agent_id, revenue in ranked]


def _top_scripts(
    session: Session, records: list[AttributionRecord], top_n: int
) -> list[dict[str, Any]]:
    """Rank recommendation scripts by attributed revenue, return top-N (desc).

    Joins ``AttributionRecord.recommendation_id`` → ``Recommendation.script``
    so the dashboard can surface the actual话术 text. Records without a
    ``recommendation_id`` are skipped. Ties are broken by
    ``recommendation_id`` asc for deterministic output.
    """
    totals: dict[str, float] = {}
    for r in records:
        if not r.recommendation_id:
            continue
        totals[r.recommendation_id] = (
            totals.get(r.recommendation_id, 0.0) + float(r.attributed_amount)
        )
    if not totals:
        return []

    rec_rows = (
        session.query(Recommendation)
        .filter(Recommendation.recommendation_id.in_(list(totals.keys())))
        .all()
    )
    script_by_id = {row.recommendation_id: row.script for row in rec_rows}

    ranked = sorted(totals.items(), key=lambda kv: (-kv[1], kv[0]))[:top_n]
    return [
        {
            "recommendation_id": rec_id,
            "script": script_by_id.get(rec_id, ""),
            "revenue": revenue,
        }
        for rec_id, revenue in ranked
    ]


def _attribution_record_to_dict(row: AttributionRecord) -> dict[str, Any]:
    return {
        "attribution_id": row.attribution_id,
        "order_id": row.order_id,
        "user_id": row.user_id,
        "touch_point_id": _touch_point_id_for_record(row),
        "conversation_id": row.conversation_id,
        "agent_id": row.agent_id,
        "recommendation_id": row.recommendation_id,
        "model": row.model,
        "attributed_amount": float(row.attributed_amount),
        "total_order_amount": float(row.total_order_amount),
        "weight": float(row.weight),
        "attributed_at": row.attributed_at.isoformat() if row.attributed_at else None,
    }


def _touch_point_id_for_record(row: AttributionRecord) -> int | None:
    """Best-effort recovery of the source touch_point.id for a record.

    The schema denormalises attribution away from touch_point to keep
    historical records immutable even if a touch point is later deleted.
    For the public API we still surface a ``touch_point_id`` by
    reverse-looking-up the (user_id, conversation_id, agent_id,
    recommendation_id) tuple — when no unique match exists, returns
    ``None`` rather than guessing.
    """
    # Lazy import avoids a circular reference at module load time.
    session = Session.object_session(row)
    if session is None:
        return None
    query = session.query(TouchPoint).filter_by(
        user_id=row.user_id,
        conversation_id=row.conversation_id or "",
        agent_id=row.agent_id or "",
    )
    if row.recommendation_id is None:
        query = query.filter(TouchPoint.recommendation_id.is_(None))
    else:
        query = query.filter_by(recommendation_id=row.recommendation_id)
    touch = query.order_by(TouchPoint.id.desc()).first()
    return int(touch.id) if touch is not None else None
