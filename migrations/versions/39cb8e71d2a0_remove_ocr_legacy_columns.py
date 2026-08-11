"""remove OCR and unused legacy document columns

Revision ID: 39cb8e71d2a0
Revises: 0f6d8a2c4b17
Create Date: 2026-08-11 00:05:00
"""

from alembic import op
import sqlalchemy as sa


revision = "39cb8e71d2a0"
down_revision = "0f6d8a2c4b17"
branch_labels = None
depends_on = None


PAGE_KIND_ENUM = sa.Enum(
    "RESIDENCE_FRONT",
    "RESIDENCE_BACK",
    "WORK_PERMIT_PAGE_1",
    "WORK_PERMIT_PAGE_2",
    name="documentpagekind",
    native_enum=False,
)


def _column_names(table_name: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table_name)}


def upgrade():
    bind = op.get_bind()
    sqlite = bind.dialect.name == "sqlite"
    if sqlite:
        op.execute("PRAGMA foreign_keys=OFF")
    try:
        draft_columns = _column_names("document_drafts")
        if "work_permit_number" in draft_columns:
            with op.batch_alter_table("document_drafts", recreate="always" if sqlite else "auto") as batch:
                batch.drop_column("work_permit_number")

        document_columns = _column_names("staff_documents")
        indexes = {index["name"] for index in sa.inspect(bind).get_indexes("staff_documents")}
        with op.batch_alter_table("staff_documents", recreate="always" if sqlite else "auto") as batch:
            if "uq_document_set_page" in indexes:
                batch.drop_index("uq_document_set_page")
            for column in ("ocr_confidence", "ocr_status", "extracted_data_json"):
                if column in document_columns:
                    batch.drop_column(column)
            batch.alter_column(
                "document_set_id",
                existing_type=sa.String(length=32),
                nullable=False,
            )
            batch.alter_column(
                "page_kind",
                existing_type=sa.String(length=30),
                type_=PAGE_KIND_ENUM,
                nullable=False,
            )
            batch.create_unique_constraint(
                "uq_document_set_page", ["document_set_id", "page_kind"]
            )

        profile_columns = _column_names("staff_profiles")
        if "work_permit_number" in profile_columns:
            with op.batch_alter_table("staff_profiles", recreate="always" if sqlite else "auto") as batch:
                batch.drop_column("work_permit_number")
    finally:
        if sqlite:
            op.execute("PRAGMA foreign_keys=ON")


def downgrade():
    bind = op.get_bind()
    sqlite = bind.dialect.name == "sqlite"
    if sqlite:
        op.execute("PRAGMA foreign_keys=OFF")
    try:
        with op.batch_alter_table("staff_profiles", recreate="always" if sqlite else "auto") as batch:
            batch.add_column(sa.Column("work_permit_number", sa.String(length=100), nullable=True))
        with op.batch_alter_table("staff_documents", recreate="always" if sqlite else "auto") as batch:
            batch.drop_constraint("uq_document_set_page", type_="unique")
            batch.add_column(sa.Column("ocr_status", sa.String(length=30), nullable=False, server_default="NOT_STARTED"))
            batch.add_column(sa.Column("ocr_confidence", sa.Numeric(5, 2), nullable=True))
            batch.add_column(sa.Column("extracted_data_json", sa.JSON(), nullable=True))
            batch.alter_column("document_set_id", existing_type=sa.String(length=32), nullable=True)
            batch.alter_column("page_kind", existing_type=PAGE_KIND_ENUM, type_=sa.String(length=30), nullable=True)
            batch.create_index("uq_document_set_page", ["document_set_id", "page_kind"], unique=True)
        with op.batch_alter_table("document_drafts", recreate="always" if sqlite else "auto") as batch:
            batch.add_column(sa.Column("work_permit_number", sa.String(length=100), nullable=True))
    finally:
        if sqlite:
            op.execute("PRAGMA foreign_keys=ON")
