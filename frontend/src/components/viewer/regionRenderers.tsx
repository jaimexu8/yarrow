import type { ReactNode } from 'react';
import { ImageIcon } from 'lucide-react';
import type { RegionNode, TableCellNode, TableNode } from '@/lib/viewer';

/*
 * How each region of the parsed tree is shown in the extracted pane. The
 * rules mirror backend/app/services/markdown.py, so the viewer renders
 * like markdown.
 */

const HEADING_LEVELS: Record<string, 1 | 2 | 3> = {
  doc_title: 1,
  title: 1,
  paragraph_title: 2,
  section_title: 2,
  sub_paragraph_title: 3,
};
const TABLE_LABELS = new Set(['table']);
const TABLE_CAPTION_LABELS = new Set(['table_title', 'table_caption']);
const FIGURE_LABELS = new Set(['image', 'figure', 'chart', 'seal']);
const FORMULA_LABELS = new Set(['formula', 'equation']);
const ARTIFACT_LABELS = new Set(['header', 'footer', 'number', 'page_number']);

export type RegionKind =
  | 'heading'
  | 'paragraph'
  | 'table'
  | 'caption'
  | 'figure'
  | 'formula'
  | 'artifact';

export function regionKind(region: RegionNode): RegionKind {
  const label = region.region_type ?? '';
  if (TABLE_LABELS.has(label)) return 'table';
  if (TABLE_CAPTION_LABELS.has(label)) return 'caption';
  if (FIGURE_LABELS.has(label)) return 'figure';
  if (FORMULA_LABELS.has(label)) return 'formula';
  if (ARTIFACT_LABELS.has(label)) return 'artifact';
  if (label in HEADING_LEVELS) return 'heading';
  return 'paragraph';
}

/** Collapse runs of spaces and tabs, keep line breaks. */
function clean(text: string | null): string {
  return (text ?? '')
    .replace(/\r\n?/g, '\n')
    .replace(/[ \t]+/g, ' ')
    .trim();
}

export type RegionRenderContext = {
  /** The logical table to show at this region, or null for a later part of
   * a table already shown where it started. */
  table: TableNode | null;
  /** The previous region was a table caption, so a table should not repeat
   * its stored title. */
  captioned: boolean;
};

type RegionRenderer = (
  region: RegionNode,
  context: RegionRenderContext
) => ReactNode;

// Headings sit one level below the page's own <h1> (the filename).
const HEADING_TAGS = { 1: 'h2', 2: 'h3', 3: 'h4' } as const;

const BULLET = /^\s*[•▪◦·‣∙*\-–—]\s+/;
const ORDERED = /^\s*(\d{1,3})[.)]\s+/;

/**
 * Paragraphs and lists. The OCR has no list label, so bullet and numbered
 * lines inside a text region become lists, as in the export.
 */
function TextBlock({ text }: { text: string }) {
  type Block =
    | { type: 'p'; lines: string[] }
    | { type: 'ul' | 'ol'; items: string[]; start?: number };
  const blocks: Block[] = [];

  for (const raw of text.split('\n')) {
    const line = raw.trim();
    const last = blocks[blocks.length - 1];
    if (!line) {
      blocks.push({ type: 'p', lines: [] });
      continue;
    }

    const ordered = ORDERED.exec(line);

    // If the line starts with a bullet or an ordered list marker, treat it as a list item.
    if (BULLET.test(line) || ordered) {
      // Determine the type of list (ordered or unordered) and the list item content.
      const type = ordered ? 'ol' : 'ul';
      const item = line.replace(ordered ? ORDERED : BULLET, '');

      // Add the list item to the current list or start a new list if necessary.
      if (last?.type === type) last.items.push(item);
      else
        blocks.push({
          type,
          items: [item],
          start: ordered ? Number(ordered[1]) : undefined,
        });
    } else if (last?.type === 'p') {
      // Append the current line to the last paragraph block.
      last.lines.push(line);
    } else {
      // Start a new paragraph block with the current line.
      blocks.push({ type: 'p', lines: [line] });
    }
  }

  return (
    <>
      {blocks.map((block, index) => {
        if (block.type === 'p') {
          return block.lines.length ? (
            <p key={index}>{block.lines.join(' ')}</p>
          ) : null;
        }
        const List = block.type;
        return (
          <List key={index} start={block.start}>
            {block.items.map((item, itemIndex) => (
              <li key={itemIndex}>{item}</li>
            ))}
          </List>
        );
      })}
    </>
  );
}

/**
 * A logical table from its cells. Unlike the Markdown export, cells that
 * span rows or columns keep their spans. Leading rows made only of header
 * cells form the table head.
 */
