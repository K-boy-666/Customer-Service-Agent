"""Demand mining engine for the cs-profit-engine (Task 3).

Pure functional service that mines sales opportunities from a customer
conversation: rule-based intent classification, order-co-occurrence product
graph, and cross-sell / up-sell opportunity scoring. Follows the same
functional style as ``analytics_service.py`` — every public function takes a
SQLAlchemy ``Session`` (where data access is needed) and returns plain data.

No LLM calls: intent classification is keyword-based so tests are
reproducible. The module never writes to the database; ``mine_demand`` reads
the user profile via ``user_profile_service.get_profile`` but treats a missing
profile as ``user_value_tier="low"`` rather than raising.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

import user_profile_service as ups
from models import Order, OrderItem, Product

# ---------------------------------------------------------------------------
# Intent classification (rule-based, no LLM)
# ---------------------------------------------------------------------------

# (intent, confidence, keywords) — checked in this order; first hit wins.
# Complaint is checked before return so a complaining customer who also
# mentions "退货" is classified as a complaint. Upgrade is checked before
# product_inquiry so "咨询升级" resolves to the more specific upgrade_inquiry.
INTENT_RULES: tuple[tuple[str, float, tuple[str, ...]], ...] = (
    ("intent:complaint", 0.9, ("投诉", "差评", "不满")),
    ("intent:after_sales_return", 0.85, ("退货", "退款", "换货")),
    ("intent:upgrade_inquiry", 0.8, ("升级", "pro", "plus", "max")),
    ("intent:logistics_inquiry", 0.8, ("物流", "快递", "到哪")),
    ("intent:product_inquiry", 0.75, ("咨询", "了解", "问下")),
)

DEFAULT_INTENT = "intent:general"
DEFAULT_CONFIDENCE = 0.4

# Intents eligible for sales opportunity mining. logistics_inquiry and
# complaint are intentionally excluded — a logistics question carries no
# cross-sell signal, and pitching during a complaint damages trust.
OPPORTUNITY_INTENTS: frozenset[str] = frozenset(
    {
        "intent:after_sales_return",
        "intent:product_inquiry",
        "intent:upgrade_inquiry",
    }
)


def classify_intent(message: str, order_id: str | None = None) -> tuple[str, float]:
    """Classify a customer message into an intent label + confidence.

    Pure keyword matching (no LLM) so tests are reproducible. Matching is
    case-insensitive for English keywords (``pro`` / ``plus`` / ``max``).
    The optional ``order_id`` is reserved for future rule enrichment and is
    currently unused.
    """
    _ = order_id  # reserved for future rules; intentionally unused today
    if not message:
        return DEFAULT_INTENT, DEFAULT_CONFIDENCE
    lowered = message.lower()
    for intent, confidence, keywords in INTENT_RULES:
        for kw in keywords:
            if kw in lowered:
                return intent, confidence
    return DEFAULT_INTENT, DEFAULT_CONFIDENCE


# ---------------------------------------------------------------------------
# Product relation graph (order-item co-occurrence)
# ---------------------------------------------------------------------------


def get_product_relations(session: Session, sku: str, top_n: int = 5) -> list[dict[str, Any]]:
    """Return related products ranked by order co-occurrence with ``sku``.

    Two products are related when they appear as separate ``OrderItem`` rows
    in the same ``Order``. The returned list is sorted by co-occurrence count
    descending (ties broken by SKU asc for determinism) and trimmed to
    ``top_n``. ``weight`` is the co-occurrence count divided by the total
    number of orders — a global support denominator that normalises raw
    counts so a popular pair in a small catalogue does not dominate.

    Returns an empty list when ``top_n <= 0``, when no orders exist, or when
    ``sku`` has no co-occurring products.
    """
    if top_n <= 0:
        return []

    total_orders = session.query(func.count(Order.id)).scalar() or 0
    if total_orders == 0:
        return []

    # Orders that contain the source SKU. Use a select() (not subquery()) so
    # SQLAlchemy 2.0 accepts it directly in .in_() without a coercion warning.
    source_order_ids = (
        select(OrderItem.order_id).where(OrderItem.sku == sku).distinct()
    )

    co_count = func.count(OrderItem.order_id.distinct()).label("co_count")
    rows = (
        session.query(
            OrderItem.sku.label("sku"),
            Product.name.label("name"),
            co_count,
        )
        .join(Product, Product.sku == OrderItem.sku)
        .filter(
            OrderItem.order_id.in_(source_order_ids),
            OrderItem.sku != sku,
        )
        .group_by(OrderItem.sku, Product.name)
        .order_by(desc(co_count), OrderItem.sku)
        .limit(top_n)
        .all()
    )

    return [
        {
            "sku": row.sku,
            "name": row.name,
            "weight": row.co_count / total_orders,
            "co_occurrence_count": int(row.co_count),
        }
        for row in rows
    ]


# ---------------------------------------------------------------------------
# Opportunity scoring
# ---------------------------------------------------------------------------

# Value-tier boost (additive, per SubTask 3.3).
VALUE_TIER_BOOST: dict[str, float] = {
    "vip": 0.2,
    "high": 0.15,
    "medium": 0.05,
    "low": 0.0,
}

INTENT_CONFIDENCE_WEIGHT = 0.2
CATEGORY_MATCH_BONUS = 0.1


def score_opportunity(
    user_value_tier: str,
    relation_weight: float,
    intent_confidence: float,
    relation_category_match: bool = True,
) -> float:
    """Score an opportunity in the closed interval ``[0, 1]``.

    Composition (per SubTask 3.3):
    - Base: ``relation_weight`` (order co-occurrence support, expected 0-1).
    - Value-tier boost: vip +0.2 / high +0.15 / medium +0.05 / low +0.
    - Intent boost: ``intent_confidence * 0.2``.
    - Category-match boost: +0.1 when target shares the source's category
      (e.g. after-sales accessory in the same category as the original
      product).
    - Clamped to ``[0, 1]``.

    Unknown tiers fall back to the ``low`` boost (0.0) so a malformed profile
    never inflates a score.
    """
    base = float(relation_weight)
    tier_boost = VALUE_TIER_BOOST.get(user_value_tier, 0.0)
    intent_boost = float(intent_confidence) * INTENT_CONFIDENCE_WEIGHT
    category_bonus = CATEGORY_MATCH_BONUS if relation_category_match else 0.0
    score = base + tier_boost + intent_boost + category_bonus
    return max(0.0, min(1.0, score))


# ---------------------------------------------------------------------------
# Demand mining entry point
# ---------------------------------------------------------------------------

# Up-sell base weight when no co-occurrence signal is available. Same-category
# higher-priced candidates carry a weak but non-zero relation; a candidate
# that also co-occurs with the source SKU overrides this with its actual
# co-occurrence weight (looked up via get_product_relations).
UP_SELL_FALLBACK_WEIGHT = 0.3
UP_SELL_TOP_N = 3
CROSS_SELL_TOP_N = 3
# Look-up window large enough to cover any same-category co-occurring
# candidate when computing up-sell relation weights.
RELATION_LOOKUP_FOR_UP_SELL = 20


def mine_demand(
    session: Session,
    user_id: str,
    conversation_context: dict[str, Any],
) -> dict[str, Any]:
    """Mine demand from a conversation: intent + opportunities + confidence.

    ``conversation_context`` keys:
    - ``message`` (str): latest customer message.
    - ``history`` (list[dict], optional): prior turns; reserved for future
      enrichment, currently unused.
    - ``order_id`` (str | None): order the conversation is anchored to.
    - ``mentioned_skus`` (list[str]): SKUs the customer mentioned; the
      primary driver of opportunity generation.

    Returns ``{intent, intent_confidence, opportunities}`` where each
    opportunity is ``{type, target_sku, target_name, opportunity_score,
    reason}``. ``type`` is ``"cross_sell"`` or ``"up_sell"``. Opportunities
    are sorted by ``opportunity_score`` descending (ties broken by
    ``target_sku`` asc) for stable output.

    If the user has no ``UserProfile`` (``get_profile`` returns ``None``),
    ``user_value_tier`` falls back to ``"low"`` so the function never blocks
    on missing profile data. Intents outside ``OPPORTUNITY_INTENTS``
    (logistics, complaint, general) yield an empty opportunities list.
    """
    message = str(conversation_context.get("message") or "")
    mentioned_skus: list[str] = list(conversation_context.get("mentioned_skus") or [])
    order_id = conversation_context.get("order_id")

    intent, confidence = classify_intent(message, order_id)

    user_value_tier = _resolve_value_tier(session, user_id)

    opportunities: list[dict[str, Any]] = []
    if intent in OPPORTUNITY_INTENTS and mentioned_skus:
        opportunities = _build_opportunities(
            session,
            mentioned_skus=mentioned_skus,
            intent=intent,
            intent_confidence=confidence,
            user_value_tier=user_value_tier,
        )

    return {
        "intent": intent,
        "intent_confidence": confidence,
        "opportunities": opportunities,
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _resolve_value_tier(session: Session, user_id: str) -> str:
    """Return the user's value tier, defaulting to ``"low"`` when no profile."""
    profile = ups.get_profile(session, user_id)
    if profile is None:
        return "low"
    value = profile.get("value") or {}
    tier = value.get("tier") or "low"
    return tier if tier in VALUE_TIER_BOOST else "low"


