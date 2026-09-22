"""verification/reset token expiry on users, celery task id on jobs

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-22 16:30:00.000000

Adds the columns the Sprint 1 auth and job stories need:

- users.verification_expires_at   email verification code expiry (US-1)
- users.reset_token,
  users.reset_token_expires_at    password reset flow (US-67)
- jobs.celery_task_id             lets a queued job be revoked (US-42)
"""

import sqlalchemy as sa

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users", sa.Column("verification_expires_at", sa.DateTime(), nullable=True)
    )
    op.add_column("users", sa.Column("reset_token", sa.String(), nullable=True))
    op.add_column(
        "users", sa.Column("reset_token_expires_at", sa.DateTime(), nullable=True)
    )

    op.add_column("jobs", sa.Column("celery_task_id", sa.String(), nullable=True))
    op.create_index(
        op.f("ix_jobs_celery_task_id"), "jobs", ["celery_task_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_jobs_celery_task_id"), table_name="jobs")
    op.drop_column("jobs", "celery_task_id")

    op.drop_column("users", "reset_token_expires_at")
    op.drop_column("users", "reset_token")
    op.drop_column("users", "verification_expires_at")
