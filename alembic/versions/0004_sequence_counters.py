"""add sequence counters table

Revision ID: 0004_sequence_counters
Revises: 0003_conversation_states
Create Date: 2026-06-30
"""

from alembic import op
import sqlalchemy as sa


revision = "0004_sequence_counters"
down_revision = "0003_conversation_states"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sequence_counters",
        sa.Column("prefix_name", sa.String(length=10), nullable=False),
        sa.Column("counter_date", sa.String(length=8), nullable=False),
        sa.Column("last_value", sa.Integer(), nullable=False, default=0),
        sa.UniqueConstraint("prefix_name", "counter_date", name="uq_seq_prefix_date"),
    )


def downgrade() -> None:
    op.drop_table("sequence_counters")
