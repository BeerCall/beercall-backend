"""add proof photo comments

Revision ID: e8b1f2a3c4d5
Revises: c2b7a1e9f4d6
Create Date: 2026-11-09 23:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e8b1f2a3c4d5'
down_revision: Union[str, Sequence[str], None] = 'c2b7a1e9f4d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'proof_photo_comments',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('apero_id', sa.Integer(), nullable=False),
        sa.Column('photo_owner_id', sa.Integer(), nullable=False),
        sa.Column('author_id', sa.Integer(), nullable=False),
        sa.Column('content', sa.String(length=500), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['apero_id'], ['aperos.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['author_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['photo_owner_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_proof_photo_comments_id'), 'proof_photo_comments', ['id'], unique=False)
    op.create_index(
        'ix_proof_photo_comments_apero_owner',
        'proof_photo_comments',
        ['apero_id', 'photo_owner_id'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index('ix_proof_photo_comments_apero_owner', table_name='proof_photo_comments')
    op.drop_index(op.f('ix_proof_photo_comments_id'), table_name='proof_photo_comments')
    op.drop_table('proof_photo_comments')
