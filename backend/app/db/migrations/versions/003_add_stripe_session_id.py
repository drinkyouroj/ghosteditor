"""Add stripe_session_id to manuscripts for idempotent webhook handling.

Per DECISION_006 JUDGE Amendment 1: webhook handler checks stripe_session_id
to prevent double-processing of checkout.session.completed events.

Revision ID: 003
Revises: 002
Create Date: 2026-03-13
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("manuscripts", sa.Column("stripe_session_id", sa.Text, nullable=True))


def downgrade() -> None:
    op.drop_column("manuscripts", "stripe_session_id")
