"""Ombudsman complaints: draft context, and the filing details on the case.

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "drafts",
        sa.Column("context", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "cases", sa.Column("ombudsman_filed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("cases", sa.Column("ombudsman_ref", sa.String(length=40), nullable=True))


def downgrade() -> None:
    op.drop_column("cases", "ombudsman_ref")
    op.drop_column("cases", "ombudsman_filed_at")
    op.drop_column("drafts", "context")
