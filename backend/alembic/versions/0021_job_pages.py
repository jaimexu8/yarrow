"""job_pages table and pages.error_code, so a job's progress is recorded page
by page while it runs (US-30, US-83)

Revision ID: 0021
Revises: 0019
Create Date: 2026-11-04 12:00:00.000000

job_pages holds one row per page in one job's run (every page, or only the
pages a reprocess targets). The worker inserts them all as queued once the file
is loaded and commits each page's change on its own, so progress is visible
while the job runs. It never holds extraction results; those stay in pages and
are written in the job's final commit, which also copies each page's final
status and error_code back onto its job_pages row.

- id: primary key.
- job_id: the job whose run the page belongs to. Deleted with the job.
- page_number: 1-based page number in the document. Unique per job.
- status: queued, processing, completed, failed or canceled. Queued until the
  worker sends the page to text recognition, processing while it waits for
  the answer, then completed or failed. A cancel turns queued and processing
  rows into canceled.
- error_code: why the page failed (inference_timeout, inference_unavailable,
  inference_error, invalid_page_image or unknown). NULL unless failed.
- error_message: the raw worker text behind error_code. Kept for admins and
  logs only; users are shown the message for error_code instead.
- started_at: when the page was sent to text recognition. NULL while queued.
- finished_at: when the page completed, failed or was canceled.

pages.error_code: the same code as job_pages.error_code, kept on the page after
the run so a failed page still says why. NULL for pages processed before this
migration, which are described with the generic failed-page message.

jobs.current_stage keeps its type, but is limited to the JobStage vocabulary
(queued, downloading, preparing, parsing, saving, finished, failed, canceled).
Jobs that already ended are moved to the stage of their final status, since the
worker used to leave a failed job at "parsing". The downgrade leaves those
values in place: they are valid strings for the old code as well.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0021"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "job_pages",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default="queued",
        ),
        sa.Column("error_code", sa.String(length=32), nullable=True),
        sa.Column("error_message", sa.String(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", "page_number", name="uq_job_pages_job_page"),
        sa.CheckConstraint(
            "status IN ('queued','processing','completed','failed','canceled')",
            name="ck_job_pages_status",
        ),
    )
    op.create_index("ix_job_pages_job_id", "job_pages", ["job_id"])
    op.add_column("pages", sa.Column("error_code", sa.String(length=32), nullable=True))
    op.execute(
        """
        UPDATE jobs
        SET current_stage = CASE status
            WHEN 'completed' THEN 'finished'
            ELSE status
        END
        WHERE status IN ('completed', 'failed', 'canceled')
        """
    )


def downgrade() -> None:
    op.drop_column("pages", "error_code")
    op.drop_index("ix_job_pages_job_id", table_name="job_pages")
    op.drop_table("job_pages")