def _build_opportunities(
    session: Session,
    *,
    mentioned_skus: list[str],
    intent: str,
    intent_confidence: float,
    user_value_tier: str,
) -> list[dict[str, Any]]:
    """Generate cross-sell and up-sell opportunities for mentioned SKUs."""
    opportunities: list[dict[str, Any]] = []
    seen_targets: set[str] = set()

    for sku in mentioned_skus:
        product = session.query(Product).filter_by(sku=sku).one_or_none()
        if product is None:
            continue

        if intent in ("intent:after_sales_return", "intent:product_inquiry"):
            opportunities.extend(
                _cross_sell_opportunities(
                    session,
                    source=product,
                    intent_confidence=intent_confidence,
                    user_value_tier=user_value_tier,
                    seen_targets=seen_targets,
                )
            )

        if intent in ("intent:product_inquiry", "intent:upgrade_inquiry"):
            opportunities.extend(
                _up_sell_opportunities(
                    session,
                    source=product,
                    intent_confidence=intent_confidence,
                    user_value_tier=user_value_tier,
                    seen_targets=seen_targets,
                )
            )

    # Sort by score desc (ties broken by target_sku asc) for deterministic
    # output across DB engines and test runs.
    opportunities.sort(key=lambda o: (-o["opportunity_score"], o["target_sku"]))
    return opportunities


