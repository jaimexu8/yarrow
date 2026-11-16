"""Add the PDF password used to process protected documents."""

import sqlalchemy as sa

from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("pdf_password", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("documents", "pdf_password")
