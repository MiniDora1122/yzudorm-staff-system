"""add OCR and document retention policy

Revision ID: 7860ad8b1090
Revises: b4852085ec6b
Create Date: 2026-08-10 22:54:43.839856

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '7860ad8b1090'
down_revision = 'b4852085ec6b'
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if '_alembic_tmp_document_drafts' in inspector.get_table_names():
        # Clean up an empty artifact left by an interrupted SQLite batch migration.
        op.drop_table('_alembic_tmp_document_drafts')
        inspector = sa.inspect(op.get_bind())
    if 'document_retention_policies' not in inspector.get_table_names():
        op.create_table('document_retention_policies',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('retention_days', sa.Integer(), nullable=False),
        sa.Column('cleanup_hour', sa.Integer(), nullable=False),
        sa.Column('cleanup_minute', sa.Integer(), nullable=False),
        sa.Column('last_cleanup_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_by', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['updated_by'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id')
        )
    # SQLite cannot safely rebuild these tables while requests and documents reference
    # them. Legacy work_permit_number columns are retained but no longer mapped or used.


def downgrade():
    inspector = sa.inspect(op.get_bind())
    if 'document_retention_policies' in sa.inspect(op.get_bind()).get_table_names():
        op.drop_table('document_retention_policies')
