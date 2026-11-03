"""documents.content_sha256, a per-owner content fingerprint (US-79)

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-28 10:00:00.000000

The upload endpoint already hashes every file while sniffing its type; this
stores that hash so a later upload of identical bytes can be recognised and the
user warned before a second copy is kept. Matching is always restricted to the
caller's own documents, so the index starts with owner_id. It is not unique:
"Upload anyway" deliberately stores another copy with the same hash.

Rows from before this migration keep NULL and never match anything. There is
no backfill: computing the hash means reading every stored original back from
object storage, which a schema migration should not depend on (it may run
where MinIO/S3 is unreachable, and would take as long as downloading the whole
library). Those documents simply never trigger a duplicate warning.
"""

import sqlalchemy as sa

from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "documents", sa.Column("content_sha256", sa.String(length=64), nullable=True)
    )
    op.create_index(
        "ix_documents_owner_content_sha256",
        "documents",
        ["owner_id", "content_sha256"],
    )


def downgrade() -> None:
    op.drop_index("ix_documents_owner_content_sha256", table_name="documents")
    op.drop_column("documents", "content_sha256")
