"""add_user_ldap_dn

Revision ID: 0002
Revises: 0001
Create Date: 2026-04-20 18:17:11.739746

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0002'
down_revision: Union[str, Sequence[str], None] = '0001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('user', sa.Column('ldap_dn', sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column('user', 'ldap_dn')
