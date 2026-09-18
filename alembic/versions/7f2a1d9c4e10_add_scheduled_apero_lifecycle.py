"""add scheduled apero lifecycle

Revision ID: 7f2a1d9c4e10
Revises: a4097df63198
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

revision = "7f2a1d9c4e10"
down_revision = "a4097df63198"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {c["name"] for c in inspector.get_columns("aperos")}
    for name, column in (
        ("status", sa.Column("status", sa.String(20), nullable=True)),
        ("scheduled_for", sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=True)),
        ("started_at", sa.Column("started_at", sa.DateTime(timezone=True), nullable=True)),
        ("ended_at", sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True)),
        ("daily_check_processed_at", sa.Column("daily_check_processed_at", sa.DateTime(timezone=True), nullable=True)),
    ):
        if name not in columns:
            op.add_column("aperos", column)
    op.execute(text("UPDATE aperos SET status = 'ACTIVE' WHERE status IS NULL"))
    op.execute(text("UPDATE aperos SET started_at = created_at WHERE started_at IS NULL"))
    if bind.dialect.name == "sqlite":
        op.execute(text("UPDATE aperos SET ended_at = datetime(created_at, '+4 hours') WHERE status = 'ENDED' AND ended_at IS NULL"))
    else:
        op.execute(text("UPDATE aperos SET ended_at = created_at + INTERVAL '4 hours' WHERE status = 'ENDED' AND ended_at IS NULL"))
    with op.batch_alter_table("aperos") as batch:
        batch.alter_column("status", nullable=False, server_default="ACTIVE")
        batch.create_index("ix_aperos_status", ["status"])
        batch.create_index("ix_aperos_scheduled_for", ["scheduled_for"])
        batch.create_index("ix_aperos_started_at", ["started_at"])
        batch.create_index("ix_aperos_daily_check_processed_at", ["daily_check_processed_at"])
    with op.batch_alter_table("apero_participants") as batch:
        existing = {c["name"] for c in sa.inspect(bind).get_columns("apero_participants")}
        if "created_at" not in existing:
            batch.add_column(sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True))
        if "updated_at" not in existing:
            batch.add_column(sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True))
    op.execute(text("DELETE FROM apero_participants WHERE id NOT IN (SELECT MIN(id) FROM apero_participants GROUP BY apero_id, user_id)"))
    with op.batch_alter_table("apero_participants") as batch:
        batch.create_unique_constraint("uq_apero_participant_user", ["apero_id", "user_id"])


def downgrade():
    with op.batch_alter_table("apero_participants") as batch:
        batch.drop_constraint("uq_apero_participant_user", type_="unique")
        batch.drop_column("updated_at")
        batch.drop_column("created_at")
    with op.batch_alter_table("aperos") as batch:
        for name in ("daily_check_processed_at", "ended_at", "started_at", "scheduled_for", "status"):
            batch.drop_column(name)
