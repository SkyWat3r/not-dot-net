"""Add User.last_ad_logon (UTC datetime, populated from AD lastLogonTimestamp).

Revision ID: 0011
Revises: 0010
"""
from alembic import op
import sqlalchemy as sa


revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user",
        sa.Column("last_ad_logon", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("user", "last_ad_logon")
