// Shared numeric-cell detection for table alignment.
//
// Two surfaces decide whether a notes-table cell belongs to an amount column
// and should therefore be right-aligned: the clipboard decorator (inline styles, for
// the M-Tool / Word paste — see clipboard.ts) and the in-app review editor
// (a CSS class, see NotesReviewTab). Keeping the heuristic here means both
// agree on what counts as numeric and where the row-label column sits, so
// the preview and the paste line up.
//
// The DB / sanitiser stay style-free (gotcha #16) — alignment is a
// render-time concern applied at each of these two boundaries, never
// persisted into notes_cells.

// Accountant-style numeric cell: thousands-separated values (`1,595`),
// parenthesised negatives (`(95)`), bare dashes used for an empty year
// column (`—` / `–` / `-`), decimals, and a leading minus.
export const NUMERIC_CELL_RE =
  /^\(?\s*-?\s*[\d,]+(?:\.\d+)?\s*\)?$|^[-—–]+$/;

/** True when `text` reads like an accountant-formatted number. */
export function isNumericCellText(text: string): boolean {
  return NUMERIC_CELL_RE.test(text.trim());
}

/** Should a table cell be right-aligned?
 *
 *  Right-align accountant-numeric cells — EXCEPT the first cell of a
 *  multi-column row, which is the row-label column and stays left even
 *  when it reads like a number (e.g. a "2024" period label). A bare
 *  single-cell row (just a number) still right-aligns. This is exactly
 *  "first column left, numeric value columns right" for real disclosure
 *  tables, where the label column is text anyway. */
export function shouldRightAlignCell(
  text: string,
  index: number,
  cellsInRow: number,
): boolean {
  if (index === 0 && cellsInRow > 1) return false;
  return isNumericCellText(text);
}

/** Amount columns in a simple table. Spanning rows do not provide stable
 * column positions, so they neither define nor inherit this fallback. */
export function amountColumns(table: Element): Set<number> {
  const columns = new Set<number>();
  for (const row of Array.from(table.querySelectorAll("tr"))) {
    if (row.closest("table") !== table) continue;
    const cells = Array.from(row.children).filter(
      (cell) => cell.tagName === "TD" || cell.tagName === "TH",
    );
    if (cells.length < 2 || cells.some((cell) =>
      cell.hasAttribute("colspan") || cell.hasAttribute("rowspan"))) continue;
    cells.forEach((cell, index) => {
      if (index > 0 && isNumericCellText(cell.textContent ?? "")) {
        columns.add(index);
      }
    });
  }
  return columns;
}

/** Figures align by their own content. Inferred amount-column alignment applies
 * only to heading rows, so descriptive body text stays left. */
export function shouldRightAlignTableCell(
  cell: Element,
  index: number,
  cells: Element[],
  amountCols: Set<number>,
): boolean {
  if (shouldRightAlignCell(cell.textContent ?? "", index, cells.length)) return true;
  if (index === 0 || !amountCols.has(index) || cells.some((item) =>
    item.hasAttribute("colspan") || item.hasAttribute("rowspan"))) return false;
  const row = cell.parentElement;
  return row?.parentElement?.tagName === "THEAD" || cells.every((item) =>
    item.tagName === "TH");
}

/** Does the cell OWN its border through a persisted inline style? Mirrors the
 *  clipboard/mTool merge rule (`_styleFamily` in clipboard.ts /
 *  notes_decorate.py): any `border` / `border-*` declaration means the cell
 *  decides its whole border family, and theme additions — the totals rule
 *  included — must stand down. Without this the preview could draw a totals
 *  double underline on a cell whose paste/fill output skips it (a cell with
 *  e.g. a persisted `border-top` but no `border-bottom`). */
function ownsBorderFamily(el: Element): boolean {
  return (el.getAttribute("style") ?? "")
    .split(";")
    .some((decl) => {
      const prop = decl.slice(0, decl.indexOf(":")).trim().toLowerCase();
      return prop === "border" || prop.startsWith("border-");
    });
}

/** Toggle `className` on every `<td>`/`<th>` under `root` so a CSS rule can
 *  right-align amount columns in the review editor. Idempotent — safe to call
 *  after every editor update (amount cells get the class, the rest have it
 *  removed). Walks row by row so the row-label column can be exempted.
 *
 *  Also tags the numeric cells of "total" rows with `is-totals-num` so the
 *  theme's totals-double-underline convention can render in the preview —
 *  EXCEPT cells that own their border family (persisted WYSIWYG / sidecar
 *  styles win, exactly as on the clipboard/mTool surfaces). The
 *  `--nt-totals-border` CSS variable (set from the resolved theme only when
 *  `totalsDoubleUnderline` is on) decides whether the rule is visible, so the
 *  tagger needs no theme plumbing and an un-themed editor falls back to the
 *  normal grid border (unchanged look). */
export function tagNumericCells(
  root: ParentNode,
  className = "is-numeric",
  totalsClassName = "is-totals-num",
): void {
  const columnsByTable = new Map<Element, Set<number>>();
  for (const row of Array.from(root.querySelectorAll("tr"))) {
    const cells = Array.from(row.children).filter(
      (c) => c.tagName === "TD" || c.tagName === "TH",
    );
    const totalsRow = (row.textContent ?? "").toLowerCase().includes("total");
    const table = row.closest("table");
    if (table && !columnsByTable.has(table)) {
      columnsByTable.set(table, amountColumns(table));
    }
    const amountCols = (table ? columnsByTable.get(table) : undefined) ??
      new Set<number>();
    cells.forEach((cell, idx) => {
      const right = shouldRightAlignTableCell(
        cell, idx, cells, amountCols,
      );
      (cell as HTMLElement).classList.toggle(className, right);
      (cell as HTMLElement).classList.toggle(
        totalsClassName,
        totalsRow && right && !ownsBorderFamily(cell),
      );
    });
  }
}
