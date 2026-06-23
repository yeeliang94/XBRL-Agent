// Step 11 of docs/Archive/PLAN-NOTES-RICH-EDITOR.md — write a cell's HTML to the
// OS clipboard as BOTH `text/html` and `text/plain`.
//
// Why both: the user-tested clipboard round-trip through M-Tool relies on
// `text/html` for rich formatting (tables, bold, lists), but spreadsheet
// apps (Google Sheets, Numbers) only read `text/plain`. Writing both in
// one ClipboardItem means paste targets get the best form available.
//
// The modern path is `navigator.clipboard.write([new ClipboardItem(...)])`;
// older browsers (Firefox pre-116, Safari pre-13.4) did not ship
// ClipboardItem. The fallback uses the legacy `document.execCommand('copy')`
// technique — render the HTML into a hidden contenteditable node, select
// it, fire a copy command, and clean up.
//
// Returns a boolean telling the caller whether the write succeeded so a
// user-facing "Copied" toast is only flipped on after a real success.
//
// **Drift watch (peer-review #11):** the backend has a sibling
// flattener at `notes/html_to_text.py` that renders HTML into Excel
// cells. The two surfaces serve different targets — clipboard vs
// xlsx — so their whitespace handling is intentionally different
// (this JS version collapses inline `\s+`; the Python version
// preserves verbatim). If you touch either flattener, audit the
// other and the fixture files in `tests/test_notes_html_to_text.py`
// / `web/src/__tests__/clipboard.test.ts` to confirm the divergence
// is still deliberate.

// Inline styles injected into table/prose elements before the HTML is handed
// to the clipboard. The DB version of the HTML carries no `style=` attributes
// (the sanitiser strips them); the in-app TipTap editor decorates rendering
// via scoped CSS that does NOT travel with the clipboard payload. Without
// these inline styles, M-Tool / Word / Outlook paste targets render bare
// `<table>`s with no borders, no padding, and no right-alignment for numeric
// columns — the formatting collapse reported on 2026-04-27.
//
// The styles are now built from a `ClipboardFormatOptions` value (see
// `clipboardFormat.ts`) so the user can configure border style / font size /
// cell padding / paragraph spacing as GLOBAL defaults in localStorage (General
// settings). A double underline is document formatting applied and persisted
// from the editor toolbar, so the clipboard preserves it rather than creating
// a one-off copy-only version. Calling the decorator with DEFAULT options
// reproduces the previous hard-coded output exactly; the clipboard pinning
// tests depend on that equivalence.
//
// Default spacing values match `web/src/components/NotesReviewTab.css` so the
// M-Tool paste lays out the same way as the editor preview. The DEFAULT border
// COLOUR intentionally diverges: editor uses `#d1d5db` (a soft modern grey on
// the app's white surface); clipboard uses `#999` because external paste
// targets often render against subtle or off-white backgrounds where
// `#d1d5db` fades out. If you change either colour, update the other side AND
// CLAUDE.md gotcha #16's note on this divergence.
//
// Uniform paste face: M-Tool lays each pasted cell out under a fixed A4 page
// and its default face does not match the filing house style. The default
// Arial 10pt face (configurable) keeps the DB / sanitiser style-free while
// landing the paste in the expected font.
import { shouldRightAlignCell } from "./tableAlign";
import {
  DEFAULT_FORMAT_OPTIONS,
  type ClipboardFormatOptions,
} from "./clipboardFormat";

// The style strings below used to be module constants. They are now built from
// a `ClipboardFormatOptions` value so the user can configure border / font /
// padding / spacing (global defaults). Calling with the DEFAULT options
// reproduces the previous hard-coded output exactly — the clipboard pinning
// tests depend on that equivalence.

function _fontCss(opts: ClipboardFormatOptions): string {
  // `10pt` (NOT `10px`): paste targets like M-Tool / Word interpret a bare
  // font size in points.
  return `font-family: Arial, sans-serif; font-size: ${opts.fontSizePt}pt;`;
}

