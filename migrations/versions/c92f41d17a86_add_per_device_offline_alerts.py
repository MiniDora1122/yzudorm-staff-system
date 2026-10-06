# Copyright (c) 2026 Tay Yang Long. All Rights Reserved. Designed & Developed by Tay Yang Long.
"""add per-device offline alert settings

Revision ID: c92f41d17a86
Revises: b73d5c81a204
"""
from alembic import op
import sqlalchemy as sa


revision = "c92f41d17a86"
down_revision = "b73d5c81a204"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("attendance_devices", sa.Column("offline_alert_enabled", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("attendance_devices", sa.Column("offline_threshold_minutes", sa.Integer(), nullable=False, server_default="15"))
    op.add_column("attendance_devices", sa.Column("offline_monitor_weekdays", sa.String(length=20), nullable=False, server_default="0,1,2,3,4,5,6"))
    op.add_column("attendance_devices", sa.Column("offline_monitor_start", sa.Time(), nullable=False, server_default="00:00:00"))
    op.add_column("attendance_devices", sa.Column("offline_monitor_end", sa.Time(), nullable=False, server_default="23:59:00"))
    op.execute(
        "UPDATE attendance_devices SET offline_threshold_minutes = "
        "COALESCE((SELECT offline_threshold_minutes FROM attendance_policies WHERE id = 1), 15)"
    )


def downgrade():
    op.drop_column("attendance_devices", "offline_monitor_end")
    op.drop_column("attendance_devices", "offline_monitor_start")
    op.drop_column("attendance_devices", "offline_monitor_weekdays")
    op.drop_column("attendance_devices", "offline_threshold_minutes")
    op.drop_column("attendance_devices", "offline_alert_enabled")
