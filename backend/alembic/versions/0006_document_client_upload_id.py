"""documents.client_upload_id, so retrying an upload can't create duplicates

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-27 20:00:00.000000

The client picks an id for each file and sends it with every attempt. If a
document with that id already exists for the owner, the upload returns it
instead of storing the file again (US-7, US-8). Unique per owner; NULL for
uploads that don't send one, which Postgres treats as distinct.
"""

import sqlalchemy as sa

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "documents", sa.Column("client_upload_id", sa.String(), nullable=True)
    )
    op.create_index(
        "ix_documents_owner_client_upload_id",
        "documents",
        ["owner_id", "client_upload_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_documents_owner_client_upload_id", table_name="documents")
    op.drop_column("documents", "client_upload_id")
