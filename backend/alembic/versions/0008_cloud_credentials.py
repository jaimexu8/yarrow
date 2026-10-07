"""create cloud_credentials table

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-07 18:00:00.000000

"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cloud_credentials",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("access_token", sa.String(), nullable=False),
        sa.Column("refresh_token", sa.String(), nullable=True),
        sa.Column("token_expires_at", sa.DateTime(), nullable=True),
        sa.Column("account_email", sa.String(), nullable=True),
        sa.Column("account_name", sa.String(), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "provider", name="uq_user_cloud_provider"),
    )
    op.create_index("idx_cloud_credentials_user_id", "cloud_credentials", ["user_id"])


def downgrade() -> None:
    op.drop_index("idx_cloud_credentials_user_id", table_name="cloud_credentials")
    op.drop_table("cloud_credentials")
