"""allow scheduled aperos without a photo

Revision ID: 9c4e6f2a1b7d
Revises: 7f2a1d9c4e10
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

revision = "9c4e6f2a1b7d"
down_revision = "7f2a1d9c4e10"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    photo_column = next(
        (column for column in sa.inspect(bind).get_columns("aperos")
         if column["name"] == "photo_path"),
        None,
    )
    if photo_column is not None and photo_column.get("nullable", True) is False:
        with op.batch_alter_table("aperos") as batch:
            batch.alter_column(
                "photo_path",
                existing_type=sa.String(),
                nullable=True,
            )


def downgrade():
    bind = op.get_bind()
    photo_column = next(
        (column for column in sa.inspect(bind).get_columns("aperos")
         if column["name"] == "photo_path"),
        None,
    )
    if photo_column is not None and photo_column.get("nullable", True):
        op.execute(text("UPDATE aperos SET photo_path = '' WHERE photo_path IS NULL"))
        with op.batch_alter_table("aperos") as batch:
            batch.alter_column(
                "photo_path",
                existing_type=sa.String(),
                nullable=False,
            )
