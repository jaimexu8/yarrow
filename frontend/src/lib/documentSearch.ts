import type { DocumentTree, RegionNode, TableNode } from './viewer';

export type DocumentHit = {
  regionId: string;
  pageNumber: number;
};

function hasTerm(text: string | null | undefined, term: string): boolean {
  return Boolean(text && text.toLowerCase().includes(term));
}

function cellsInRegion(table: TableNode, regionId: string) {
  const part = table.parts.find((item) => item.region_id === regionId);
  if (!part) return [];
  return table.cells.filter(
    (cell) =>
      cell.region_table_id === part.region_table_id &&
      cell.row >= part.row_start &&
      cell.row <= part.row_end
  );
}

function regionMatches(
  region: RegionNode,
  tables: TableNode[],
  term: string
): boolean {
  if (hasTerm(region.text, term) || hasTerm(region.caption, term)) {
    return true;
  }
  for (const table of tables) {
    if (region.is_first_table_part && hasTerm(table.title, term)) {
      if (table.parts.some((part) => part.region_id === region.id)) {
        return true;
      }
    }
    if (
      cellsInRegion(table, region.id).some((cell) => hasTerm(cell.text, term))
    ) {
      return true;
    }
  }
  return false;
}

/** Hits in reading order. One per region so the existing highlight can jump. */
export function findInDocument(
  tree: DocumentTree,
  rawQuery: string
): DocumentHit[] {
  const term = rawQuery.trim().toLowerCase();
  if (!term) return [];

  const hits: DocumentHit[] = [];
  for (const page of tree.pages) {
    const regions = [...page.regions].sort(
      (a, b) => a.reading_order - b.reading_order
    );
    for (const region of regions) {
      if (regionMatches(region, tree.tables, term)) {
        hits.push({ regionId: region.id, pageNumber: region.page_number });
      }
    }
  }
  return hits;
}
