"""batches table and documents.batch_id, so files uploaded together can be
tracked as one batch (US-31)

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-26 12:00:00.000000

The browser makes one batch id per "Upload" click and sends it with every
file in that run. The first upload request with an unseen id creates the
batch row; later requests reuse it. Documents keep their own jobs; the batch
is only a label for grouping them and reporting progress.

documents.batch_id is nullable: documents uploaded before this migration, and
uploads that don't send a batch id, belong to no batch.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "batches",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.add_column(
        "documents",
        sa.Column(
            "batch_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("batches.id"),
            nullable=True,
        ),
    )
    # The progress query filters by batch; not unique, a batch has many files.
    op.create_index("ix_documents_batch_id", "documents", ["batch_id"])


def downgrade() -> None:
    op.drop_index("ix_documents_batch_id", table_name="documents")
    op.drop_column("documents", "batch_id")
    op.drop_table("batches")
