"""add English location and shift type names

Revision ID: d81e04b637aa
Revises: c52f41a92b70
Create Date: 2026-08-11 16:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = "d81e04b637aa"
down_revision = "c52f41a92b70"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    sqlite = bind.dialect.name == "sqlite"
    if sqlite:
        op.execute("PRAGMA foreign_keys=OFF")
        op.execute("DROP TABLE IF EXISTS _alembic_tmp_work_locations")
        op.execute("DROP TABLE IF EXISTS _alembic_tmp_shift_types")
    try:
        with op.batch_alter_table("work_locations") as batch:
            batch.add_column(sa.Column("name_en", sa.String(length=100), nullable=True))
        op.execute(
            "UPDATE work_locations SET name_en = CASE code "
            "WHEN 'OFFICE' THEN 'Office' WHEN 'MC' THEN 'Management Center' "
            "WHEN 'PR' THEN 'Parcel Room' ELSE code END"
        )
        with op.batch_alter_table("work_locations", recreate="always" if sqlite else "auto") as batch:
            batch.alter_column("name_en", existing_type=sa.String(length=100), nullable=False)

        with op.batch_alter_table("shift_types") as batch:
            batch.add_column(sa.Column("name_en", sa.String(length=100), nullable=True))
        op.execute(
            "UPDATE shift_types SET name_en = CASE code "
            "WHEN 'OFFICE_AM' THEN 'Office Morning Shift' "
            "WHEN 'OFFICE_PM' THEN 'Office Afternoon Shift' "
            "WHEN 'MC_AM' THEN 'Management Center Morning Shift' "
            "WHEN 'MC_PM' THEN 'Management Center Afternoon Shift' "
            "WHEN 'MC_EVENING' THEN 'Management Center Evening Shift' "
            "WHEN 'MC_SDA' THEN 'Management Center SDA Shift' "
            "ELSE code END"
        )
        with op.batch_alter_table("shift_types", recreate="always" if sqlite else "auto") as batch:
            batch.alter_column("name_en", existing_type=sa.String(length=100), nullable=False)
    finally:
        if sqlite:
            op.execute("PRAGMA foreign_keys=ON")


def downgrade():
    bind = op.get_bind()
    sqlite = bind.dialect.name == "sqlite"
    if sqlite:
        op.execute("PRAGMA foreign_keys=OFF")
    try:
        with op.batch_alter_table("shift_types", recreate="always" if sqlite else "auto") as batch:
            batch.drop_column("name_en")
        with op.batch_alter_table("work_locations", recreate="always" if sqlite else "auto") as batch:
            batch.drop_column("name_en")
    finally:
        if sqlite:
            op.execute("PRAGMA foreign_keys=ON")
