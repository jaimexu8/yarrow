"""keep the raw OCR text on region_texts for corrections and reverting

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-25 12:00:00.000000

"""

import sqlalchemy as sa

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Null until the first manual correction, then the raw OCR output verbatim;
    # text_content carries the active (corrected) text.
    op.add_column(
        "region_texts", sa.Column("original_text", sa.Text(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("region_texts", "original_text")
