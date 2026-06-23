"""initial schema

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-06-24
"""

from alembic import op

from models import Base

revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(op.get_bind())


def downgrade() -> None:
    Base.metadata.drop_all(op.get_bind())
