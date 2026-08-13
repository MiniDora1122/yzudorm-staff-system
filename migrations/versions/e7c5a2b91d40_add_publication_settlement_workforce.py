"""add publication settlement workforce and operations indexes

Revision ID: e7c5a2b91d40
Revises: 3b1d47c8a91e
Create Date: 2026-08-13
"""

from alembic import op
import sqlalchemy as sa


revision = "e7c5a2b91d40"
down_revision = "3b1d47c8a91e"
branch_labels = None
depends_on = None


def upgrade():
    # A failed SQLite batch migration can leave this table behind. Core tables
    # are altered in place below so existing foreign keys never need to be broken.
    op.execute("DROP TABLE IF EXISTS _alembic_tmp_users")
    op.add_column("users", sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("archived_by", sa.Integer(), nullable=True))

    op.add_column(
        "shifts",
        sa.Column("publication_status", sa.String(length=20), nullable=False, server_default="PUBLISHED"),
    )
    op.add_column("shifts", sa.Column("published_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("shifts", sa.Column("published_by", sa.Integer(), nullable=True))
    op.create_index(
        "ix_shifts_staff_status_date", "shifts", ["staff_id", "status", "shift_date"], unique=False
    )
    op.create_index(
        "ix_shifts_status_publication_date",
        "shifts",
        ["status", "publication_status", "shift_date"],
        unique=False,
    )
    op.execute("UPDATE shifts SET published_at = created_at WHERE publication_status = 'PUBLISHED'")

    with op.batch_alter_table("leave_requests") as batch_op:
        batch_op.create_index(
            "ix_leave_requests_status_created", ["status", "created_at"], unique=False
        )
    with op.batch_alter_table("swap_requests") as batch_op:
        batch_op.create_index(
            "ix_swap_requests_admin_status_created", ["admin_status", "created_at"], unique=False
        )
    with op.batch_alter_table("audit_logs") as batch_op:
        batch_op.create_index("ix_audit_logs_created_at", ["created_at"], unique=False)
    with op.batch_alter_table("notifications") as batch_op:
        batch_op.create_index(
            "ix_notifications_role_status", ["recipient_role", "status"], unique=False
        )
        batch_op.create_index(
            "ix_notifications_user_status", ["recipient_user_id", "status"], unique=False
        )

    op.create_table(
        "monthly_settlements",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("month_start", sa.Date(), nullable=False),
        sa.Column("is_locked", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("snapshot_json", sa.Text(), nullable=True),
        sa.Column("closed_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("unlocked_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("unlocked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("unlock_reason", sa.String(length=500), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("month_start"),
    )
    op.create_index("ix_monthly_settlements_month_start", "monthly_settlements", ["month_start"])

    op.create_table(
        "backup_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("validation_message", sa.String(length=500), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_backup_runs_status", "backup_runs", ["status"])

    op.create_table(
        "staff_groups",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=100), nullable=False, unique=True),
        sa.Column("name_en", sa.String(length=120), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_staff_groups_is_active", "staff_groups", ["is_active"])

    op.create_table(
        "staff_group_members",
        sa.Column("group_id", sa.Integer(), sa.ForeignKey("staff_groups.id"), primary_key=True),
        sa.Column("staff_id", sa.Integer(), sa.ForeignKey("staff_profiles.id"), primary_key=True),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "staffing_requirements",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("shift_date", sa.Date(), nullable=False),
        sa.Column("shift_type_id", sa.Integer(), sa.ForeignKey("shift_types.id"), nullable=False),
        sa.Column("required_count", sa.Integer(), nullable=False),
        sa.Column("note", sa.String(length=500), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="OPEN"),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("required_count > 0", name="ck_staffing_requirement_positive_count"),
        sa.UniqueConstraint("shift_date", "shift_type_id", name="uq_staffing_requirement_slot"),
    )
    op.create_index(
        "ix_staffing_requirements_status_date", "staffing_requirements", ["status", "shift_date"]
    )

    op.create_table(
        "requirement_audience_groups",
        sa.Column(
            "requirement_id", sa.Integer(), sa.ForeignKey("staffing_requirements.id"), primary_key=True
        ),
        sa.Column("group_id", sa.Integer(), sa.ForeignKey("staff_groups.id"), primary_key=True),
    )
    op.create_table(
        "requirement_audience_staff",
        sa.Column(
            "requirement_id", sa.Integer(), sa.ForeignKey("staffing_requirements.id"), primary_key=True
        ),
        sa.Column("staff_id", sa.Integer(), sa.ForeignKey("staff_profiles.id"), primary_key=True),
    )
    op.create_table(
        "vacancy_applications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "requirement_id", sa.Integer(), sa.ForeignKey("staffing_requirements.id"), nullable=False
        ),
        sa.Column("staff_id", sa.Integer(), sa.ForeignKey("staff_profiles.id"), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="PENDING"),
        sa.Column("note", sa.String(length=500), nullable=True),
        sa.Column("reviewed_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_note", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("requirement_id", "staff_id", name="uq_vacancy_application_staff"),
    )
    op.create_index(
        "ix_vacancy_applications_status_created", "vacancy_applications", ["status", "created_at"]
    )


def downgrade():
    op.drop_index("ix_vacancy_applications_status_created", table_name="vacancy_applications")
    op.drop_table("vacancy_applications")
    op.drop_table("requirement_audience_staff")
    op.drop_table("requirement_audience_groups")
    op.drop_index("ix_staffing_requirements_status_date", table_name="staffing_requirements")
    op.drop_table("staffing_requirements")
    op.drop_table("staff_group_members")
    op.drop_index("ix_staff_groups_is_active", table_name="staff_groups")
    op.drop_table("staff_groups")
    op.drop_index("ix_backup_runs_status", table_name="backup_runs")
    op.drop_table("backup_runs")
    op.drop_index("ix_monthly_settlements_month_start", table_name="monthly_settlements")
    op.drop_table("monthly_settlements")

    with op.batch_alter_table("notifications") as batch_op:
        batch_op.drop_index("ix_notifications_user_status")
        batch_op.drop_index("ix_notifications_role_status")
    with op.batch_alter_table("audit_logs") as batch_op:
        batch_op.drop_index("ix_audit_logs_created_at")
    with op.batch_alter_table("swap_requests") as batch_op:
        batch_op.drop_index("ix_swap_requests_admin_status_created")
    with op.batch_alter_table("leave_requests") as batch_op:
        batch_op.drop_index("ix_leave_requests_status_created")
    op.drop_index("ix_shifts_status_publication_date", table_name="shifts")
    op.drop_index("ix_shifts_staff_status_date", table_name="shifts")
    op.drop_column("shifts", "published_by")
    op.drop_column("shifts", "published_at")
    op.drop_column("shifts", "publication_status")
    op.drop_column("users", "archived_by")
    op.drop_column("users", "archived_at")
