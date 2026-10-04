"""users.session_version, so a password reset ends existing sessions

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-26 19:00:00.000000

Every access token records the user's session_version at sign-in. A password
reset increments it, and tokens carrying an older version are refused (US-67).
A counter rather than a timestamp, so there is no same-second window in which
an old token still passes.
"""

import sqlalchemy as sa

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("session_version", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("users", "session_version")
