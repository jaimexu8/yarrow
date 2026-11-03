"""add document folders

Revision ID: 0014_folders
Revises: 0012
Create Date: 2026-11-03 00:00:00.000000

"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0014_folders"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "folders",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_folders_owner_id", "folders", ["owner_id"])
    # unique per owner, ignoring case so "Invoices" and "invoices" clash
    op.create_index(
        "uq_folders_owner_lower_name",
        "folders",
        ["owner_id", sa.text("lower(name)")],
        unique=True,
    )
    op.add_column(
        "documents",
        sa.Column("folder_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index("ix_documents_folder_id", "documents", ["folder_id"])
    op.create_foreign_key(
        "fk_documents_folder_id_folders",
        "documents",
        "folders",
        ["folder_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_documents_folder_id_folders", "documents", type_="foreignkey"
    )
    op.drop_index("ix_documents_folder_id", table_name="documents")
    op.drop_column("documents", "folder_id")
    op.drop_index("uq_folders_owner_lower_name", table_name="folders")
    op.drop_index("ix_folders_owner_id", table_name="folders")
    op.drop_table("folders")
