"""catalog sync fields

Revision ID: c2b7a1e9f4d6
Revises: 961d9803f9d0
Create Date: 2026-11-05

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c2b7a1e9f4d6'
down_revision: Union[str, Sequence[str], None] = '961d9803f9d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Ajoute l'état de disponibilité et la source fichier du catalogue."""
    op.add_column(
        'skins',
        sa.Column('available', sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column('skins', sa.Column('source', sa.String(), nullable=True))
    op.add_column(
        'skins',
        sa.Column(
            'updated_at',
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index('ix_skins_available', 'skins', ['available'])


def downgrade() -> None:
    """Retire les champs de synchronisation du catalogue."""
    op.drop_index('ix_skins_available', table_name='skins')
    op.drop_column('skins', 'updated_at')
    op.drop_column('skins', 'source')
    op.drop_column('skins', 'available')
