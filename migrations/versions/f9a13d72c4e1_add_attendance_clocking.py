"""add attendance clocking

Revision ID: f9a13d72c4e1
Revises: e7c5a2b91d40
Create Date: 2026-08-19
"""

from alembic import op
import sqlalchemy as sa


revision = "f9a13d72c4e1"
down_revision = "e7c5a2b91d40"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "attendance_policies",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("early_checkin_minutes", sa.Integer(), nullable=False, server_default="60"),
        sa.Column("late_grace_minutes", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("checkout_after_minutes", sa.Integer(), nullable=False, server_default="120"),
        sa.Column("duplicate_seconds", sa.Integer(), nullable=False, server_default="60"),
        sa.Column("updated_by", sa.Integer(), sa.ForeignKey("users.id")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "attendance_devices",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("device_code", sa.String(60), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("location_id", sa.Integer(), sa.ForeignKey("work_locations.id"), nullable=False),
        sa.Column("allowed_cidr", sa.String(80)),
        sa.Column("secret_encrypted", sa.Text()),
        sa.Column("enrollment_token_hash", sa.String(64)),
        sa.Column("enrollment_expires_at", sa.DateTime(timezone=True)),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("enrolled_at", sa.DateTime(timezone=True)),
        sa.Column("last_seen_at", sa.DateTime(timezone=True)),
        sa.Column("last_ip", sa.String(45)),
        sa.Column("last_sequence", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("revoked_by", sa.Integer(), sa.ForeignKey("users.id")),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("device_code"),
        sa.UniqueConstraint("enrollment_token_hash"),
    )
    op.create_index("ix_attendance_devices_device_code", "attendance_devices", ["device_code"])
    op.create_index("ix_attendance_devices_is_active", "attendance_devices", ["is_active"])
    op.create_table(
        "attendance_device_nonces",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("device_id", sa.Integer(), sa.ForeignKey("attendance_devices.id"), nullable=False),
        sa.Column("nonce", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("device_id", "nonce", name="uq_attendance_device_nonce"),
    )
    op.create_index("ix_attendance_device_nonces_device_id", "attendance_device_nonces", ["device_id"])
    op.create_index("ix_attendance_device_nonces_created_at", "attendance_device_nonces", ["created_at"])
    op.create_table(
        "staff_cards",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("staff_id", sa.Integer(), sa.ForeignKey("staff_profiles.id"), nullable=False),
        sa.Column("uid_hash", sa.String(64), nullable=False),
        sa.Column("uid_last4", sa.String(4), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("registered_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("registered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("disabled_by", sa.Integer(), sa.ForeignKey("users.id")),
        sa.Column("disabled_at", sa.DateTime(timezone=True)),
        sa.Column("disable_reason", sa.String(500)),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("uid_hash"),
    )
    op.create_index("ix_staff_cards_uid_hash", "staff_cards", ["uid_hash"])
    op.create_index("ix_staff_cards_staff_status", "staff_cards", ["staff_id", "status"])
    op.create_table(
        "attendance_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event_uuid", sa.String(36), nullable=False),
        sa.Column("device_id", sa.Integer(), sa.ForeignKey("attendance_devices.id"), nullable=False),
        sa.Column("staff_id", sa.Integer(), sa.ForeignKey("staff_profiles.id")),
        sa.Column("card_id", sa.Integer(), sa.ForeignKey("staff_cards.id")),
        sa.Column("shift_id", sa.Integer(), sa.ForeignKey("shifts.id")),
        sa.Column("method", sa.String(20), nullable=False),
        sa.Column("direction", sa.String(20), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_ip", sa.String(45)),
        sa.Column("offline_synced", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("device_sequence", sa.Integer(), nullable=False),
        sa.Column("late_minutes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reason_category", sa.String(80)),
        sa.Column("reason_text", sa.String(1000)),
        sa.Column("claimed_arrival_at", sa.DateTime(timezone=True)),
        sa.Column("reviewed_by", sa.Integer(), sa.ForeignKey("users.id")),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("review_note", sa.String(1000)),
        sa.UniqueConstraint("event_uuid"),
    )
    op.create_index("ix_attendance_events_event_uuid", "attendance_events", ["event_uuid"])
    op.create_index("ix_attendance_events_staff_id", "attendance_events", ["staff_id"])
    op.create_index("ix_attendance_events_shift_id", "attendance_events", ["shift_id"])
    op.create_index("ix_attendance_events_status", "attendance_events", ["status"])
    op.create_index("ix_attendance_events_staff_time", "attendance_events", ["staff_id", "occurred_at"])
    op.create_index("ix_attendance_events_status_time", "attendance_events", ["status", "occurred_at"])
    op.create_index("ix_attendance_events_shift_direction", "attendance_events", ["shift_id", "direction"])
    op.create_table(
        "attendance_adjustments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event_id", sa.Integer(), sa.ForeignKey("attendance_events.id"), nullable=False),
        sa.Column("direction", sa.String(20), nullable=False),
        sa.Column("adjusted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.String(1000), nullable=False),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_attendance_adjustments_event_id", "attendance_adjustments", ["event_id"])


def downgrade():
    op.drop_table("attendance_adjustments")
    op.drop_table("attendance_events")
    op.drop_table("staff_cards")
    op.drop_table("attendance_device_nonces")
    op.drop_table("attendance_devices")
    op.drop_table("attendance_policies")