// Grid-line CSS for one cell. "none" emits no border declaration at all so the
// cell pastes borderless; "double" is the accountant double rule; "single" is a
// 1px grid. Colour comes from the theme (`opts.borderColor`); when unset it
// keeps the historic clipboard `#999` (deliberately darker than the editor's
// grey so the grid survives off-white paste targets — gotcha #16). Trailing
// space so it concatenates cleanly before the padding declaration.
function _borderCss(opts: ClipboardFormatOptions): string {
  if (opts.borderStyle === "none") return "";
  const color = opts.borderColor ?? "#999";
  if (opts.borderStyle === "double") return `border: 3px double ${color}; `;
  return `border: 1px solid ${color}; `;
}

// Width constraint: a faithful transcription of a wide movement table sizes
// to its content and, on paste into M-Tool's fixed A4 page, overflows and
// clips the right-hand columns. `width: 100%` + `table-layout: fixed` pins
// the table to the container width and shares it across columns, so a wide
// table WRAPS its cells to fit the page instead of spilling past it (no
// column is lost; the rows just get taller). `max-width: 100%` is belt-and-
// braces for paste targets that lay the table out in an auto-width box.
// (Independent of the configurable knobs.)
const _CLIPBOARD_TABLE_STYLE =
  "border-collapse: collapse; margin: 8px 0; " +
  "width: 100%; max-width: 100%; table-layout: fixed;";

// When the user has RESIZED the table (an explicit `width: …px`), forcing
// `width: 100%` would override their sizing (CSS last-wins) and distort the
// column widths on paste into Word/M-Tool. In that case we keep the table's
// own width and only add the layout helpers — `table-layout: fixed` so the
// `<col>` widths are authoritative. (notes editor v2 follow-up.)
const _CLIPBOARD_TABLE_STYLE_KEEP_WIDTH =
  "border-collapse: collapse; margin: 8px 0; table-layout: fixed;";

/** True if the table carries its OWN explicit `width` declaration (a resized
 *  table). Property-exact so `min-width` — which TipTap emits on every
 *  un-sized table — does NOT count as a user width. */
function _tableHasExplicitWidth(table: Element): boolean {
  return (table.getAttribute("style") || "")
    .split(";")
    .some((d) => d.slice(0, d.indexOf(":")).trim().toLowerCase() === "width");
}

function _cellStyleBase(opts: ClipboardFormatOptions): string {
  const [padV, padH] = opts.cellPaddingPx;
  return (
    `${_borderCss(opts)}padding: ${padV}px ${padH}px; ` +
    "vertical-align: top; overflow-wrap: break-word; word-break: break-word; " +
    _fontCss(opts)
  );
}

// Header-row extra: fill + bold. Theme-driven (`opts.headerFill` / `headerBold`)
// with the historic defaults (`#f3f4f6`, bold) when unset — so a default copy is
// byte-identical to the old `_CLIPBOARD_HEADER_EXTRA` constant.
function _headerExtra(opts: ClipboardFormatOptions): string {
  const fill = opts.headerFill ?? "#f3f4f6";
  // `<th>` renders bold by default in most paste targets, so `false` must emit
  // an EXPLICIT `font-weight: 400` to override it — an empty string would leave
  // the target's default bold intact (peer-review MEDIUM #3). `undefined`
  // (un-themed) keeps the historic `600` so default output is byte-identical.
  const weight = opts.headerBold === false ? " font-weight: 400;" : " font-weight: 600;";
  return ` background: ${fill};${weight}`;
}

// Prose blocks: the same Arial face plus a bottom margin so consecutive
// paragraphs get breathing space on paste. Without the margin, `<p>` tags
// reach M-Tool with no spacing and the paragraphs jam together (the reported
// "no line break between paragraphs"). Margin mirrors the in-app editor
// (`NotesReviewTab.css` `.tiptap p { margin: 0 0 8px 0 }`).
function _paragraphStyle(opts: ClipboardFormatOptions): string {
  return _fontCss(opts) + ` margin: 0 0 ${opts.paragraphSpacingPx}px 0;`;
}
function _headingStyle(opts: ClipboardFormatOptions): string {
  return _fontCss(opts) + " margin: 12px 0 6px 0; font-weight: 600;";
}

