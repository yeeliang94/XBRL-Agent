// Shared numeric-cell detection for table alignment.
//
// Two surfaces decide whether a notes-table cell holds a number and should
// therefore be right-aligned: the clipboard decorator (inline styles, for
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
 *  right-align numeric cells in the review editor. Idempotent — safe to call
 *  after every editor update (numeric cells get the class, the rest have it
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
  for (const row of Array.from(root.querySelectorAll("tr"))) {
    const cells = Array.from(row.children).filter(
      (c) => c.tagName === "TD" || c.tagName === "TH",
    );
    const totalsRow = (row.textContent ?? "").toLowerCase().includes("total");
    cells.forEach((cell, idx) => {
      const right = shouldRightAlignCell(
        cell.textContent ?? "",
        idx,
        cells.length,
      );
      (cell as HTMLElement).classList.toggle(className, right);
      (cell as HTMLElement).classList.toggle(
        totalsClassName,
        totalsRow && right && !ownsBorderFamily(cell),
      );
    });
  }
}
