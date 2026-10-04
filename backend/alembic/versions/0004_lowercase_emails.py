"""store every user email in lowercase

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-26 18:00:00.000000

Registration and login now compare emails in one canonical form
(``app.schemas.normalize_email``: trimmed and lowercased). Rows created before
that kept whatever capitalization was typed, and a case-sensitive lookup would
no longer find them, so they are rewritten here.

Two accounts that differ only by case cannot both survive, and choosing one
automatically could hand a user's documents to someone else. The migration
therefore stops and names them instead, so a person can decide.
"""

import sqlalchemy as sa

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    collisions = conn.execute(
        sa.text(
            "SELECT lower(trim(email)) AS canonical, array_agg(email) AS emails "
            "FROM users GROUP BY lower(trim(email)) HAVING count(*) > 1"
        )
    ).all()
    if collisions:
        listed = "; ".join(f"{row.canonical}: {row.emails}" for row in collisions)
        raise RuntimeError(
            "Cannot lowercase user emails: these accounts differ only by case "
            f"and must be merged or removed by hand first: {listed}"
        )

    op.execute(
        "UPDATE users SET email = lower(trim(email)) WHERE email <> lower(trim(email))"
    )


def downgrade() -> None:
    # The original capitalization is not recorded anywhere, so there is
    # nothing to restore. Lowercase emails remain valid under the old code.
    pass
