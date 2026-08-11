"""add admin display name

Revision ID: a8142d35c901
Revises: 39cb8e71d2a0
Create Date: 2026-08-11 10:30:00
"""

from alembic import op
import sqlalchemy as sa


revision = "a8142d35c901"
down_revision = "39cb8e71d2a0"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("display_name", sa.String(length=100), nullable=True))
    op.execute(
        "UPDATE users SET display_name = username "
        "WHERE role = 'ADMIN' AND (display_name IS NULL OR display_name = '')"
    )


def downgrade():
    with op.batch_alter_table("users") as batch:
        batch.drop_column("display_name")
