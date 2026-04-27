"""Add user_tenure table for employment period tracking.

Revision ID: 0007
Revises: 0006
"""
from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_tenure",
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(100), nullable=False),
        sa.Column("employer", sa.String(200), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("notes", sa.String(1000), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index("ix_user_tenure_user_id", "user_tenure", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_user_tenure_user_id", table_name="user_tenure")
    op.drop_table("user_tenure")
