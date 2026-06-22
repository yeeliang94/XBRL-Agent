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
// cell padding / paragraph spacing and mark specific rows for an accountant
// double underline — a GLOBAL default in localStorage (General settings) plus
// a TRANSIENT per-cell override set in the Notes-tab Format tool. Calling the
// decorator with DEFAULT_FORMAT_OPTIONS reproduces the previous hard-coded
// output exactly; the clipboard pinning tests depend on that equivalence.
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
  type BorderStyle,
  type ClipboardFormatOptions,
} from "./clipboardFormat";

// The style strings below used to be module constants. They are now built from
// a `ClipboardFormatOptions` value so the user can configure border / font /
// padding / spacing (global default + per-cell override). Calling with the
// DEFAULT options reproduces the previous hard-coded output exactly — the
// clipboard pinning tests depend on that equivalence.

function _fontCss(opts: ClipboardFormatOptions): string {
  // `10pt` (NOT `10px`): paste targets like M-Tool / Word interpret a bare
  // font size in points.
  return `font-family: Arial, sans-serif; font-size: ${opts.fontSizePt}pt;`;
}

// Grid-line CSS for one cell. "none" emits no border declaration at all so the
// cell pastes borderless; "double" is the accountant double rule; "single" is
// the default 1px grey. Trailing space so it concatenates cleanly before the
// padding declaration (and so "none" leaves the style starting at padding).
function _borderCss(style: BorderStyle): string {
  if (style === "none") return "";
  if (style === "double") return "border: 3px double #999; ";
  return "border: 1px solid #999; ";
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

function _cellStyleBase(opts: ClipboardFormatOptions): string {
  const [padV, padH] = opts.cellPaddingPx;
  return (
    `${_borderCss(opts.borderStyle)}padding: ${padV}px ${padH}px; ` +
    "vertical-align: top; overflow-wrap: break-word; word-break: break-word; " +
    _fontCss(opts)
  );
}

const _CLIPBOARD_HEADER_EXTRA = " background: #f3f4f6; font-weight: 600;";

// Accountant "total" rule: a double bottom border on the cells of a row the
// user marked in the Format tool. Black so it reads as a deliberate underline
// regardless of the grid colour. Appended on top of the cell base style.
const _CLIPBOARD_ROW_UNDERLINE = " border-bottom: 3px double #000;";

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
    _mergeStyle(table, _CLIPBOARD_TABLE_STYLE);
    // Word/Outlook honour the legacy `border` attribute even when CSS
    // is partially stripped on paste. Belt-and-braces — the inline
    // style above does the heavy lifting on web targets.
    if (noBorder) {
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
  // right-aligns. `rowIdx` is the 0-based <tr> position across the whole
  // table (querySelectorAll document order) — the Format tool's row picker
  // numbers rows the same way, so its `rowUnderlines` line up here.
  Array.from(tmp.querySelectorAll("tr")).forEach((row, rowIdx) => {
    const cells = Array.from(row.children).filter(
      (c) => c.tagName === "TD" || c.tagName === "TH",
    );
    // Optional accountant double-underline on this row's cells.
    const underline = opts.rowUnderlines.includes(rowIdx)
      ? _CLIPBOARD_ROW_UNDERLINE
      : "";
    cells.forEach((cell, idx) => {
      const align = shouldRightAlignCell(
        cell.textContent ?? "",
        idx,
        cells.length,
      )
        ? " text-align: right;"
        : " text-align: left;";
      if (cell.tagName === "TH") {
        _mergeStyle(cell, cellBase + _CLIPBOARD_HEADER_EXTRA + align + underline);
      } else {
        _mergeStyle(cell, cellBase + align + underline);
      }
    });
  });

  // Prose: Arial + a bottom margin so non-table cells paste with a
  // consistent face and visible gaps between paragraphs.
  const paragraphStyle = _paragraphStyle(opts);
  const headingStyle = _headingStyle(opts);
  const fontCss = _fontCss(opts);
  for (const p of Array.from(tmp.querySelectorAll("p"))) {
    _mergeStyle(p, paragraphStyle);
  }
  for (const h of Array.from(tmp.querySelectorAll("h3"))) {
    _mergeStyle(h, headingStyle);
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
