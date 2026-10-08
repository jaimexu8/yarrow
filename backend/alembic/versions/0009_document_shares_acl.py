"""update document_shares for acl and permissions

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-07 19:00:00.000000

"""

import sqlalchemy as sa

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add updated_at column
    op.add_column(
        "document_shares", sa.Column("updated_at", sa.DateTime(), nullable=True)
    )

    # Add unique constraint for ACL
    op.create_unique_constraint(
        "uq_document_share_user",
        "document_shares",
        ["document_id", "shared_with_user_id"],
    )

    # Add indices for fast lookups
    op.create_index(
        "idx_document_shares_document_id",
        "document_shares",
        ["document_id"],
    )
    op.create_index(
        "idx_document_shares_shared_with_user_id",
        "document_shares",
        ["shared_with_user_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_document_shares_shared_with_user_id", table_name="document_shares"
    )
    op.drop_index("idx_document_shares_document_id", table_name="document_shares")
    op.drop_constraint("uq_document_share_user", "document_shares", type_="unique")
    op.drop_column("document_shares", "updated_at")
