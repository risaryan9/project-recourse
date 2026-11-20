"""The bank's reply window: complaint date and the Ombudsman escalation assessment.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("cases", sa.Column("bank_complained_on", sa.Date(), nullable=True))
    op.add_column(
        "cases",
        sa.Column("escalation", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("cases", "escalation")
    op.drop_column("cases", "bank_complained_on")
