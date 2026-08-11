"""add multi-page document sets

Revision ID: 0f6d8a2c4b17
Revises: 7860ad8b1090
Create Date: 2026-08-10 23:45:00
"""

from alembic import op
import sqlalchemy as sa


revision = "0f6d8a2c4b17"
down_revision = "7860ad8b1090"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("staff_documents")}
    if "document_set_id" not in columns:
        op.add_column("staff_documents", sa.Column("document_set_id", sa.String(length=32), nullable=True))
    if "page_kind" not in columns:
        op.add_column("staff_documents", sa.Column("page_kind", sa.String(length=30), nullable=True))

    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id, document_type FROM staff_documents")).fetchall()
    for row in rows:
        page_kind = "RESIDENCE_FRONT" if row.document_type == "RESIDENCE_PERMIT" else "WORK_PERMIT_PAGE_1"
        bind.execute(
            sa.text(
                "UPDATE staff_documents SET document_set_id = :set_id, page_kind = :page_kind "
                "WHERE id = :document_id AND document_set_id IS NULL"
            ),
            {"set_id": f"legacy{row.id:026d}", "page_kind": page_kind, "document_id": row.id},
        )

    indexes = {index["name"] for index in sa.inspect(bind).get_indexes("staff_documents")}
    if "ix_staff_documents_document_set_id" not in indexes:
        op.create_index("ix_staff_documents_document_set_id", "staff_documents", ["document_set_id"])
    if "uq_document_set_page" not in indexes:
        op.create_index(
            "uq_document_set_page",
            "staff_documents",
            ["document_set_id", "page_kind"],
            unique=True,
        )


def downgrade():
    indexes = {index["name"] for index in sa.inspect(op.get_bind()).get_indexes("staff_documents")}
    if "uq_document_set_page" in indexes:
        op.drop_index("uq_document_set_page", table_name="staff_documents")
    if "ix_staff_documents_document_set_id" in indexes:
        op.drop_index("ix_staff_documents_document_set_id", table_name="staff_documents")
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("staff_documents")}
    if "page_kind" in columns:
        op.drop_column("staff_documents", "page_kind")
    if "document_set_id" in columns:
        op.drop_column("staff_documents", "document_set_id")