/** Does this block own its left margin through persisted editor indentation? */
function _hasPersistedIndent(el: Element): boolean {
  return (el.getAttribute("style") || "")
    .split(";")
    .some(
      (decl) =>
        decl.slice(0, decl.indexOf(":")).trim().toLowerCase() ===
        "margin-left",
    );
}

/**
 * Merge the paste defaults into a prose block without letting a `margin:`
 * shorthand erase the editor's persisted `margin-left`. CSS shorthands reset
 * every side, so appending `margin: 0 0 8px 0` after `margin-left: 2em` makes
 * Word discard the indentation even though the saved HTML is correct.
 *
 * Keep the historic shorthand for unindented blocks (clipboard output remains
 * byte-compatible); expand only the three non-left sides when indentation is
 * present, letting the persisted left margin win.
 */
function _mergeBlockStyle(
  el: Element,
  defaultStyle: string,
  expandedMarginStyle: string,
): void {
  _mergeStyle(
    el,
    _hasPersistedIndent(el) ? expandedMarginStyle : defaultStyle,
  );
}

// Numeric-cell heuristic + row-label-column rule live in the shared
// tableAlign module so the clipboard paste and the in-app editor preview
// stay in lock-step on which cells right-align. Cells that match get
// `text-align: right` so the paste lines up the way the human-filled
// reference (top of the 2026-04-27 image) does; the first column of a
// multi-column row stays left (the row-label column).

/** Add inline styles to table / th / td so a paste into M-Tool
 *  (or Word / Outlook / Gmail) renders with visible borders,
 *  comfortable cell padding, and right-aligned numeric cells.
 *
 *  Also normalises the whole fragment to an Arial 10pt face and gives
 *  paragraphs / headings a bottom margin so prose pastes with a
 *  consistent font and visible breathing space between paragraphs.
 *
 *  Pure transformation: takes raw editor HTML, returns decorated HTML.
 *  Does NOT mutate any DOM the caller may still be using (parses into
 *  a detached `<div>`). Safe to call on any HTML the sanitiser
 *  produced — prose-only content is wrapped in a font-bearing container
 *  rather than passed through untouched.
 *
 *  Why only at clipboard time and not in the DB / sanitiser:
 *    - The sanitiser's job is to strip authoring-side styling so two
 *      users editing the same cell see the same canonical content.
 *    - The editor's display already styles tables via scoped CSS;
 *      adding inline styles to the DB would be redundant in-editor
 *      and clobber any future "user picks a theme" feature.
 *    - The clipboard is the only surface where styling can't ride on
 *      external CSS, so it's the only surface that needs the inline
 *      version.
 */
