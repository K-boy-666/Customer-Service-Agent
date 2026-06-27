"""initial schema

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-06-24
"""

from alembic import op
import sqlalchemy as sa


revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "customers",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("phone", sa.String(length=40), nullable=False),
        sa.Column("membership_tier", sa.String(length=30), nullable=False),
        sa.Column("points", sa.Integer(), nullable=False),
        sa.Column("joined_at", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("email"),
    )
    op.create_index("ix_customers_email", "customers", ["email"])

    op.create_table(
        "products",
        sa.Column("sku", sa.String(length=80), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("category", sa.String(length=120), nullable=False),
        sa.Column("unit_price", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "orders",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("order_number", sa.String(length=40), nullable=False),
        sa.Column("customer_id", sa.Integer(), sa.ForeignKey("customers.id"), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("total_amount", sa.Float(), nullable=False),
        sa.Column("currency", sa.String(length=10), nullable=False),
        sa.Column("shipping_address", sa.Text(), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.Column("updated_at", sa.String(length=40), nullable=False),
        sa.UniqueConstraint("order_number"),
    )
    op.create_index("ix_orders_customer_id", "orders", ["customer_id"])
    op.create_index("ix_orders_status", "orders", ["status"])
    op.create_index("ix_orders_created_at", "orders", ["created_at"])

    op.create_table(
        "order_items",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("order_id", sa.String(length=40), sa.ForeignKey("orders.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sku", sa.String(length=80), sa.ForeignKey("products.sku"), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("qty", sa.Integer(), nullable=False),
        sa.Column("price", sa.Float(), nullable=False),
    )
    op.create_index("ix_order_items_order_id", "order_items", ["order_id"])

    op.create_table(
        "shipments",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("order_id", sa.String(length=40), sa.ForeignKey("orders.id"), nullable=False),
        sa.Column("carrier", sa.String(length=120), nullable=False),
        sa.Column("tracking_number", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("estimated_delivery", sa.String(length=40), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("order_id"),
    )
    op.create_index("ix_shipments_order_id", "shipments", ["order_id"])
    op.create_index("ix_shipments_tracking_number", "shipments", ["tracking_number"])

    op.create_table(
        "shipment_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("shipment_id", sa.Integer(), sa.ForeignKey("shipments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("location", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("event_time", sa.String(length=40), nullable=False),
    )
    op.create_index("ix_shipment_events_shipment_id", "shipment_events", ["shipment_id"])

    op.create_table(
        "tickets",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("ticket_number", sa.String(length=80), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("type", sa.String(length=50), nullable=False),
        sa.Column("priority", sa.String(length=10), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("customer_id", sa.Integer(), sa.ForeignKey("customers.id"), nullable=True),
        sa.Column("order_id", sa.String(length=40), sa.ForeignKey("orders.id"), nullable=True),
        sa.Column("assignee", sa.String(length=120), nullable=False),
        sa.Column("department", sa.String(length=120), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("ticket_number"),
    )
    op.create_index("ix_tickets_status", "tickets", ["status"])
    op.create_index("ix_tickets_customer_id", "tickets", ["customer_id"])
    op.create_index("ix_tickets_order_id", "tickets", ["order_id"])

    op.create_table(
        "ticket_notes",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("ticket_id", sa.Integer(), sa.ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("author", sa.String(length=120), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_ticket_notes_ticket_id", "ticket_notes", ["ticket_id"])

    op.create_table(
        "returns",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("return_number", sa.String(length=80), nullable=False),
        sa.Column("order_id", sa.String(length=40), sa.ForeignKey("orders.id"), nullable=False),
        sa.Column("customer_id", sa.Integer(), sa.ForeignKey("customers.id"), nullable=True),
        sa.Column("type", sa.String(length=30), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("refund_amount", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("return_number"),
    )
    op.create_index("ix_returns_order_id", "returns", ["order_id"])
    op.create_index("ix_returns_customer_id", "returns", ["customer_id"])
    op.create_index("ix_returns_status", "returns", ["status"])

    op.create_table(
        "satisfaction_surveys",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("survey_number", sa.String(length=80), nullable=False),
        sa.Column("customer_id", sa.Integer(), sa.ForeignKey("customers.id"), nullable=True),
        sa.Column("order_id", sa.String(length=40), sa.ForeignKey("orders.id"), nullable=True),
        sa.Column("rating", sa.Integer(), nullable=False),
        sa.Column("feedback_text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("survey_number"),
    )
    op.create_index("ix_satisfaction_surveys_customer_id", "satisfaction_surveys", ["customer_id"])
    op.create_index("ix_satisfaction_surveys_order_id", "satisfaction_surveys", ["order_id"])

    op.create_table(
        "audit_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("actor_subject", sa.String(length=255), nullable=False),
        sa.Column("actor_role", sa.String(length=80), nullable=False),
        sa.Column("permission", sa.String(length=120), nullable=False),
        sa.Column("endpoint", sa.String(length=255), nullable=False),
        sa.Column("resource_type", sa.String(length=80), nullable=False),
        sa.Column("resource_id", sa.String(length=120), nullable=False),
        sa.Column("before_summary", sa.JSON(), nullable=True),
        sa.Column("after_summary", sa.JSON(), nullable=True),
        sa.Column("request_id", sa.String(length=120), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("verification_id", sa.String(length=255), nullable=False),
        sa.Column("result", sa.String(length=40), nullable=False),
        sa.Column("failure_reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_audit_events_created_at", "audit_events", ["created_at"])

    op.create_table(
        "idempotency_keys",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("key", sa.String(length=255), nullable=False),
        sa.Column("actor_subject", sa.String(length=255), nullable=False),
        sa.Column("endpoint", sa.String(length=255), nullable=False),
        sa.Column("request_hash", sa.String(length=128), nullable=False),
        sa.Column("response_json", sa.JSON(), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("actor_subject", "endpoint", "key", name="uq_idem_actor_endpoint_key"),
    )

    op.create_table(
        "otp_challenges",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("challenge_id", sa.String(length=120), nullable=False),
        sa.Column("purpose", sa.String(length=80), nullable=False),
        sa.Column("customer_id", sa.Integer(), sa.ForeignKey("customers.id"), nullable=True),
        sa.Column("order_id", sa.String(length=40), sa.ForeignKey("orders.id"), nullable=True),
        sa.Column("channel", sa.String(length=40), nullable=False),
        sa.Column("destination", sa.String(length=255), nullable=False),
        sa.Column("code_hash", sa.String(length=128), nullable=False),
        sa.Column("verification_token", sa.String(length=255), nullable=False),
        sa.Column("verified_at", sa.DateTime(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("challenge_id"),
    )
    op.create_index("ix_otp_challenges_customer_id", "otp_challenges", ["customer_id"])
    op.create_index("ix_otp_challenges_order_id", "otp_challenges", ["order_id"])


def downgrade() -> None:
    for table in (
        "otp_challenges",
        "idempotency_keys",
        "audit_events",
        "satisfaction_surveys",
        "returns",
        "ticket_notes",
        "tickets",
        "shipment_events",
        "shipments",
        "order_items",
        "orders",
        "products",
        "customers",
    ):
        op.drop_table(table)
