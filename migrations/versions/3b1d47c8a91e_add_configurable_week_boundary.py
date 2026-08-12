"""add configurable week boundary

Revision ID: 3b1d47c8a91e
Revises: acce6927c641
Create Date: 2026-08-12
"""

from alembic import op
import sqlalchemy as sa


revision = "3b1d47c8a91e"
down_revision = "acce6927c641"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("scheduling_policies") as batch_op:
        batch_op.add_column(sa.Column("week_starts_on", sa.Integer(), nullable=False, server_default="0"))
        batch_op.create_check_constraint(
            "ck_scheduling_policy_week_starts_on", "week_starts_on BETWEEN 0 AND 6"
        )


def downgrade():
    with op.batch_alter_table("scheduling_policies") as batch_op:
        batch_op.drop_constraint("ck_scheduling_policy_week_starts_on", type_="check")
        batch_op.drop_column("week_starts_on")
