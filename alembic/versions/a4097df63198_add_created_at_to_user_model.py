"""add created_at to user model

Revision ID: a4097df63198
Revises: 883ae2b8ae13
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text


revision = "a4097df63198"
down_revision = "883ae2b8ae13"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("users")}

    if "created_at" not in columns:
        op.add_column(
            "users",
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.execute(text("UPDATE users SET created_at = CURRENT_TIMESTAMP WHERE created_at IS NULL"))
        with op.batch_alter_table("users") as batch:
            batch.alter_column("created_at", nullable=False)


def downgrade():
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("users")}
    if "created_at" in columns:
        with op.batch_alter_table("users") as batch:
            batch.drop_column("created_at")
