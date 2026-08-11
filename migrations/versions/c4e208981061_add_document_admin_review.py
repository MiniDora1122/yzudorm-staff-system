"""add document admin review

Revision ID: c4e208981061
Revises: 9d5cff5bba6e
Create Date: 2026-08-11 13:34:25.281639

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c4e208981061'
down_revision = '9d5cff5bba6e'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    sqlite = bind.dialect.name == "sqlite"
    if sqlite:
        op.execute("PRAGMA foreign_keys=OFF")
        op.execute("DROP TABLE IF EXISTS _alembic_tmp_staff_documents")
    with op.batch_alter_table('staff_documents', schema=None, recreate="always" if sqlite else "auto") as batch_op:
        batch_op.add_column(sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('reviewed_by', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('rejection_reason', sa.Text(), nullable=True))
        batch_op.alter_column('status',
               existing_type=sa.VARCHAR(length=12),
               type_=sa.Enum('UPLOADED', 'NEEDS_REVIEW', 'PENDING_ADMIN', 'CONFIRMED', 'REJECTED', 'REPLACED', 'FAILED', 'DELETED', name='documentstatus', native_enum=False),
               existing_nullable=False)
        batch_op.create_foreign_key('fk_staff_documents_reviewed_by', 'users', ['reviewed_by'], ['id'])

    if sqlite:
        op.execute("PRAGMA foreign_keys=ON")

    # ### end Alembic commands ###


def downgrade():
    bind = op.get_bind()
    sqlite = bind.dialect.name == "sqlite"
    if sqlite:
        op.execute("PRAGMA foreign_keys=OFF")
        op.execute("DROP TABLE IF EXISTS _alembic_tmp_staff_documents")
    op.execute("UPDATE staff_documents SET status='NEEDS_REVIEW' WHERE status IN ('PENDING_ADMIN', 'REJECTED')")
    with op.batch_alter_table('staff_documents', schema=None, recreate="always" if sqlite else "auto") as batch_op:
        batch_op.drop_constraint('fk_staff_documents_reviewed_by', type_='foreignkey')
        batch_op.alter_column('status',
               existing_type=sa.Enum('UPLOADED', 'NEEDS_REVIEW', 'PENDING_ADMIN', 'CONFIRMED', 'REJECTED', 'REPLACED', 'FAILED', 'DELETED', name='documentstatus', native_enum=False),
               type_=sa.VARCHAR(length=12),
               existing_nullable=False)
        batch_op.drop_column('rejection_reason')
        batch_op.drop_column('reviewed_by')
        batch_op.drop_column('reviewed_at')

    if sqlite:
        op.execute("PRAGMA foreign_keys=ON")

    # ### end Alembic commands ###