export function decorateHtmlForClipboard(
  html: string,
  opts: ClipboardFormatOptions = DEFAULT_FORMAT_OPTIONS,
): string {
  if (!html) return html;
  const tmp = document.createElement("div");
  tmp.innerHTML = html;

  const cellBase = _cellStyleBase(opts);
  const noBorder = opts.borderStyle === "none";

  for (const table of Array.from(tmp.querySelectorAll("table"))) {
    _mergeStyle(
      table,
      _tableHasExplicitWidth(table)
        ? _CLIPBOARD_TABLE_STYLE_KEEP_WIDTH
        : _CLIPBOARD_TABLE_STYLE,
    );
    // Word/Outlook honour the legacy `border` attribute even when CSS
    // is partially stripped on paste. Belt-and-braces — the inline
    // style above does the heavy lifting on web targets.
    //
    // If ANY cell carries its OWN persisted border (notes WYSIWYG), the
    // cells decide their grid — a table-level `border="1"` would redraw a
    // grid over a cell that the user explicitly set borderless (peer-review
    // #3). So suppress the legacy attribute whenever cells own their borders,
    // exactly as we do for the global "no border" option.
    const cellsOwnBorders =
      table.querySelector('td[style*="border"], th[style*="border"]') !== null;
    if (noBorder || cellsOwnBorders) {
      // "No border" must win regardless of what the input table already
      // carried, or a legacy paste target would still draw a grid from a
      // pre-existing attribute (peer-review [MEDIUM]).
      table.removeAttribute("border");
      table.removeAttribute("cellpadding");
      table.removeAttribute("cellspacing");
    } else {
      if (!table.hasAttribute("border")) table.setAttribute("border", "1");
      if (!table.hasAttribute("cellpadding"))
        table.setAttribute("cellpadding", "4");
      if (!table.hasAttribute("cellspacing"))
        table.setAttribute("cellspacing", "0");
    }
  }

  // Walk row by row so the row-label column (first cell of a multi-column
  // row) can stay left-aligned while numeric value columns go right —
  // see shouldRightAlignCell. A bare single-cell numeric row still
  // right-aligns.
  Array.from(tmp.querySelectorAll("tr")).forEach((row) => {
    const cells = Array.from(row.children).filter(
      (c) => c.tagName === "TD" || c.tagName === "TH",
    );
    cells.forEach((cell, idx) => {
      const align = shouldRightAlignCell(
        cell.textContent ?? "",
        idx,
        cells.length,
      )
        ? " text-align: right;"
        : " text-align: left;";
      if (cell.tagName === "TH") {
        _mergeCellStyle(cell, cellBase + _headerExtra(opts) + align);
      } else {
        _mergeCellStyle(cell, cellBase + align);
      }
    });
  });

  // Prose: Arial + a bottom margin so non-table cells paste with a
  // consistent face and visible gaps between paragraphs.
  const paragraphStyle = _paragraphStyle(opts);
  const headingStyle = _headingStyle(opts);
  const fontCss = _fontCss(opts);
  for (const p of Array.from(tmp.querySelectorAll("p"))) {
    _mergeBlockStyle(
      p,
      paragraphStyle,
      fontCss +
        ` margin-top: 0; margin-right: 0; margin-bottom: ${opts.paragraphSpacingPx}px;`,
    );
  }
  for (const h of Array.from(tmp.querySelectorAll("h3"))) {
    _mergeBlockStyle(
      h,
      headingStyle,
      fontCss +
        " margin-top: 12px; margin-right: 0; margin-bottom: 6px; font-weight: 600;",
    );
  }
  for (const list of Array.from(tmp.querySelectorAll("ul, ol, li"))) {
    _mergeStyle(list, fontCss);
  }

  // Carry the font on the wrapping container too, so any element we did not
  // explicitly style (bare <strong>, <em>, loose text) still inherits the
  // chosen face in the paste target rather than the host editor's default.
  _mergeStyle(tmp, fontCss);
  return tmp.outerHTML;
}

/** The CSS "family" a property belongs to, so a persisted longhand
 *  (`border-bottom`) and the decorator's shorthand (`border`) are recognised
 *  as the same concern. A shorthand silently resets all longhands of its
 *  family, so we must never append the decorator's shorthand onto a cell that
 *  already carries that family (peer-review #3). */
function _styleFamily(prop: string): string {
  if (prop === "border" || prop.startsWith("border-")) return "border";
  if (prop === "background" || prop.startsWith("background-")) return "background";
  return prop;
}

/** Property-aware merge for a table CELL: persisted (WYSIWYG) declarations win.
 *  The decorator's defaults (border / padding / font / alignment) are appended
 *  ONLY for properties — or property families (border, background) — the cell
 *  does not already control.
 *
 *  Back-compat is exact: a cell with NO persisted style (every old run, and
 *  every clipboard pinning-test fixture) takes the verbatim set-attribute path
 *  below, byte-identical to the previous concat behaviour. */
function _mergeCellStyle(cell: Element, addition: string): void {
  const existing = cell.getAttribute("style");
  if (!existing) {
    // Legacy path — no persisted style, so the decorator owns the cell fully.
    cell.setAttribute("style", addition);
    return;
  }
  // Persisted declarations come first (they win) and stay verbatim.
  const parts = existing
    .split(";")
    .map((s) => s.trim())
    .filter(Boolean);
  const ownedFamilies = new Set(
    parts
      .map((d) => d.slice(0, d.indexOf(":")).trim().toLowerCase())
      .map(_styleFamily),
  );
  for (const decl of addition.split(";")) {
    const d = decl.trim();
    if (!d) continue;
    const prop = d.slice(0, d.indexOf(":")).trim().toLowerCase();
    const fam = _styleFamily(prop);
    // Skip a decorator default the cell already controls — for border /
    // background that means ANY member of the family; for others, the exact
    // property. (`text-align`, `padding`, `font-*` are independent.)
    if (fam === "border" || fam === "background") {
      if (ownedFamilies.has(fam)) continue;
    } else if (ownedFamilies.has(prop)) {
      continue;
    }
    parts.push(d);
  }
  cell.setAttribute("style", parts.join("; "));
}

