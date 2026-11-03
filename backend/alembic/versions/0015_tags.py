"""per-user document tags

Revision ID: 0015
Revises: 0013
Create Date: 2026-11-03 00:00:00.000000

"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0015"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tags",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tags_owner_id", "tags", ["owner_id"])
    # same name, different owner is allowed; "Invoice" and "invoice" are not.
    op.execute(
        "CREATE UNIQUE INDEX uq_tags_owner_lower_name ON tags (owner_id, lower(name))"
    )
    op.create_table(
        "document_tags",
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tag_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["document_id"], ["documents.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["tag_id"], ["tags.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("document_id", "tag_id"),
    )
    op.create_index(
        "ix_document_tags_tag_id", "document_tags", ["tag_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_document_tags_tag_id", table_name="document_tags")
    op.drop_table("document_tags")
    op.execute("DROP INDEX IF EXISTS uq_tags_owner_lower_name")
    op.drop_index("ix_tags_owner_id", table_name="tags")
    op.drop_table("tags")
