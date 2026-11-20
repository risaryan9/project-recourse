"""Anonymous case ownership: the hash of the owner token that created the case.

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("cases", sa.Column("owner_id", sa.String(length=64), nullable=True))
    op.create_index(op.f("ix_cases_owner_id"), "cases", ["owner_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_cases_owner_id"), table_name="cases")
    op.drop_column("cases", "owner_id")
