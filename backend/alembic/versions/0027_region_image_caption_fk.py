"""region image captions reference a figure_title region text

Revision ID: 0027
Revises: 0022
Create Date: 2026-11-13 12:00:00.000000

The free-text region_images.caption column is replaced by caption_id, a
foreign key to the region_texts row of the figure_title region that captions
the image. Existing caption strings are dropped; reprocess a document to
relink its captions.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0027"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "region_images",
        sa.Column("caption_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_region_images_caption_id_region_texts",
        "region_images",
        "region_texts",
        ["caption_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.drop_column("region_images", "caption")


def downgrade() -> None:
    op.add_column("region_images", sa.Column("caption", sa.String(), nullable=True))
    op.execute(
        """
        UPDATE region_images
        SET caption = region_texts.text_content
        FROM region_texts
        WHERE region_images.caption_id = region_texts.id
        """
    )
    op.drop_constraint(
        "fk_region_images_caption_id_region_texts", "region_images", type_="foreignkey"
    )
    op.drop_column("region_images", "caption_id")