function TableBlock({
  table,
  captioned,
}: {
  table: TableNode;
  captioned: boolean;
}) {
  const rows = new Map<number, TableCellNode[]>();

  // Organize the table cells by their row number.
  for (const cell of table.cells) {
    rows.set(cell.row, [...(rows.get(cell.row) ?? []), cell]);
  }

  const rowNumbers = Array.from(
    { length: table.row_count },
    (_, index) => index
  );

  // Determine the number of header rows: consecutive rows at the top where all cells are headers.
  let headRows = 0;
  while (
    headRows < rowNumbers.length &&
    (rows.get(headRows) ?? []).length > 0 &&
    (rows.get(headRows) ?? []).every((cell) => cell.is_header)
  ) {
    headRows += 1;
  }

  // If no header rows are found but there are header cells, treat the first row as the header.
  if (headRows === 0 && !table.cells.some((cell) => cell.is_header)) {
    headRows = Math.min(1, rowNumbers.length);
  }

  const renderRow = (row: number, header: boolean) => (
    <tr key={row}>
      {(rows.get(row) ?? [])
        .sort((a, b) => a.col - b.col)
        .map((cell) => {
          const Cell = header || cell.is_header ? 'th' : 'td';
          return (
            <Cell
              key={`${cell.row}:${cell.col}`}
              rowSpan={cell.row_span > 1 ? cell.row_span : undefined}
              colSpan={cell.col_span > 1 ? cell.col_span : undefined}
              scope={Cell === 'th' && header ? 'col' : undefined}
            >
              {clean(cell.text)}
            </Cell>
          );
        })}
    </tr>
  );

  return (
    <figure className="not-prose my-5">
      {table.title && !captioned && (
        <figcaption className="mb-2 text-sm font-semibold text-slate-900">
          {clean(table.title)}
        </figcaption>
      )}
      <div className="overflow-x-auto rounded-lg border border-slate-200">
        <table className="w-full border-collapse text-left text-sm text-slate-700 [&_td]:border-t [&_td]:border-slate-200 [&_td]:px-3 [&_td]:py-2 [&_td]:align-top [&_th]:bg-slate-50 [&_th]:px-3 [&_th]:py-2 [&_th]:font-semibold [&_th]:text-slate-900">
          {headRows > 0 && (
            <thead>
              {rowNumbers.slice(0, headRows).map((row) => renderRow(row, true))}
            </thead>
          )}
          <tbody>
            {rowNumbers.slice(headRows).map((row) => renderRow(row, false))}
          </tbody>
        </table>
      </div>
    </figure>
  );
}

const RENDERERS: Record<RegionKind, RegionRenderer> = {
  heading: (region) => {
    const text = clean(region.text);
    if (!text) return null;
    const Tag = HEADING_TAGS[HEADING_LEVELS[region.region_type ?? ''] ?? 2];
    return <Tag>{text}</Tag>;
  },
  paragraph: (region) => {
    const text = clean(region.text);
    return text ? <TextBlock text={text} /> : null;
  },
  caption: (region) => {
    const text = clean(region.text);
    return text ? (
      <p>
        <strong>{text}</strong>
      </p>
    ) : null;
  },
  table: (_region, { table, captioned }) =>
    table ? <TableBlock table={table} captioned={captioned} /> : null,
  figure: (region) => {
    const label = (region.region_type ?? 'figure').replace(/_/g, ' ');
    return (
      <div className="not-prose my-5 flex items-center gap-3 rounded-lg border border-dashed border-slate-300 bg-slate-50 px-4 py-3 text-sm text-slate-600">
        <ImageIcon
          aria-hidden="true"
          className="size-4 shrink-0 text-slate-400"
        />
        <span>
          <span className="capitalize">{label}</span>
          {region.caption ? <> — {clean(region.caption)}</> : null}
        </span>
      </div>
    );
  },
  formula: (region) => {
    const text = clean(region.text);
    return text ? (
      <pre className="whitespace-pre-wrap">
        <code>{text}</code>
      </pre>
    ) : null;
  },
  // Running headers, footers and page numbers: kept, as in the viewer's
  // Markdown, but quiet so they do not read as content.
  artifact: (region) => {
    const text = clean(region.text);
    return text ? (
      <p className="not-prose my-2 text-xs text-slate-400">{text}</p>
    ) : null;
  },
};

/**
 * One region of the extracted document. The wrapper carries the region's id
 * and page so features such as highlighting or labeling can locate the block.
 */
export function RegionBlock({
  region,
  context,
}: {
  region: RegionNode;
  context: RegionRenderContext;
}) {
  const content = RENDERERS[regionKind(region)](region, context);
  if (!content) return null;
  return (
    <div
      data-region-id={region.id}
      data-region-type={region.region_type ?? undefined}
      data-page={region.page_number}
    >
      {content}
    </div>
  );
}
