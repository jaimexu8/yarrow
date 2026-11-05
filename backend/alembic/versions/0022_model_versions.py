"""Record OCR model name and version on pages, jobs, and documents (US-85)

Revision ID: 0022
Revises: 0026
Create Date: 2026-11-04 22:00:00.000000

- pages.model_name: the inference model name that produced this page's current
  results. Set only for completed pages.
- pages.model_version: the inference model version that produced this page's
  current results. Set only for completed pages.
- jobs.model_name: the inference model name the run used. If pages report
  different versions, stores the version reported by the most pages, with ties
  broken by the later response.
- jobs.model_version: the inference model version the run used, chosen by the
  same majority rule and tie breaker as jobs.model_name.
- jobs.pipeline_version: the worker package version from worker/pyproject.toml
  used for the processing run.
- documents.model_name: the inference model name copied from the job in the
  final commit of any run that leaves the document completed, or failed with at
  least one completed page.
- documents.model_version: the inference model version copied from the job in
  the final commit of any run that leaves the document completed, or failed with
  at least one completed page.
- documents.pipeline_version: the worker package version copied from the job in
  the final commit of any run that leaves the document completed, or failed with
  at least one completed page.
- documents.processed_at: naive UTC time of the final commit of the run that last
  set the document model fields.
"""

import sqlalchemy as sa

from alembic import op

revision = "0022"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("pages", sa.Column("model_name", sa.String(length=128), nullable=True))
    op.add_column("pages", sa.Column("model_version", sa.String(length=64), nullable=True))
    op.add_column("jobs", sa.Column("model_name", sa.String(length=128), nullable=True))
    op.add_column("jobs", sa.Column("model_version", sa.String(length=64), nullable=True))
    op.add_column("jobs", sa.Column("pipeline_version", sa.String(length=64), nullable=True))
    op.add_column("documents", sa.Column("model_name", sa.String(length=128), nullable=True))
    op.add_column("documents", sa.Column("model_version", sa.String(length=64), nullable=True))
    op.add_column("documents", sa.Column("pipeline_version", sa.String(length=64), nullable=True))
    op.add_column("documents", sa.Column("processed_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("documents", "processed_at")
    op.drop_column("documents", "pipeline_version")
    op.drop_column("documents", "model_version")
    op.drop_column("documents", "model_name")
    op.drop_column("jobs", "pipeline_version")
    op.drop_column("jobs", "model_version")
    op.drop_column("jobs", "model_name")
    op.drop_column("pages", "model_version")
    op.drop_column("pages", "model_name")
