"""Restore the user creation timestamp default without dropping existing values.

Revision ID: 320ad2a4bc1f
Revises: e53b7a9c2d41
"""
from alembic import op
import sqlalchemy as sa

revision = "320ad2a4bc1f"
down_revision = "e53b7a9c2d41"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("users")}
    if "created_at" not in columns:
        # Repair databases on which the original destructive revision was applied.
        # Historical timestamps cannot be recovered here; existing backups are needed.
        op.add_column("users", sa.Column("created_at", sa.DateTime(timezone=True),
                                        server_default=sa.func.now(), nullable=False))
    else:
        op.alter_column("users", "created_at", server_default=sa.func.now())


def downgrade() -> None:
    # The column predates Plan B and must survive a rollback.
    op.alter_column("users", "created_at", server_default=None)
