"""verification attempt and resend limits on users

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-26 12:00:00.000000

Columns backing the US-1 abuse limits:

- users.verification_attempts          wrong guesses against the current code
- users.verification_sent_at           last code sent, for the resend cooldown
- users.verification_send_count        codes sent in the current window
- users.verification_window_started_at start of that one-hour window
"""

import sqlalchemy as sa

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "verification_attempts", sa.Integer(), nullable=False, server_default="0"
        ),
    )
    op.add_column(
        "users", sa.Column("verification_sent_at", sa.DateTime(), nullable=True)
    )
    op.add_column(
        "users",
        sa.Column(
            "verification_send_count", sa.Integer(), nullable=False, server_default="0"
        ),
    )
    op.add_column(
        "users",
        sa.Column("verification_window_started_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "verification_window_started_at")
    op.drop_column("users", "verification_send_count")
    op.drop_column("users", "verification_sent_at")
    op.drop_column("users", "verification_attempts")
