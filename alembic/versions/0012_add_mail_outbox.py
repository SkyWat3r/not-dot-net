"""Add mail_outbox table for durable retryable mail sending.

Revision ID: 0012
Revises: 0011
"""
from alembic import op
import sqlalchemy as sa


revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mail_outbox",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("to_address", sa.String(255), nullable=False),
        sa.Column("subject", sa.String(500), nullable=False),
        sa.Column("body_html", sa.Text(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
        sa.Column("failed_at", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
    )
    op.create_index("ix_mail_outbox_created_at", "mail_outbox", ["created_at"])
    op.create_index(
        "ix_mail_outbox_pending",
        "mail_outbox",
        ["sent_at", "failed_at", "next_attempt_at"],
    )
    op.create_index("ix_mail_outbox_failed_at", "mail_outbox", ["failed_at"])


def downgrade() -> None:
    op.drop_index("ix_mail_outbox_failed_at", table_name="mail_outbox")
    op.drop_index("ix_mail_outbox_pending", table_name="mail_outbox")
    op.drop_index("ix_mail_outbox_created_at", table_name="mail_outbox")
    op.drop_table("mail_outbox")
