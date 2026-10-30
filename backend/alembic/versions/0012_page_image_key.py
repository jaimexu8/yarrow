"""store the upright page image for rotated pages

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-28 00:00:00.000000

"""

import sqlalchemy as sa

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Null unless the page was rotated before OCR; the viewer then shows the
    # upright image from storage instead of the original file. Mirrors
    # RegionImage.image_key.
    op.add_column(
        "pages", sa.Column("page_image_key", sa.String(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("pages", "page_image_key")