/** Append an inline style fragment to a node's existing `style=` attr.
 *  Most editor HTML reaching this function carries no `style=` (the
 *  sanitiser stripped it), but we don't want to clobber any styling a
 *  future code path might add — concatenating preserves both. */
function _mergeStyle(el: Element, addition: string): void {
  const existing = el.getAttribute("style");
  if (!existing) {
    el.setAttribute("style", addition);
    return;
  }
  // Ensure a separator between existing and new declarations.
  const sep = existing.trimEnd().endsWith(";") ? " " : "; ";
  el.setAttribute("style", existing.trimEnd() + sep + addition.trimStart());
}

/** Encode a string as a `text/plain` clipboard variant. This is a
 *  best-effort plaintext rendering — the editor also keeps the exact
 *  HTML in the text/html variant for rich targets.
 *
 *  `textContent` alone drops paragraph breaks, list markers, and
 *  table row separators so plain-text paste targets (Google Sheets,
 *  Numbers, Notes) see mashed-together content. Walk the DOM tree
 *  and emit structural whitespace so the plaintext form is still
 *  readable: `\n\n` between block paragraphs, `- ` / `1. ` prefixes
 *  for list items, and ` | `-separated row/cell text for tables.
 *  Mirrors what `notes/html_to_text.py` does on the backend so the
 *  Excel download and the clipboard plain-text form agree. */
export function htmlToPlaintext(html: string): string {
  const tmp = document.createElement("div");
  tmp.innerHTML = html;
  const out: string[] = [];
  renderNodeToPlaintext(tmp, out, { listStack: [] });
  // Collapse 3+ blank lines back to 2 so repeated `<p>` or nested
  // blocks don't leave gaps the user has to clean up on paste.
  return out.join("").replace(/\n{3,}/g, "\n\n").trim();
}

/** Context threaded through the recursive walker — only tracks the
 *  current list stack so nested `<ol>` numbering doesn't double-count
 *  on a `<ul>` inside an `<ol>`. */
interface PlaintextCtx {
  // Each stack frame = one open list. `type` is "ul" / "ol";
  // `index` is the next item number to emit for ordered lists.
  listStack: Array<{ type: "ul" | "ol"; index: number }>;
}

function renderNodeToPlaintext(
  node: Node,
  out: string[],
  ctx: PlaintextCtx,
): void {
  if (node.nodeType === Node.TEXT_NODE) {
    // Collapse runs of whitespace inside inline text the way an
    // HTML renderer would — preserves single spaces between words
    // while dropping author-side formatting indents.
    const text = (node.textContent ?? "").replace(/\s+/g, " ");
    if (text) out.push(text);
    return;
  }
  if (node.nodeType !== Node.ELEMENT_NODE) return;

  const el = node as Element;
  const tag = el.tagName.toLowerCase();

  switch (tag) {
    case "br":
      out.push("\n");
      return;
    case "p":
    case "div":
    case "h1":
    case "h2":
    case "h3":
    case "h4":
    case "h5":
    case "h6": {
      walkChildren(el, out, ctx);
      out.push("\n\n");
      return;
    }
    case "ul":
    case "ol": {
      ctx.listStack.push({ type: tag, index: 1 });
      walkChildren(el, out, ctx);
      ctx.listStack.pop();
      // Trailing separator so a list followed by a paragraph has a
      // gap. Collapsed back to `\n\n` in the caller's final pass.
      out.push("\n");
      return;
    }
    case "li": {
      const frame = ctx.listStack[ctx.listStack.length - 1];
      const prefix = frame?.type === "ol"
        ? `${frame.index++}. `
        : "- ";
      out.push(prefix);
      walkChildren(el, out, ctx);
      out.push("\n");
      return;
    }
    case "table": {
      walkChildren(el, out, ctx);
      out.push("\n");
      return;
    }
    case "thead":
    case "tbody":
    case "tfoot": {
      walkChildren(el, out, ctx);
      return;
    }
    case "tr": {
      // Render the cells ourselves so we can join with ` | ` and
      // avoid the default inline-concatenation textContent gives us.
      const cells: string[] = [];
      for (const child of Array.from(el.children)) {
        const childTag = child.tagName.toLowerCase();
        if (childTag === "td" || childTag === "th") {
          const sub: string[] = [];
          walkChildren(child, sub, ctx);
          cells.push(sub.join("").trim());
        }
      }
      out.push(cells.join(" | "));
      out.push("\n");
      return;
    }
    // Inline tags — no structural whitespace, just descend into
    // children so their text content bubbles up untouched.
    default:
      walkChildren(el, out, ctx);
      return;
  }
}

