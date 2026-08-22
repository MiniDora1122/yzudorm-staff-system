"""add configurable backup policy

Revision ID: b71c8e4d2a90
Revises: f9a13d72c4e1
Create Date: 2026-08-23
"""

from alembic import op
import sqlalchemy as sa


revision = "b71c8e4d2a90"
down_revision = "f9a13d72c4e1"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "backup_policies",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("mode", sa.String(length=8), nullable=False, server_default="DAILY"),
        sa.Column("interval_hours", sa.Integer(), nullable=False, server_default="24"),
        sa.Column("daily_hour", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("daily_minute", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "interval_hours >= 1 AND interval_hours <= 168",
            name="ck_backup_policy_interval_hours",
        ),
        sa.CheckConstraint(
            "daily_hour >= 0 AND daily_hour <= 23",
            name="ck_backup_policy_daily_hour",
        ),
        sa.CheckConstraint(
            "daily_minute >= 0 AND daily_minute <= 59",
            name="ck_backup_policy_daily_minute",
        ),
    )


def downgrade():
    op.drop_table("backup_policies")
