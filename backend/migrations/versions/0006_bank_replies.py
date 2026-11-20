"""Bank replies (judged by ReplyJudgeGraph) and the bank's complaint reference.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("cases", sa.Column("bank_complaint_ref", sa.String(length=40), nullable=True))
    op.create_table(
        "bank_replies",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sender", sa.String(length=320), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("redactions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("verdict", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("label", sa.String(length=32), nullable=True),
        sa.Column("next_action", sa.String(length=32), nullable=True),
        sa.Column("suspicious", sa.Boolean(), nullable=True),
        sa.Column("unsatisfactory", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.id"],
            name=op.f("fk_bank_replies_case_id_cases"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_bank_replies")),
    )
    op.create_index(op.f("ix_bank_replies_case_id"), "bank_replies", ["case_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_bank_replies_case_id"), table_name="bank_replies")
    op.drop_table("bank_replies")
    op.drop_column("cases", "bank_complaint_ref")
