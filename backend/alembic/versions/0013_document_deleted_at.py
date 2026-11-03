"""soft-delete documents with a 7-day trash window

Revision ID: 0013
Revises: 0014_folders
Create Date: 2026-11-03 00:00:00.000000

"""

import sqlalchemy as sa

from alembic import op

revision = "0013"
down_revision = "0014_folders"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Null while the document is in the library. Set on delete; cleared on
    # restore. Expired rows (older than 7 days) are purged in the app.
    op.add_column(
        "documents", sa.Column("deleted_at", sa.DateTime(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("documents", "deleted_at")
