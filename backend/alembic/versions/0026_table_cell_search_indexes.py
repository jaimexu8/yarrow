"""indexes on the join path from documents to table cells, so search can
match extracted table cell text (US-58)

Revision ID: 0026
Revises: 0025
Create Date: 2026-11-04 12:00:00.000000

No columns are added. Search joins documents -> pages -> regions ->
region_tables -> table_cells for the caller's documents, and none of those
foreign keys was indexed:

- ix_pages_document_id: pages.document_id, the pages of one document.
- ix_regions_page_id: regions.page_id, the regions of one page.
- ix_region_tables_region_id: region_tables.region_id, the table part a table
  region holds.
- ix_region_tables_table_id: region_tables.table_id, every part of one logical
  table, used to find the header cell above a matched cell.
- ix_table_cells_region_table_id: table_cells.region_table_id, the cells of
  one table part.

The models declare the same indexes with index=True, so the two agree.
"""

from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None

_INDEXES = (
    ("ix_table_cells_region_table_id", "table_cells", "region_table_id"),
    ("ix_region_tables_region_id", "region_tables", "region_id"),
    ("ix_region_tables_table_id", "region_tables", "table_id"),
    ("ix_regions_page_id", "regions", "page_id"),
    ("ix_pages_document_id", "pages", "document_id"),
)


def upgrade() -> None:
    for name, table, column in _INDEXES:
        op.create_index(name, table, [column])


def downgrade() -> None:
    for name, table, _column in reversed(_INDEXES):
        op.drop_index(name, table_name=table)
