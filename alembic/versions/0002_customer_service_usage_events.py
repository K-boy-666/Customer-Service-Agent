"""add customer service usage events

Revision ID: 0002_usage_events
Revises: 0001_initial_schema
Create Date: 2026-06-24
"""

from alembic import op
from sqlalchemy import Column, DateTime, Integer, JSON, MetaData, String, Table, Text

revision = "0002_usage_events"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def _table() -> Table:
    return Table(
        "customer_service_usage_events",
        MetaData(),
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("conversation_id", String(120), nullable=False, index=True),
        Column("customer_id", Integer, nullable=True, index=True),
        Column("order_id", String(40), nullable=True, index=True),
        Column("status", String(40), nullable=False, index=True),
        Column("emotional_level", String(20), nullable=False, default=""),
        Column("message_length", Integer, nullable=False, default=0),
        Column("intents", JSON, nullable=False, default=list),
        Column("dispatched_agents", JSON, nullable=False, default=list),
        Column("tool_calls", JSON, nullable=False, default=list),
        Column("needs_human", Integer, nullable=False, default=0),
        Column("failure_reason", Text, nullable=False, default=""),
        Column("created_at", DateTime, nullable=False, index=True),
    )


def upgrade() -> None:
    _table().create(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    _table().drop(op.get_bind(), checkfirst=True)
