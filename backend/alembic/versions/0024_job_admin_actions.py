"""jobs.canceled_by_id, cancel_reason, retry_of_job_id and requested_by_id, so
admins can force-cancel and retry jobs with a record of who did what (US-75)

Revision ID: 0024
Revises: 0021
Create Date: 2026-11-04 18:00:00.000000

- canceled_by_id: the user who canceled the job. The owner (or an editor) for
  a cancel from the library, the admin for a force-cancel. NULL for jobs that
  were not canceled, were canceled before this migration, or whose canceling
  user was deleted.
- cancel_reason: an optional note an admin gives when force-canceling, at most
  500 characters. NULL for user cancels.
- retry_of_job_id: the failed job an admin retry was created from. NULL for
  every other job, and set to NULL if that job is deleted.
- requested_by_id: the user who caused the job to be queued: the uploader, the
  user who reprocessed the document, or the admin who retried it. NULL for jobs
  queued before this migration or whose requesting user was deleted.

ix_jobs_document_id_created_at serves the "latest job of a document" lookups
made by cancel, retry, reprocess and the admin jobs list.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0024"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column("canceled_by_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column("jobs", sa.Column("cancel_reason", sa.String(length=500), nullable=True))
    op.add_column(
        "jobs",
        sa.Column("retry_of_job_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "jobs",
        sa.Column("requested_by_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_jobs_canceled_by_id_users",
        "jobs",
        "users",
        ["canceled_by_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_jobs_retry_of_job_id_jobs",
        "jobs",
        "jobs",
        ["retry_of_job_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_jobs_requested_by_id_users",
        "jobs",
        "users",
        ["requested_by_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_jobs_document_id_created_at", "jobs", ["document_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_jobs_document_id_created_at", table_name="jobs")
    op.drop_constraint("fk_jobs_requested_by_id_users", "jobs", type_="foreignkey")
    op.drop_constraint("fk_jobs_retry_of_job_id_jobs", "jobs", type_="foreignkey")
    op.drop_constraint("fk_jobs_canceled_by_id_users", "jobs", type_="foreignkey")
    op.drop_column("jobs", "requested_by_id")
    op.drop_column("jobs", "retry_of_job_id")
    op.drop_column("jobs", "cancel_reason")
    op.drop_column("jobs", "canceled_by_id")
