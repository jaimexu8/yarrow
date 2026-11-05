"""pages.thumbnail_key, so the viewer can show a small preview of every page
for navigating long documents (US-98)

Revision ID: 0023
Revises: 0021
Create Date: 2026-11-04 18:00:00.000000

- thumbnail_key: storage key of the page's WebP thumbnail,
  documents/{document_id}/thumbnails/{page_number}.webp. The worker builds it
  from the upright page image (160 px wide, aspect ratio kept) and uploads it
  before text recognition starts, then sets this column in the job's final
  commit. NULL when the upload failed and for pages processed before this
  migration; the viewer shows a placeholder for those. A reprocess rewrites
  the same key. The object is deleted together with the document.
"""

import sqlalchemy as sa

from alembic import op

revision = "0023"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("pages", sa.Column("thumbnail_key", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("pages", "thumbnail_key")
