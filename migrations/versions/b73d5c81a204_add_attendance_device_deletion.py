"""add attendance device deletion tombstone

Revision ID: b73d5c81a204
Revises: 48463e284ed5
"""
from alembic import op
import sqlalchemy as sa


revision = "b73d5c81a204"
down_revision = "48463e284ed5"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("attendance_devices", sa.Column("deleted_at", sa.DateTime(timezone=True)))
    op.create_index("ix_attendance_devices_deleted_at", "attendance_devices", ["deleted_at"])


def downgrade():
    op.drop_index("ix_attendance_devices_deleted_at", table_name="attendance_devices")
    op.drop_column("attendance_devices", "deleted_at")
