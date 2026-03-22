"""add_user_metadata

Revision ID: a1b2c3d4e5f6
Revises: dc2cbddabb63
Create Date: 2026-03-22 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = 'dc2cbddabb63'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'documents',
        sa.Column(
            'user_metadata',
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.create_index(
        'idx_documents_user_metadata',
        'documents',
        ['user_metadata'],
        postgresql_using='gin',
    )


def downgrade() -> None:
    op.drop_index('idx_documents_user_metadata', table_name='documents')
    op.drop_column('documents', 'user_metadata')
