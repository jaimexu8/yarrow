"""batches.sealed_at, completed_at, claimed_at and notified_at, so a finished
upload batch is announced to its owner once (US-60)

Revision ID: 0019
Revises: 0018
Create Date: 2026-11-03 12:00:00.000000

- sealed_at: the browser finished sending the run's files. Cleared when
  another file joins (a Retry). A batch whose tab closed mid-upload counts as
  sealed once no file has joined it for BATCH_SEAL_TIMEOUT_SECONDS.
- completed_at: every live (not trashed) document is completed, failed or
  canceled. Cleared when a file joins or a document goes back to the queue.
- claimed_at: a short lease taken by the tab about to show the notification,
  so other tabs skip it. Expires after BATCH_NOTIFICATION_LEASE_SECONDS if the
  tab never acknowledges (lost reply, closed tab), and the batch is offered
  again.
- notified_at: set by the acknowledgement, never cleared, so the same batch
  is never announced twice.

Batches that exist before this migration are treated as sealed (their upload
runs are long over). Those with nothing left to process are also marked
completed and notified, so deploying this does not flood users with
notifications for old uploads; those still running are announced when they
finish.
"""

import sqlalchemy as sa

from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for column in ("sealed_at", "completed_at", "claimed_at", "notified_at"):
        op.add_column("batches", sa.Column(column, sa.DateTime(), nullable=True))
    # The notifier asks for one user's unannounced batches on every check.
    op.create_index(
        "ix_batches_owner_unnotified",
        "batches",
        ["owner_id"],
        postgresql_where=sa.text("notified_at IS NULL"),
    )
    op.execute("UPDATE batches SET sealed_at = timezone('utc', now())")
    # Same rule as the endpoints: only live documents count, and anything
    # that is not a final status still counts as being worked on.
    op.execute(
        """
        UPDATE batches
        SET completed_at = timezone('utc', now()),
            notified_at = timezone('utc', now())
        WHERE NOT EXISTS (
            SELECT 1 FROM documents
            WHERE documents.batch_id = batches.id
              AND documents.deleted_at IS NULL
              AND documents.status NOT IN ('completed', 'failed', 'canceled')
        )
        """
    )


def downgrade() -> None:
    op.drop_index("ix_batches_owner_unnotified", table_name="batches")
    for column in ("notified_at", "claimed_at", "completed_at", "sealed_at"):
        op.drop_column("batches", column)
