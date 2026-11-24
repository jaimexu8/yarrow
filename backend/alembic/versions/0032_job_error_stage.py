"""jobs.error_stage

Revision ID: 0030
Revises: 0029
Create Date: 2026-11-20

- jobs.error_stage: the pipeline stage in which a job failed, so failures can
  be grouped by stage (downloading, parsing, finalizing, queue_timeout).
"""

import sqlalchemy as sa

from alembic import op

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("error_stage", sa.String(32), nullable=True))


def downgrade() -> None:
    op.drop_column("jobs", "error_stage")
