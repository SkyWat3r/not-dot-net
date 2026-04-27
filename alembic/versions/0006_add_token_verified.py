"""Drop token_verified column from workflow_request (moved to browser storage).

Revision ID: 0006
Revises: 0005
"""
from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("workflow_request") as batch_op:
        batch_op.drop_column("token_verified")


def downgrade() -> None:
    with op.batch_alter_table("workflow_request") as batch_op:
        batch_op.add_column(
            sa.Column("token_verified", sa.Boolean(), server_default=sa.false(), nullable=False),
        )
