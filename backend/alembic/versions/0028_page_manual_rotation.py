"""pages.manual_rotation_deg, the user's rotation override for a page

Revision ID: 0028
Revises: 0027
Create Date: 2026-11-10

- manual_rotation_deg: the absolute rotation (0, 90, 180 or 270, clockwise
  from the original page) the user chose for a page, so the page is re-OCRed
  at that orientation instead of the auto-detected one. NULL when no override
  is set and auto-detection decides the page's orientation. Set by the page
  rotation endpoint; the worker re-saves it on the re-created page row, so a
  later whole-document reprocess keeps it.
"""

import sqlalchemy as sa

from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "pages", sa.Column("manual_rotation_deg", sa.Integer(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("pages", "manual_rotation_deg")
