"""add attendance device identity

Revision ID: c9f45a12d803
Revises: b71c8e4d2a90
Create Date: 2026-08-23
"""

from alembic import op
import sqlalchemy as sa


revision = "c9f45a12d803"
down_revision = "b71c8e4d2a90"
branch_labels = None
depends_on = None


def upgrade():
    # A failed SQLite batch migration can leave this Alembic-owned table behind.
    op.execute("DROP TABLE IF EXISTS _alembic_tmp_attendance_devices")
    op.add_column("attendance_devices", sa.Column("installation_id", sa.String(36)))
    op.add_column("attendance_devices", sa.Column("computer_name", sa.String(255)))
    op.add_column("attendance_devices", sa.Column("mac_addresses_json", sa.Text()))
    op.add_column("attendance_devices", sa.Column("pending_computer_name", sa.String(255)))
    op.add_column("attendance_devices", sa.Column("pending_mac_addresses_json", sa.Text()))
    op.add_column("attendance_devices", sa.Column("identity_changed_at", sa.DateTime(timezone=True)))
    op.create_index("uq_attendance_devices_installation_id", "attendance_devices", ["installation_id"], unique=True)


def downgrade():
    op.drop_index("uq_attendance_devices_installation_id", table_name="attendance_devices")
    op.drop_column("attendance_devices", "identity_changed_at")
    op.drop_column("attendance_devices", "pending_mac_addresses_json")
    op.drop_column("attendance_devices", "pending_computer_name")
    op.drop_column("attendance_devices", "mac_addresses_json")
    op.drop_column("attendance_devices", "computer_name")
    op.drop_column("attendance_devices", "installation_id")
