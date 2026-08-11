"""add recurring shift series

Revision ID: 9d5cff5bba6e
Revises: d81e04b637aa
Create Date: 2026-08-11 11:29:49.869031

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '9d5cff5bba6e'
down_revision = 'd81e04b637aa'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    sqlite = bind.dialect.name == "sqlite"
    if sqlite:
        op.execute("PRAGMA foreign_keys=OFF")
        op.execute("DROP TABLE IF EXISTS _alembic_tmp_shifts")
    op.create_table('shift_series',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('staff_id', sa.Integer(), nullable=False),
    sa.Column('shift_type_id', sa.Integer(), nullable=False),
    sa.Column('starts_on', sa.Date(), nullable=False),
    sa.Column('ends_on', sa.Date(), nullable=False),
    sa.Column('weekday', sa.Integer(), nullable=False),
    sa.Column('created_by', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ),
    sa.ForeignKeyConstraint(['shift_type_id'], ['shift_types.id'], ),
    sa.ForeignKeyConstraint(['staff_id'], ['staff_profiles.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('shift_series', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_shift_series_shift_type_id'), ['shift_type_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_shift_series_staff_id'), ['staff_id'], unique=False)

    with op.batch_alter_table('shifts', schema=None, recreate="always" if sqlite else "auto") as batch_op:
        batch_op.add_column(sa.Column('series_id', sa.Integer(), nullable=True))
        batch_op.create_index(batch_op.f('ix_shifts_series_id'), ['series_id'], unique=False)
        batch_op.create_foreign_key('fk_shifts_series_id', 'shift_series', ['series_id'], ['id'])

    if sqlite:
        op.execute("PRAGMA foreign_keys=ON")

    # ### end Alembic commands ###


def downgrade():
    bind = op.get_bind()
    sqlite = bind.dialect.name == "sqlite"
    if sqlite:
        op.execute("PRAGMA foreign_keys=OFF")
        op.execute("DROP TABLE IF EXISTS _alembic_tmp_shifts")
    with op.batch_alter_table('shifts', schema=None, recreate="always" if sqlite else "auto") as batch_op:
        batch_op.drop_constraint('fk_shifts_series_id', type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_shifts_series_id'))
        batch_op.drop_column('series_id')

    with op.batch_alter_table('shift_series', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_shift_series_staff_id'))
        batch_op.drop_index(batch_op.f('ix_shift_series_shift_type_id'))

    op.drop_table('shift_series')
    if sqlite:
        op.execute("PRAGMA foreign_keys=ON")
