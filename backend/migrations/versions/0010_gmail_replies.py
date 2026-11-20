"""Gmail sending and the reply watcher: processed message IDs on bank replies.

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "bank_replies", sa.Column("gmail_message_id", sa.String(length=64), nullable=True)
    )
    op.create_unique_constraint(
        op.f("uq_bank_replies_gmail_message_id"), "bank_replies", ["gmail_message_id"]
    )


def downgrade() -> None:
    op.drop_constraint(op.f("uq_bank_replies_gmail_message_id"), "bank_replies", type_="unique")
    op.drop_column("bank_replies", "gmail_message_id")
