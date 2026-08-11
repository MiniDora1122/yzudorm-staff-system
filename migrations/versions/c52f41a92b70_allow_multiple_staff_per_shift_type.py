"""allow multiple staff per shift type

Revision ID: c52f41a92b70
Revises: a8142d35c901
Create Date: 2026-08-11 15:00:00
"""

from alembic import op


revision = "c52f41a92b70"
down_revision = "a8142d35c901"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    sqlite = bind.dialect.name == "sqlite"
    if sqlite:
        op.execute("PRAGMA foreign_keys=OFF")
        op.execute("DROP TABLE IF EXISTS _alembic_tmp_shifts")
    try:
        with op.batch_alter_table("shifts", recreate="always" if sqlite else "auto") as batch:
            batch.drop_constraint("uq_shift_date_type", type_="unique")
    finally:
        if sqlite:
            op.execute("PRAGMA foreign_keys=ON")


def downgrade():
    bind = op.get_bind()
    sqlite = bind.dialect.name == "sqlite"
    if sqlite:
        op.execute("PRAGMA foreign_keys=OFF")
        op.execute("DROP TABLE IF EXISTS _alembic_tmp_shifts")
    try:
        with op.batch_alter_table("shifts", recreate="always" if sqlite else "auto") as batch:
            batch.create_unique_constraint("uq_shift_date_type", ["shift_date", "shift_type_id"])
    finally:
        if sqlite:
            op.execute("PRAGMA foreign_keys=ON")
