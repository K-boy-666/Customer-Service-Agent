"""add durable conversation state

Revision ID: 0003_conversation_states
Revises: 0002_usage_events
Create Date: 2026-06-27
"""

from alembic import op
import sqlalchemy as sa


revision = "0003_conversation_states"
down_revision = "0002_usage_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "conversation_states",
        sa.Column("conversation_id", sa.String(length=120), primary_key=True),
        sa.Column("customer_id", sa.Integer(), nullable=True),
        sa.Column("order_id", sa.String(length=40), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_conversation_states_customer_id", "conversation_states", ["customer_id"])
    op.create_index("ix_conversation_states_order_id", "conversation_states", ["order_id"])


def downgrade() -> None:
    op.drop_index("ix_conversation_states_order_id", table_name="conversation_states")
    op.drop_index("ix_conversation_states_customer_id", table_name="conversation_states")
    op.drop_table("conversation_states")
