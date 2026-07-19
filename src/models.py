"""SQLAlchemy ORM models for the customer-service platform."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    phone: Mapped[str] = mapped_column(String(40), nullable=False)
    membership_tier: Mapped[str] = mapped_column(String(30), nullable=False, default="standard")
    points: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    joined_at: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now, nullable=False)

    orders: Mapped[list[Order]] = relationship(back_populates="customer")


class Product(Base):
    __tablename__ = "products"

    sku: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str] = mapped_column(String(120), nullable=False)
    unit_price: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, nullable=False)


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    order_number: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending", index=True)
    total_amount: Mapped[float] = mapped_column(Float, nullable=False)
    currency: Mapped[str] = mapped_column(String(10), nullable=False, default="CNY")
    shipping_address: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    updated_at: Mapped[str] = mapped_column(String(40), nullable=False)

    customer: Mapped[Customer] = relationship(back_populates="orders")
    items: Mapped[list[OrderItem]] = relationship(back_populates="order", cascade="all, delete-orphan")
    shipment: Mapped[Shipment | None] = relationship(back_populates="order", cascade="all, delete-orphan")


class OrderItem(Base):
    __tablename__ = "order_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True)
    sku: Mapped[str] = mapped_column(ForeignKey("products.sku"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    qty: Mapped[int] = mapped_column(Integer, nullable=False)
    price: Mapped[float] = mapped_column(Float, nullable=False)

    order: Mapped[Order] = relationship(back_populates="items")


class Shipment(Base):
    __tablename__ = "shipments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), nullable=False, unique=True, index=True)
    carrier: Mapped[str] = mapped_column(String(120), nullable=False)
    tracking_number: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="pending")
    estimated_delivery: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now, nullable=False)

    order: Mapped[Order] = relationship(back_populates="shipment")
    events: Mapped[list[ShipmentEvent]] = relationship(back_populates="shipment", cascade="all, delete-orphan")


class ShipmentEvent(Base):
    __tablename__ = "shipment_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    shipment_id: Mapped[int] = mapped_column(ForeignKey("shipments.id", ondelete="CASCADE"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    location: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    event_time: Mapped[str] = mapped_column(String(40), nullable=False)

    shipment: Mapped[Shipment] = relationship(back_populates="events")


class Ticket(Base):
    __tablename__ = "tickets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticket_number: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    type: Mapped[str] = mapped_column(String(50), nullable=False, default="incident")
    priority: Mapped[str] = mapped_column(String(10), nullable=False, default="P3")
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="new", index=True)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id"), nullable=True, index=True)
    order_id: Mapped[str | None] = mapped_column(ForeignKey("orders.id"), nullable=True, index=True)
    assignee: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    department: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now, nullable=False)

    notes: Mapped[list[TicketNote]] = relationship(back_populates="ticket", cascade="all, delete-orphan")


class TicketNote(Base):
    __tablename__ = "ticket_notes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False, index=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    author: Mapped[str] = mapped_column(String(120), nullable=False, default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, nullable=False)

    ticket: Mapped[Ticket] = relationship(back_populates="notes")


class ReturnRequest(Base):
    __tablename__ = "returns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    return_number: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), nullable=False, index=True)
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id"), nullable=True, index=True)
    type: Mapped[str] = mapped_column(String(30), nullable=False, default="return")
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="pending", index=True)
    refund_amount: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now, nullable=False)


class SatisfactionSurvey(Base):
    __tablename__ = "satisfaction_surveys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    survey_number: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id"), nullable=True, index=True)
    order_id: Mapped[str | None] = mapped_column(ForeignKey("orders.id"), nullable=True, index=True)
    rating: Mapped[int] = mapped_column(Integer, nullable=False)
    feedback_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, nullable=False)


class CustomerServiceUsageEvent(Base):
    __tablename__ = "customer_service_usage_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    customer_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    order_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    emotional_level: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    message_length: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    intents: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    dispatched_agents: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    tool_calls: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    needs_human: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failure_reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, nullable=False, index=True)


class ConversationStateRecord(Base):
    __tablename__ = "conversation_states"

    conversation_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    customer_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    order_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now, nullable=False)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor_subject: Mapped[str] = mapped_column(String(255), nullable=False)
    actor_role: Mapped[str] = mapped_column(String(80), nullable=False)
    permission: Mapped[str] = mapped_column(String(120), nullable=False)
    endpoint: Mapped[str] = mapped_column(String(255), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(80), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    before_summary: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    after_summary: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    request_id: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    verification_id: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    result: Mapped[str] = mapped_column(String(40), nullable=False)
    failure_reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, nullable=False, index=True)


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"
    __table_args__ = (UniqueConstraint("actor_subject", "endpoint", "key", name="uq_idem_actor_endpoint_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(255), nullable=False)
    actor_subject: Mapped[str] = mapped_column(String(255), nullable=False)
    endpoint: Mapped[str] = mapped_column(String(255), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    response_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False, default=200)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, nullable=False)


class OtpChallenge(Base):
    __tablename__ = "otp_challenges"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    challenge_id: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    purpose: Mapped[str] = mapped_column(String(80), nullable=False)
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id"), nullable=True, index=True)
    order_id: Mapped[str | None] = mapped_column(ForeignKey("orders.id"), nullable=True, index=True)
    channel: Mapped[str] = mapped_column(String(40), nullable=False)
    destination: Mapped[str] = mapped_column(String(255), nullable=False)
    code_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    verification_token: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, nullable=False)


# ---------------------------------------------------------------------------
# CS Profit Engine — user profile, recommendation, funnel, attribution
# ---------------------------------------------------------------------------


class UserProfile(Base):
    __tablename__ = "user_profile"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(80), nullable=False, unique=True, index=True)
    primary_customer_id: Mapped[int | None] = mapped_column(
        ForeignKey("customers.id"), nullable=True, index=True
    )
    display_name: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    aggregated_attrs: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=now, onupdate=now, nullable=False
    )


class UserIdentity(Base):
    __tablename__ = "user_identity"
    __table_args__ = (
        UniqueConstraint(
            "platform", "identity_type", "identity_value", name="uq_user_identity_platform_value"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("user_profile.user_id"), nullable=False, index=True
    )
    platform: Mapped[str] = mapped_column(String(40), nullable=False)
    identity_type: Mapped[str] = mapped_column(String(40), nullable=False)
    identity_value: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, nullable=False)


class UserIntentTag(Base):
    __tablename__ = "user_intent_tag"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("user_profile.user_id"), nullable=False, index=True
    )
    tag: Mapped[str] = mapped_column(String(120), nullable=False)
    source: Mapped[str] = mapped_column(String(40), nullable=False, default="conversation")
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, nullable=False, index=True)


class UserValueScore(Base):
    __tablename__ = "user_value_score"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("user_profile.user_id"), nullable=False, index=True
    )
    score: Mapped[float] = mapped_column(Float, nullable=False)
    tier: Mapped[str] = mapped_column(String(20), nullable=False)
    rfm_r: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    rfm_f: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    rfm_m: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    interaction_weight: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    computed_at: Mapped[datetime] = mapped_column(DateTime, default=now, nullable=False, index=True)


class Recommendation(Base):
    __tablename__ = "recommendation"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    recommendation_id: Mapped[str] = mapped_column(
        String(80), nullable=False, unique=True, index=True
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("user_profile.user_id"), nullable=False, index=True
    )
    conversation_id: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    recommend_type: Mapped[str] = mapped_column(String(40), nullable=False)
    target_ref: Mapped[str] = mapped_column(String(120), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    script: Mapped[str] = mapped_column(Text, nullable=False, default="")
    expected_conversion_rate: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    opportunity_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=now, onupdate=now, nullable=False
    )


class FunnelEvent(Base):
    __tablename__ = "funnel_event"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    recommendation_id: Mapped[str] = mapped_column(
        ForeignKey("recommendation.recommendation_id"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    session_id: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    order_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, nullable=False, index=True)


class TouchPoint(Base):
    __tablename__ = "touch_point"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    conversation_id: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    agent_id: Mapped[str] = mapped_column(String(80), nullable=False)
    recommendation_id: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    touch_time: Mapped[datetime] = mapped_column(DateTime, default=now, nullable=False, index=True)
    touch_type: Mapped[str] = mapped_column(String(40), nullable=False, default="conversation")


class AttributionRecord(Base):
    __tablename__ = "attribution_record"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    attribution_id: Mapped[str] = mapped_column(
        String(80), nullable=False, unique=True, index=True
    )
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    conversation_id: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    agent_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    recommendation_id: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    model: Mapped[str] = mapped_column(String(40), nullable=False)
    attributed_amount: Mapped[float] = mapped_column(Float, nullable=False)
    total_order_amount: Mapped[float] = mapped_column(Float, nullable=False)
    weight: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    attributed_at: Mapped[datetime] = mapped_column(
        DateTime, default=now, nullable=False, index=True
    )


class AgentAssistEvent(Base):
    __tablename__ = "agent_assist_event"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    agent_id: Mapped[str] = mapped_column(String(80), nullable=False)
    assist_type: Mapped[str] = mapped_column(String(40), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    adopted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, nullable=False, index=True)
