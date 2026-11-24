"""jobs.skip_text_layer

Revision ID: 0031
Revises: 0030
Create Date: 2026-11-23

- jobs.skip_text_layer: the user asked to reprocess this document without
  reusing its stored text layer. The worker reads the flag from the job row.
"""

import sqlalchemy as sa

from alembic import op

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column(
            "skip_text_layer",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("jobs", "skip_text_layer")
