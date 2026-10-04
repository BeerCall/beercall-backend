"""baseline

Revision ID: b8c5b3d541dc
Revises: 
Create Date: 2026-03-25 18:13:10.582311

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b8c5b3d541dc'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. users
    op.create_table(
        'users',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('username', sa.String(), nullable=False),
        sa.Column('hashed_password', sa.String(), nullable=False),
        sa.Column('capsules', sa.Integer(), nullable=True),
        sa.Column('avatar_config', sa.JSON(), nullable=True),
        sa.Column('ia_fraud_count', sa.Integer(), nullable=True),
        sa.Column('consecutive_joins', sa.Integer(), nullable=True),
        sa.Column('consecutive_declines', sa.Integer(), nullable=True),
        sa.Column('consecutive_piscine', sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_users_id'), 'users', ['id'], unique=False)
    op.create_index(op.f('ix_users_username'), 'users', ['username'], unique=True)

    # 2. squads
    op.create_table(
        'squads',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('icon', sa.String(), nullable=True),
        sa.Column('color', sa.String(), nullable=True),
        sa.Column('invite_code', sa.String(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_squads_id'), 'squads', ['id'], unique=False)
    op.create_index(op.f('ix_squads_invite_code'), 'squads', ['invite_code'], unique=True)

    # 3. squad_members
    op.create_table(
        'squad_members',
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('squad_id', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['squad_id'], ['squads.id'], ),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('user_id', 'squad_id')
    )

    # 4. badges
    op.create_table(
        'badges',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('description', sa.String(), nullable=True),
        sa.Column('icon', sa.String(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_badges_id'), 'badges', ['id'], unique=False)

    # 5. skins
    op.create_table(
        'skins',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('gender', sa.String(), nullable=False),
        sa.Column('category', sa.String(), nullable=False),
        sa.Column('price_caps', sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_skins_id'), 'skins', ['id'], unique=False)

    # 6. user_badges
    op.create_table(
        'user_badges',
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('badge_id', sa.String(), nullable=False),
        sa.Column('unlocked_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['badge_id'], ['badges.id'], ),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('user_id', 'badge_id')
    )

    # 7. user_skins
    op.create_table(
        'user_skins',
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('skin_id', sa.String(), nullable=False),
        sa.Column('unlocked_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['skin_id'], ['skins.id'], ),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('user_id', 'skin_id')
    )

    # 8. aperos
    op.create_table(
        'aperos',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('squad_id', sa.Integer(), nullable=False),
        sa.Column('creator_id', sa.Integer(), nullable=False),
        sa.Column('location_name', sa.String(), nullable=True),
        sa.Column('latitude', sa.Float(), nullable=False),
        sa.Column('longitude', sa.Float(), nullable=False),
        sa.Column('photo_path', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['creator_id'], ['users.id'], ),
        sa.ForeignKeyConstraint(['squad_id'], ['squads.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_aperos_id'), 'aperos', ['id'], unique=False)

    # 9. apero_participants
    
    op.create_table(
        'apero_participants',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('apero_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('status', sa.Enum('JOINED', 'DECLINED', 'GHOST', name='participationstatus'), nullable=True),
        sa.Column('excuse', sa.String(), nullable=True),
        sa.Column('photo_path', sa.String(), nullable=True),
        sa.ForeignKeyConstraint(['apero_id'], ['aperos.id'], ),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_apero_participants_id'), 'apero_participants', ['id'], unique=False)


def downgrade() -> None:
    # 9
    op.drop_index(op.f('ix_apero_participants_id'), table_name='apero_participants')
    op.drop_table('apero_participants')
    bind = op.get_bind()
    if bind.engine.name == 'postgresql':
        op.execute("DROP TYPE participationstatus")
    # 8
    op.drop_index(op.f('ix_aperos_id'), table_name='aperos')
    op.drop_table('aperos')
    # 7
    op.drop_table('user_skins')
    # 6
    op.drop_table('user_badges')
    # 5
    op.drop_index(op.f('ix_skins_id'), table_name='skins')
    op.drop_table('skins')
    # 4
    op.drop_index(op.f('ix_badges_id'), table_name='badges')
    op.drop_table('badges')
    # 3
    op.drop_table('squad_members')
    # 2
    op.drop_index(op.f('ix_squads_invite_code'), table_name='squads')
    op.drop_index(op.f('ix_squads_id'), table_name='squads')
    op.drop_table('squads')
    # 1
    op.drop_index(op.f('ix_users_username'), table_name='users')
    op.drop_index(op.f('ix_users_id'), table_name='users')
    op.drop_table('users')