function walkChildren(node: Node, out: string[], ctx: PlaintextCtx): void {
  for (const child of Array.from(node.childNodes)) {
    renderNodeToPlaintext(child, out, ctx);
  }
}

/** Copy ``html`` to the clipboard as both text/html and text/plain.
 *
 *  Uses the modern async Clipboard API when available; falls back to the
 *  legacy execCommand pathway when not. Returns true on success so the
 *  caller can flip a "Copied" indicator only after a confirmed write.
 */
export async function copyHtmlAsRichText(
  html: string,
  opts: ClipboardFormatOptions = DEFAULT_FORMAT_OPTIONS,
): Promise<boolean> {
  // Prefer the async Clipboard API. It needs BOTH `navigator.clipboard`
  // and the `ClipboardItem` global — we feature-detect both.
  const hasClipboardItem =
    typeof (globalThis as { ClipboardItem?: unknown }).ClipboardItem !==
    "undefined";
  const nav = globalThis.navigator as Navigator | undefined;
  if (nav?.clipboard?.write && hasClipboardItem) {
    try {
      const plain = htmlToPlaintext(html);
      // Decorate AFTER the plain-text rendering so the plain variant
      // sees the original semantic HTML and produces the same
      // pipe-separated rows as before — the inline-style pass is a
      // visual decoration only and adds nothing to the plaintext form.
      const decorated = decorateHtmlForClipboard(html, opts);
      const ClipboardItemCtor = (
        globalThis as unknown as {
          ClipboardItem: new (items: Record<string, Blob>) => unknown;
        }
      ).ClipboardItem;
      const item = new ClipboardItemCtor({
        "text/html": new Blob([decorated], { type: "text/html" }),
        "text/plain": new Blob([plain], { type: "text/plain" }),
      });
      await nav.clipboard.write([item as unknown as ClipboardItem]);
      return true;
    } catch {
      // Fall through to the legacy path — permission prompts in some
      // browsers reject the write() even when the API is present.
    }
  }

  // Legacy fallback: render the HTML into a hidden contenteditable div
  // (so execCommand('copy') sees a Selection over live DOM, which is
  // what lets the browser preserve the `text/html` format), select it,
  // fire the command, and clean up. Returns false if even that fails.
  try {
    const holder = document.createElement("div");
    holder.contentEditable = "true";
    // Same decoration pass as the modern path so legacy-fallback
    // pastes don't lose table styling.
    holder.innerHTML = decorateHtmlForClipboard(html, opts);
    // Pull off-screen rather than display:none — hidden elements cannot
    // carry a Selection in most browsers.
    holder.style.position = "fixed";
    holder.style.left = "-9999px";
    holder.style.top = "0";
    holder.style.opacity = "0";
    document.body.appendChild(holder);
    try {
      const range = document.createRange();
      range.selectNodeContents(holder);
      const sel = window.getSelection();
      if (!sel) return false;
      sel.removeAllRanges();
      sel.addRange(range);
      const ok = document.execCommand("copy");
      sel.removeAllRanges();
      return ok;
    } finally {
      document.body.removeChild(holder);
    }
  } catch {
    return false;
  }
}
