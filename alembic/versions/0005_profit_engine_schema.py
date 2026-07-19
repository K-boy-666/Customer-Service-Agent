"""add cs profit engine schema (user profile, recommendation, funnel, attribution)

Revision ID: 0005_profit_engine_schema
Revises: 0004_sequence_counters
Create Date: 2026-07-19

Adds nine tables backing the AI-driven customer-service profit engine:
- user_profile / user_identity / user_intent_tag / user_value_score
- recommendation / funnel_event
- touch_point / attribution_record / agent_assist_event

The migration uses only ``op.create_table`` + ``op.create_index`` with
``sa.Column`` declarations that are portable between SQLite and MySQL.
No dialect-specific DDL is emitted.
"""

import sqlalchemy as sa
from alembic import op

revision = "0005_profit_engine_schema"
down_revision = "0004_sequence_counters"
branch_labels = None
depends_on = None


# Tables that must exist before this migration runs. customers and orders are
# created in 0001_initial_schema and are always present at this point.
_NEW_TABLES = (
    "user_profile",
    "user_identity",
    "user_intent_tag",
    "user_value_score",
    "recommendation",
    "funnel_event",
    "touch_point",
    "attribution_record",
    "agent_assist_event",
)


def upgrade() -> None:
    # 1. user_profile ---------------------------------------------------------
    op.create_table(
        "user_profile",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.String(length=80), nullable=False),
        sa.Column("primary_customer_id", sa.Integer(), sa.ForeignKey("customers.id"), nullable=True),
        sa.Column("display_name", sa.String(length=120), nullable=False, default=""),
        sa.Column("aggregated_attrs", sa.JSON(), nullable=False, default=dict),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("user_id", name="uq_user_profile_user_id"),
    )
    op.create_index("ix_user_profile_user_id", "user_profile", ["user_id"], unique=True)
    op.create_index("ix_user_profile_primary_customer_id", "user_profile", ["primary_customer_id"])

    # 2. user_identity --------------------------------------------------------
    op.create_table(
        "user_identity",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.String(length=80),
            sa.ForeignKey("user_profile.user_id"),
            nullable=False,
        ),
        sa.Column("platform", sa.String(length=40), nullable=False),
        sa.Column("identity_type", sa.String(length=40), nullable=False),
        sa.Column("identity_value", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "platform", "identity_type", "identity_value", name="uq_user_identity_platform_value"
        ),
    )
    op.create_index("ix_user_identity_user_id", "user_identity", ["user_id"])

    # 3. user_intent_tag ------------------------------------------------------
    op.create_table(
        "user_intent_tag",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.String(length=80),
            sa.ForeignKey("user_profile.user_id"),
            nullable=False,
        ),
        sa.Column("tag", sa.String(length=120), nullable=False),
        sa.Column("source", sa.String(length=40), nullable=False, default="conversation"),
        sa.Column("confidence", sa.Float(), nullable=False, default=0.0),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_user_intent_tag_user_id", "user_intent_tag", ["user_id"])
    op.create_index("ix_user_intent_tag_created_at", "user_intent_tag", ["created_at"])

    # 4. user_value_score -----------------------------------------------------
    op.create_table(
        "user_value_score",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.String(length=80),
            sa.ForeignKey("user_profile.user_id"),
            nullable=False,
        ),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("tier", sa.String(length=20), nullable=False),
        sa.Column("rfm_r", sa.Float(), nullable=False, default=0.0),
        sa.Column("rfm_f", sa.Float(), nullable=False, default=0.0),
        sa.Column("rfm_m", sa.Float(), nullable=False, default=0.0),
        sa.Column("interaction_weight", sa.Float(), nullable=False, default=0.0),
        sa.Column("computed_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_user_value_score_user_id", "user_value_score", ["user_id"])
    op.create_index("ix_user_value_score_computed_at", "user_value_score", ["computed_at"])

    # 5. recommendation -------------------------------------------------------
    op.create_table(
        "recommendation",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("recommendation_id", sa.String(length=80), nullable=False),
        sa.Column(
            "user_id",
            sa.String(length=80),
            sa.ForeignKey("user_profile.user_id"),
            nullable=False,
        ),
        sa.Column("conversation_id", sa.String(length=120), nullable=False),
        sa.Column("recommend_type", sa.String(length=40), nullable=False),
        sa.Column("target_ref", sa.String(length=120), nullable=False),
        sa.Column("content", sa.Text(), nullable=False, default=""),
        sa.Column("script", sa.Text(), nullable=False, default=""),
        sa.Column("expected_conversion_rate", sa.Float(), nullable=False, default=0.0),
        sa.Column("opportunity_score", sa.Float(), nullable=False, default=0.0),
        sa.Column("status", sa.String(length=40), nullable=False, default="pending"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("recommendation_id", name="uq_recommendation_recommendation_id"),
    )
    op.create_index(
        "ix_recommendation_recommendation_id", "recommendation", ["recommendation_id"], unique=True
    )
    op.create_index("ix_recommendation_user_id", "recommendation", ["user_id"])
    op.create_index("ix_recommendation_conversation_id", "recommendation", ["conversation_id"])

    # 6. funnel_event ---------------------------------------------------------
    op.create_table(
        "funnel_event",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "recommendation_id",
            sa.String(length=80),
            sa.ForeignKey("recommendation.recommendation_id"),
            nullable=False,
        ),
        sa.Column("user_id", sa.String(length=80), nullable=False),
        sa.Column("session_id", sa.String(length=120), nullable=False),
        sa.Column("event_type", sa.String(length=40), nullable=False),
        sa.Column("order_id", sa.String(length=40), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False, default=dict),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_funnel_event_recommendation_id", "funnel_event", ["recommendation_id"])
    op.create_index("ix_funnel_event_user_id", "funnel_event", ["user_id"])
    op.create_index("ix_funnel_event_session_id", "funnel_event", ["session_id"])
    op.create_index("ix_funnel_event_order_id", "funnel_event", ["order_id"])
    op.create_index("ix_funnel_event_created_at", "funnel_event", ["created_at"])

    # 7. touch_point ----------------------------------------------------------
    op.create_table(
        "touch_point",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.String(length=80), nullable=False),
        sa.Column("conversation_id", sa.String(length=120), nullable=False),
        sa.Column("agent_id", sa.String(length=80), nullable=False),
        sa.Column("recommendation_id", sa.String(length=80), nullable=True),
        sa.Column("touch_time", sa.DateTime(), nullable=False),
        sa.Column("touch_type", sa.String(length=40), nullable=False, default="conversation"),
    )
    op.create_index("ix_touch_point_user_id", "touch_point", ["user_id"])
    op.create_index("ix_touch_point_conversation_id", "touch_point", ["conversation_id"])
    op.create_index("ix_touch_point_recommendation_id", "touch_point", ["recommendation_id"])
    op.create_index("ix_touch_point_touch_time", "touch_point", ["touch_time"])

    # 8. attribution_record ---------------------------------------------------
    op.create_table(
        "attribution_record",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("attribution_id", sa.String(length=80), nullable=False),
        sa.Column(
            "order_id", sa.String(length=40), sa.ForeignKey("orders.id"), nullable=False
        ),
        sa.Column("user_id", sa.String(length=80), nullable=False),
        sa.Column("conversation_id", sa.String(length=120), nullable=True),
        sa.Column("agent_id", sa.String(length=80), nullable=True),
        sa.Column("recommendation_id", sa.String(length=80), nullable=True),
        sa.Column("model", sa.String(length=40), nullable=False),
        sa.Column("attributed_amount", sa.Float(), nullable=False),
        sa.Column("total_order_amount", sa.Float(), nullable=False),
        sa.Column("weight", sa.Float(), nullable=False, default=1.0),
        sa.Column("attributed_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("attribution_id", name="uq_attribution_record_attribution_id"),
    )
    op.create_index(
        "ix_attribution_record_attribution_id",
        "attribution_record",
        ["attribution_id"],
        unique=True,
    )
    op.create_index("ix_attribution_record_order_id", "attribution_record", ["order_id"])
    op.create_index("ix_attribution_record_user_id", "attribution_record", ["user_id"])
    op.create_index("ix_attribution_record_conversation_id", "attribution_record", ["conversation_id"])
    op.create_index(
        "ix_attribution_record_recommendation_id", "attribution_record", ["recommendation_id"]
    )
    op.create_index("ix_attribution_record_attributed_at", "attribution_record", ["attributed_at"])

    # 9. agent_assist_event ---------------------------------------------------
    op.create_table(
        "agent_assist_event",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("conversation_id", sa.String(length=120), nullable=False),
        sa.Column("agent_id", sa.String(length=80), nullable=False),
        sa.Column("assist_type", sa.String(length=40), nullable=False),
        sa.Column("content", sa.Text(), nullable=False, default=""),
        sa.Column("adopted", sa.Integer(), nullable=False, default=0),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_agent_assist_event_conversation_id", "agent_assist_event", ["conversation_id"])
    op.create_index("ix_agent_assist_event_created_at", "agent_assist_event", ["created_at"])


def downgrade() -> None:
    # Drop in reverse dependency order. Dropping a table cascades to its
    # indexes, so explicit index drops are not required.
    for table in reversed(_NEW_TABLES):
        op.drop_table(table)
