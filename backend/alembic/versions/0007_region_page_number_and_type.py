"""region.page_number and enum for region_type

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-05 19:45:00.000000

"""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add page_number column
    op.add_column("regions", sa.Column("page_number", sa.Integer(), nullable=True))
    
    # Change region_type to enum (PostgreSQL)
    op.execute("CREATE TYPE region_type_enum AS ENUM ('header', 'paragraph', 'figure', 'table')")
    op.execute("ALTER TABLE regions ALTER COLUMN region_type TYPE region_type_enum USING region_type::region_type_enum")


def downgrade() -> None:
    op.execute("ALTER TABLE regions ALTER COLUMN region_type TYPE VARCHAR USING region_type::text")
    op.execute("DROP TYPE region_type_enum")
    op.drop_column("regions", "page_number")
