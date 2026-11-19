"""Add the PDF password a job uses to open a protected document."""

import sqlalchemy as sa

from alembic import op

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("password", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("jobs", "password")