def _cross_sell_opportunities(
    session: Session,
    *,
    source: Product,
    intent_confidence: float,
    user_value_tier: str,
    seen_targets: set[str],
) -> list[dict[str, Any]]:
    """Build cross-sell opportunities from order co-occurrence."""
    results: list[dict[str, Any]] = []
    relations = get_product_relations(session, source.sku, top_n=CROSS_SELL_TOP_N)
    for rel in relations:
        if rel["sku"] in seen_targets:
            continue
        rel_product = session.query(Product).filter_by(sku=rel["sku"]).one_or_none()
        category_match = (
            rel_product is not None and rel_product.category == source.category
        )
        score = score_opportunity(
            user_value_tier=user_value_tier,
            relation_weight=rel["weight"],
            intent_confidence=intent_confidence,
            relation_category_match=category_match,
        )
        results.append(
            {
                "type": "cross_sell",
                "target_sku": rel["sku"],
                "target_name": rel["name"],
                "opportunity_score": score,
                "reason": (
                    f"商品 {source.name} 与 {rel['name']} 在 "
                    f"{rel['co_occurrence_count']} 单中共现"
                ),
            }
        )
        seen_targets.add(rel["sku"])
    return results


def _up_sell_opportunities(
    session: Session,
    *,
    source: Product,
    intent_confidence: float,
    user_value_tier: str,
    seen_targets: set[str],
) -> list[dict[str, Any]]:
    """Build up-sell opportunities from same-category higher-priced products."""
    results: list[dict[str, Any]] = []
    candidates = (
        session.query(Product)
        .filter(
            Product.category == source.category,
            Product.sku != source.sku,
            Product.unit_price > source.unit_price,
        )
        .order_by(Product.unit_price.asc())
        .limit(UP_SELL_TOP_N)
        .all()
    )
    # Pre-fetch co-occurrence weights so an up-sell candidate that also
    # co-occurs with the source SKU scores higher than a same-category-only
    # candidate. Falls back to UP_SELL_FALLBACK_WEIGHT when no co-occurrence.
    relation_by_sku = {
        rel["sku"]: rel
        for rel in get_product_relations(session, source.sku, top_n=RELATION_LOOKUP_FOR_UP_SELL)
    }
    for cand in candidates:
        if cand.sku in seen_targets:
            continue
        rel = relation_by_sku.get(cand.sku)
        relation_weight = rel["weight"] if rel is not None else UP_SELL_FALLBACK_WEIGHT
        score = score_opportunity(
            user_value_tier=user_value_tier,
            relation_weight=relation_weight,
            intent_confidence=intent_confidence,
            relation_category_match=True,
        )
        results.append(
            {
                "type": "up_sell",
                "target_sku": cand.sku,
                "target_name": cand.name,
                "opportunity_score": score,
                "reason": (
                    f"{cand.name} 与 {source.name} 同属 {source.category} 类目，"
                    f"价格由 {source.unit_price:.2f} 升至 {cand.unit_price:.2f}"
                ),
            }
        )
        seen_targets.add(cand.sku)
    return results
