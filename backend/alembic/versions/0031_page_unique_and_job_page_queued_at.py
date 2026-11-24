"""pages unique (document_id, page_number), job_pages.queued_at,
jobs.merge_consecutive_tables, for the Celery-managed per-page pipeline

Revision ID: 0029
Revises: 0028
Create Date: 2026-11-19

- pages: one row per (document, page_number), unique. The per-page worker
  tasks upsert pages, so a duplicate would be a bug. There is no
  pages.created_at to pick a winner, so the migration checks for duplicates
  first and fails with the offending rows instead of deduplicating.
- job_pages.queued_at: when a page was put on the OCR queue, so the sweeper
  can fail pages that sit queued past the queue timeout.
- jobs.merge_consecutive_tables: whether finalize stitches table parts that
  span consecutive pages. The backend never sets it (production is always
  False); the integration test enables it.
"""

import sqlalchemy as sa

from alembic import op

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    duplicates = conn.execute(
        sa.text(
            "SELECT document_id, page_number, count(*) "
            "FROM pages GROUP BY document_id, page_number HAVING count(*) > 1"
        )
    ).all()
    if duplicates:
        listed = "; ".join(
            f"(document_id={row.document_id}, page_number={row.page_number}, "
            f"count={row.count})"
            for row in duplicates
        )
        raise RuntimeError(
            "Cannot add a unique constraint on pages (document_id, page_number): "
            f"duplicate page rows exist and must be removed by hand first: {listed}"
        )

    op.create_unique_constraint(
        "uq_pages_document_page", "pages", ["document_id", "page_number"]
    )
    op.add_column("job_pages", sa.Column("queued_at", sa.DateTime(), nullable=True))
    op.add_column(
        "jobs",
        sa.Column(
            "merge_consecutive_tables",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("jobs", "merge_consecutive_tables")
    op.drop_column("job_pages", "queued_at")
    op.drop_constraint("uq_pages_document_page", "pages", type_="unique")
