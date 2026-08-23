"""harden sessions workflows and attendance event identity

Revision ID: e36c84a91f20
Revises: c92f41d17a86
"""
from alembic import op
import sqlalchemy as sa


revision = "e36c84a91f20"
down_revision = "c92f41d17a86"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("session_version", sa.Integer(), nullable=False, server_default="1"))

    op.execute(
        "UPDATE leave_requests SET status = 'CANCELLED' "
        "WHERE status = 'PENDING' AND id NOT IN ("
        "SELECT MIN(id) FROM leave_requests WHERE status = 'PENDING' GROUP BY shift_id)"
    )
    op.create_index(
        "uq_leave_requests_pending_shift", "leave_requests", ["shift_id"], unique=True,
        sqlite_where=sa.text("status = 'PENDING'"),
        postgresql_where=sa.text("status = 'PENDING'"),
    )

    op.execute(
        "UPDATE swap_requests SET admin_status = 'CANCELLED' "
        "WHERE admin_status IN ('NOT_READY', 'PENDING') AND peer_status != 'REJECTED' AND id NOT IN ("
        "SELECT MIN(id) FROM swap_requests WHERE admin_status IN ('NOT_READY', 'PENDING') "
        "AND peer_status != 'REJECTED' GROUP BY requester_shift_id)"
    )
    op.create_index(
        "uq_swap_requests_active_requester_shift", "swap_requests", ["requester_shift_id"], unique=True,
        sqlite_where=sa.text("admin_status IN ('NOT_READY', 'PENDING') AND peer_status != 'REJECTED'"),
        postgresql_where=sa.text("admin_status IN ('NOT_READY', 'PENDING') AND peer_status != 'REJECTED'"),
    )

    op.execute(
        "UPDATE attendance_events SET device_sequence = -id WHERE EXISTS ("
        "SELECT 1 FROM attendance_events earlier WHERE earlier.device_id = attendance_events.device_id "
        "AND earlier.device_sequence = attendance_events.device_sequence "
        "AND earlier.id < attendance_events.id)"
    )
    op.create_index(
        "uq_attendance_events_device_sequence", "attendance_events",
        ["device_id", "device_sequence"], unique=True,
    )


def downgrade():
    op.drop_index("uq_attendance_events_device_sequence", table_name="attendance_events")
    op.drop_index("uq_swap_requests_active_requester_shift", table_name="swap_requests")
    op.drop_index("uq_leave_requests_pending_shift", table_name="leave_requests")
    op.drop_column("users", "session_version")
