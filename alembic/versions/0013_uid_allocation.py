"""Add uid_allocation table for centralized UID management.

Revision ID: 0013
Revises: 0012
"""
from alembic import op
import sqlalchemy as sa


revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "uid_allocation",
        sa.Column("uid", sa.Integer(), primary_key=True),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
        sa.Column("sam_account", sa.String(64), nullable=True),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("note", sa.String(255), nullable=True),
    )
    op.create_index("ix_uid_allocation_acquired_at", "uid_allocation", ["acquired_at"])
    op.create_index("ix_uid_allocation_user_id", "uid_allocation", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_uid_allocation_user_id", table_name="uid_allocation")
    op.drop_index("ix_uid_allocation_acquired_at", table_name="uid_allocation")
    op.drop_table("uid_allocation")
