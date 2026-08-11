"""add dynamic locations and payroll settings

Revision ID: 486c51e89d9e
Revises: bc7a80fd741c
Create Date: 2026-08-10 16:55:15.937860

"""
from alembic import op
import sqlalchemy as sa
from datetime import datetime, timezone


# revision identifiers, used by Alembic.
revision = '486c51e89d9e'
down_revision = 'bc7a80fd741c'
branch_labels = None
depends_on = None


def upgrade():
    is_sqlite = op.get_bind().dialect.name == 'sqlite'
    op.create_table('payroll_settings',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('effective_date', sa.Date(), nullable=False),
    sa.Column('default_hourly_wage', sa.Numeric(precision=8, scale=2), nullable=False),
    sa.Column('labor_insurance_rate', sa.Numeric(precision=8, scale=6), nullable=False),
    sa.Column('employment_insurance_rate', sa.Numeric(precision=8, scale=6), nullable=False),
    sa.Column('employer_labor_share', sa.Numeric(precision=8, scale=6), nullable=False),
    sa.Column('occupational_accident_rate', sa.Numeric(precision=8, scale=6), nullable=False),
    sa.Column('health_insurance_rate', sa.Numeric(precision=8, scale=6), nullable=False),
    sa.Column('employer_health_share', sa.Numeric(precision=8, scale=6), nullable=False),
    sa.Column('average_dependents', sa.Numeric(precision=6, scale=4), nullable=False),
    sa.Column('supplementary_health_rate', sa.Numeric(precision=8, scale=6), nullable=False),
    sa.Column('employer_pension_rate', sa.Numeric(precision=8, scale=6), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('effective_date')
    )
    op.create_table('work_locations',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('code', sa.String(length=40), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('color', sa.String(length=7), nullable=False),
    sa.Column('display_order', sa.Integer(), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('code'),
    sa.UniqueConstraint('name')
    )
    locations = sa.table(
        'work_locations',
        sa.column('id', sa.Integer()),
        sa.column('code', sa.String()),
        sa.column('name', sa.String()),
        sa.column('color', sa.String()),
        sa.column('display_order', sa.Integer()),
        sa.column('is_active', sa.Boolean()),
        sa.column('created_at', sa.DateTime(timezone=True)),
        sa.column('updated_at', sa.DateTime(timezone=True)),
    )
    now = datetime(2026, 8, 10, tzinfo=timezone.utc)
    op.bulk_insert(locations, [
        {'id': 1, 'code': 'OFFICE', 'name': '辦公室', 'color': '#198754', 'display_order': 10, 'is_active': True, 'created_at': now, 'updated_at': now},
        {'id': 2, 'code': 'MC', 'name': '管理中心', 'color': '#1556a3', 'display_order': 20, 'is_active': True, 'created_at': now, 'updated_at': now},
    ])

    with op.batch_alter_table('shift_types', schema=None) as batch_op:
        batch_op.add_column(sa.Column('location_id', sa.Integer(), nullable=True))

    op.execute("UPDATE shift_types SET location_id = CASE location WHEN 'OFFICE' THEN 1 WHEN 'MC' THEN 2 ELSE 1 END")

    # SQLite batch migration rebuilds shift_types. Temporarily disabling foreign
    # keys prevents the existing shifts table from blocking that replacement.
    if is_sqlite:
        with op.get_context().autocommit_block():
            op.execute('PRAGMA foreign_keys=OFF')
    try:
        with op.batch_alter_table('shift_types', schema=None) as batch_op:
            batch_op.alter_column('location_id', existing_type=sa.Integer(), nullable=False)
            batch_op.drop_index(batch_op.f('ix_shift_types_location'))
            batch_op.create_index(batch_op.f('ix_shift_types_location_id'), ['location_id'], unique=False)
            batch_op.create_foreign_key('fk_shift_types_location_id_work_locations', 'work_locations', ['location_id'], ['id'])
            batch_op.drop_column('location')
    finally:
        if is_sqlite:
            with op.get_context().autocommit_block():
                op.execute('PRAGMA foreign_keys=ON')

    with op.batch_alter_table('staff_profiles', schema=None) as batch_op:
        batch_op.add_column(sa.Column('hourly_wage', sa.Numeric(precision=8, scale=2), nullable=True))
        batch_op.add_column(sa.Column('labor_insured_salary', sa.Numeric(precision=10, scale=2), nullable=True))
        batch_op.add_column(sa.Column('health_insured_salary', sa.Numeric(precision=10, scale=2), nullable=True))
        batch_op.add_column(sa.Column('pension_salary', sa.Numeric(precision=10, scale=2), nullable=True))
        batch_op.add_column(sa.Column('labor_insurance_enabled', sa.Boolean(), nullable=False, server_default=sa.true()))
        batch_op.add_column(sa.Column('employment_insurance_enabled', sa.Boolean(), nullable=False, server_default=sa.true()))
        batch_op.add_column(sa.Column('health_insurance_enabled', sa.Boolean(), nullable=False, server_default=sa.true()))
        batch_op.add_column(sa.Column('labor_pension_enabled', sa.Boolean(), nullable=False, server_default=sa.true()))


def downgrade():
    is_sqlite = op.get_bind().dialect.name == 'sqlite'
    with op.batch_alter_table('staff_profiles', schema=None) as batch_op:
        batch_op.drop_column('labor_pension_enabled')
        batch_op.drop_column('health_insurance_enabled')
        batch_op.drop_column('employment_insurance_enabled')
        batch_op.drop_column('labor_insurance_enabled')
        batch_op.drop_column('pension_salary')
        batch_op.drop_column('health_insured_salary')
        batch_op.drop_column('labor_insured_salary')
        batch_op.drop_column('hourly_wage')

    with op.batch_alter_table('shift_types', schema=None) as batch_op:
        batch_op.add_column(sa.Column('location', sa.VARCHAR(length=30), nullable=True))

    op.execute("UPDATE shift_types SET location = (SELECT code FROM work_locations WHERE work_locations.id = shift_types.location_id)")

    if is_sqlite:
        with op.get_context().autocommit_block():
            op.execute('PRAGMA foreign_keys=OFF')
    try:
        with op.batch_alter_table('shift_types', schema=None) as batch_op:
            batch_op.alter_column('location', existing_type=sa.VARCHAR(length=30), nullable=False)
            batch_op.drop_constraint('fk_shift_types_location_id_work_locations', type_='foreignkey')
            batch_op.drop_index(batch_op.f('ix_shift_types_location_id'))
            batch_op.create_index(batch_op.f('ix_shift_types_location'), ['location'], unique=False)
            batch_op.drop_column('location_id')
    finally:
        if is_sqlite:
            with op.get_context().autocommit_block():
                op.execute('PRAGMA foreign_keys=ON')

    op.drop_table('work_locations')
    op.drop_table('payroll_settings')
