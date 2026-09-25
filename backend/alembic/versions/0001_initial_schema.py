"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-09-16 14:00:00.000000

"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Users
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=True),
        sa.Column("hashed_password", sa.String(), nullable=False),
        sa.Column("is_admin", sa.Boolean(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=True),
        sa.Column("is_verified", sa.Boolean(), nullable=True),
        sa.Column("verification_token", sa.String(), nullable=True),
        sa.Column("storage_used_bytes", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_users_email"), "users", ["email"], unique=True)

    # Documents
    op.create_table(
        "documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("filename", sa.String(), nullable=False),
        sa.Column("file_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("file_type", sa.String(), nullable=False),
        sa.Column("storage_key", sa.String(), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(), nullable=True),
        sa.Column("error_message", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.execute(
        "CREATE INDEX idx_documents_filename_gin ON documents USING gin (to_tsvector('english', filename))"
    )

    # Document Shares
    op.create_table(
        "document_shares",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("shared_with_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("permission", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
        ),
        sa.ForeignKeyConstraint(
            ["shared_with_user_id"],
            ["users.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # Jobs
    op.create_table(
        "jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(), nullable=True),
        sa.Column("current_stage", sa.String(), nullable=True),
        sa.Column("pages_processed", sa.Integer(), nullable=True),
        sa.Column("total_pages", sa.Integer(), nullable=True),
        sa.Column("error_message", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # Tables
    op.create_table(
        "tables",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=True),
        sa.Column("col_count", sa.Integer(), nullable=True),
        sa.Column("is_stitched", sa.Boolean(), nullable=True),
        sa.Column("title", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # Pages
    op.create_table(
        "pages",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("width", sa.Float(), nullable=True),
        sa.Column("height", sa.Float(), nullable=True),
        sa.Column("status", sa.String(), nullable=True),
        sa.Column("error_message", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # Warnings
    op.create_table(
        "warnings",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("page_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("warning_type", sa.String(), nullable=True),
        sa.Column("message", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["page_id"],
            ["pages.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # Regions
    op.create_table(
        "regions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("page_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reading_order", sa.Integer(), nullable=True),
        sa.Column("region_type", sa.String(), nullable=True),
        sa.Column("x0", sa.Float(), nullable=True),
        sa.Column("y0", sa.Float(), nullable=True),
        sa.Column("x1", sa.Float(), nullable=True),
        sa.Column("y1", sa.Float(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(
            ["page_id"],
            ["pages.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # Region Texts
    op.create_table(
        "region_texts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("region_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("text_content", sa.String(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(
            ["region_id"],
            ["regions.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.execute(
        "CREATE INDEX idx_region_texts_content_gin ON region_texts USING gin (to_tsvector('english', text_content))"
    )

    # Region Images
    op.create_table(
        "region_images",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("region_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("image_key", sa.String(), nullable=True),
        sa.Column("caption", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(
            ["region_id"],
            ["regions.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # Region Tables
    op.create_table(
        "region_tables",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("region_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("table_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reading_order", sa.Integer(), nullable=True),
        sa.Column("row_start", sa.Integer(), nullable=True),
        sa.Column("row_end", sa.Integer(), nullable=True),
        sa.Column("col_start", sa.Integer(), nullable=True),
        sa.Column("col_end", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["region_id"],
            ["regions.id"],
        ),
        sa.ForeignKeyConstraint(
            ["table_id"],
            ["tables.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # Table Cells
    op.create_table(
        "table_cells",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("region_table_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("row_idx", sa.Integer(), nullable=True),
        sa.Column("col_idx", sa.Integer(), nullable=True),
        sa.Column("row_span", sa.Integer(), nullable=True),
        sa.Column("col_span", sa.Integer(), nullable=True),
        sa.Column("text_content", sa.String(), nullable=True),
        sa.Column("x0", sa.Float(), nullable=True),
        sa.Column("y0", sa.Float(), nullable=True),
        sa.Column("x1", sa.Float(), nullable=True),
        sa.Column("y1", sa.Float(), nullable=True),
        sa.Column("is_header", sa.Boolean(), nullable=True),
        sa.ForeignKeyConstraint(
            ["region_table_id"],
            ["region_tables.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    pass
