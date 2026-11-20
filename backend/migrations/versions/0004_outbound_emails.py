"""Outbound emails, and the bank a case's complaint goes to.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("cases", sa.Column("bank_id", sa.String(length=32), nullable=True))
    op.create_table(
        "outbound_emails",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("draft_id", sa.Uuid(), nullable=False),
        sa.Column("channel", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("sandbox", sa.Boolean(), nullable=False),
        sa.Column("bank_id", sa.String(length=32), nullable=True),
        sa.Column("recipient", sa.String(length=320), nullable=True),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("blocked_reasons", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("message_id", sa.String(length=128), nullable=True),
        sa.Column("thread_id", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.id"],
            name=op.f("fk_outbound_emails_case_id_cases"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["draft_id"],
            ["drafts.id"],
            name=op.f("fk_outbound_emails_draft_id_drafts"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_outbound_emails")),
        sa.UniqueConstraint("draft_id", name=op.f("uq_outbound_emails_draft_id")),
    )
    op.create_index(op.f("ix_outbound_emails_case_id"), "outbound_emails", ["case_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_outbound_emails_case_id"), table_name="outbound_emails")
    op.drop_table("outbound_emails")
    op.drop_column("cases", "bank_id")
