// Notes Review tab — post-run WYSIWYG editor for notes_cells rows.
//
// Covers Steps 9–12 of docs/Archive/PLAN-NOTES-RICH-EDITOR.md:
//
//   9.  Read-only render of every cell's HTML grouped by sheet.
//  10.  Per-cell edit mode with a formatting toolbar and a debounced
//       PATCH save; inline "Saved / Saving / Save failed" status chip.
//  11.  Copy-as-rich-text button that writes both text/html and
//       text/plain to the clipboard so the HTML round-trips to M-Tool.
//  12.  Regenerate-notes button with a confirm dialog when edits exist
//       (driven by /api/runs/{id}/notes_cells/edited_count).
//
// TipTap is the editor (see plan Key Decisions). All component styling
// is inline (gotcha #7); the one exception is the scoped
// NotesReviewTab.css that carries selectors for structural table/list
// rules TipTap's rendered DOM needs (prefix `.notes-review-tab`).
import {
  useEffect,
  useRef,
  useState,
  useCallback,
  useMemo,
} from "react";
import { useEditor, EditorContent } from "@tiptap/react";
import type { Editor } from "@tiptap/react";
import { StarterKit } from "@tiptap/starter-kit";
import { Table } from "@tiptap/extension-table";
import { TableRow } from "@tiptap/extension-table-row";
// Notes editor v2 marks. StarterKit already bundles bold/italic/strike/
// underline, so only the extras are imported here. Color rides on TextStyle
// (a <span style="color">); Highlight is multicolor (<mark style>); TextAlign
// sets text-align on paragraphs/headings. All are pinned to the installed
// TipTap version (3.22.4) to avoid the peer-version skew documented in the
// plan. The backend sanitiser (notes/html_sanitize.py) is widened in lock-step
// to accept exactly the tags/styles these produce.
import { Superscript } from "@tiptap/extension-superscript";
import { Subscript } from "@tiptap/extension-subscript";
import { TextStyle } from "@tiptap/extension-text-style";
import { Color } from "@tiptap/extension-color";
import { Highlight } from "@tiptap/extension-highlight";
import { TextAlign } from "@tiptap/extension-text-align";
import {
  TEXT_COLORS,
  HIGHLIGHT_COLORS,
  type PaletteSwatch,
} from "../lib/notesPalette";
import {
  StyledTableCell,
  StyledTableHeader,
  currentCellAttrs,
  applyCellFill,
  applyCellBorderSide,
  applyCellBorderAll,
  applyCellAlign,
  type CellAlign,
  gridBorderValue,
  DEFAULT_BORDER_COLOR,
  BORDER_NONE,
  FILL_NONE,
  type BorderSide,
} from "../lib/cellFormatting";
import { Indent, indentBlocks, outdentBlocks } from "../lib/notesIndent";
import { pwc } from "../lib/theme";
import { ui, uiClass } from "../lib/uiStyles";
import {
  fetchNotesCells,
  patchNotesCell,
  patchNotesFact,
  parseNumericInput,
  sortSheetsBySlot,
  INVALID_NUMBER,
  NUMERIC_VALUE_COLUMNS,
  type NotesCell,
  type NotesSheet,
} from "../lib/notesCells";
import { copyHtmlAsRichText } from "../lib/clipboard";
import { tagNumericCells } from "../lib/tableAlign";
import { formatGroupedInput } from "../lib/numberFormat";
import {
  loadGlobalFormat,
  type ClipboardFormatOptions,
} from "../lib/clipboardFormat";
import { ClipboardFormatControls } from "./ClipboardFormatControls";
import { notesSheetDisplayName } from "../lib/sheetLabels";
import "./NotesReviewTab.css";

// Debounce window for the PATCH save. Matched to the 1.5s decision in
// the plan (Step 10). Kept in a const so the test file can reference
// the same magic number via `vi.advanceTimersByTimeAsync(1600)`.
const SAVE_DEBOUNCE_MS = 1500;

/** Props for the top-level tab. */
export interface NotesReviewTabProps {
  runId: number;
  /** Called when the user confirms "Regenerate notes" — the parent wires
   *  this to the existing rerun endpoint. Optional so a standalone
   *  render (e.g. in a screenshot test) still works. */
  onRegenerate?: (runId: number) => void;
  /** Sheet to focus when the reviewer picks a notes sub-tab in the
   *  SheetNavigator: that section auto-expands and scrolls into view. null /
   *  undefined = no focus (the default stacked, all-collapsed view). */
  focusSheet?: string | null;
}

/** Editor lifecycle status chip per cell. */
type SaveStatus = "idle" | "dirty" | "saving" | "saved" | "failed";

/** Whether an HTML string carries no real content. An empty notes cell comes
 *  back from the backend as "" (or whitespace), but TipTap normalises empty
 *  content to "<p></p>" on mount — so the mount-time `onUpdate` sees
 *  next="<p></p>" vs saved="" and would schedule a spurious PATCH, flipping
 *  an UNEDITED empty cell to "Saved" (issue 3, 2026-06-21). Treating both
 *  sides as blank-equivalent suppresses that phantom save. */
export function isBlankHtml(html: string | null | undefined): boolean {
  if (!html) return true;
  return (
    html
      .replace(/<p>\s*<\/p>/gi, "")
      .replace(/<br\s*\/?>/gi, "")
      .replace(/&nbsp;/gi, "")
      .replace(/<[^>]+>/g, "")
      .trim() === ""
  );
}

/** Sentinel for `pendingCount` when the edited_count endpoint failed or
 *  was unreachable — we can't determine the overwrite count so the modal
 *  shows a generic "couldn't verify your edits" warning instead of a
 *  specific number. Using -1 rather than a second state field keeps the
 *  existing `pendingCount !== null` modal-open check working unchanged. */
const UNKNOWN_COUNT = -1;

/** The shared TipTap extension stack. Pulled out so every editor
 *  instance uses the same configuration — deviation would mean one
 *  editor could accept a tag the sanitiser strips on the way back.
 *
 *  Peer-review S-8: StarterKit ships `codeBlock`, `code`, `blockquote`,
 *  and `horizontalRule` by default, but `notes/html_sanitize.py`'s
 *  ALLOWED_TAGS whitelist does NOT include `<pre>`, `<code>`,
 *  `<blockquote>`, or `<hr>`. Users pressing Cmd+Shift+C (code block)
 *  or pasting fenced code would produce HTML the backend silently
 *  strips. We disable the mismatched extensions here to keep the editor's
 *  affordances aligned with what the backend will accept. If we want to
 *  support these formats in the future, we extend ALLOWED_TAGS and re-enable
 *  here — both sides in one review. */
const TIPTAP_EXTENSIONS = [
  StarterKit.configure({
    code: false,
    codeBlock: false,
    blockquote: false,
    horizontalRule: false,
  }),
  // Inline marks (v2). Superscript/subscript are mutually exclusive by
  // default (toggling one clears the other), which matches footnote-style use.
  Superscript,
  Subscript,
  // Text colour rides on TextStyle; Color adds the setColor command. Highlight
  // is multicolor so the toolbar can offer a small palette of highlight shades.
  TextStyle,
  Color,
  Highlight.configure({ multicolor: true }),
  // Paragraph/heading alignment. Table-cell alignment is handled separately
  // (a per-cell `textAlign` attribute set from the Table toolbar group), so
  // cells are intentionally NOT in this type list.
  TextAlign.configure({ types: ["heading", "paragraph"] }),
  // Paragraph indentation (custom; no first-party TipTap extension).
  Indent,
  // resizable: true enables drag-to-resize column widths. Widths serialise as
  // a standard `<colgroup><col style="width">` + cell `colwidth` attrs, which
  // the sanitiser accepts and which paste faithfully into Word/Excel.
  Table.configure({ resizable: true }),
  TableRow,
  // Styled variants carry the WYSIWYG fill / per-side border attributes
  // (web/src/lib/cellFormatting.ts) so the accountant's formatting persists.
  StyledTableHeader,
  StyledTableCell,
];

// Canonicalise a notes-HTML string for EQUALITY comparison only (never for
// storage or display). The editor serialises inline styles in the browser's
// form — `rgb(...)`, a trailing `;`, idiosyncratic spacing — while the backend
// sanitiser re-emits them canonically (`prop: value` joined by `; `,
// lowercased, NO trailing `;`). Comparing the two raw reports a difference on
// every colour / highlight / paragraph-alignment save and triggers a needless
// `setContent()` that resets the cursor/selection (peer-review HIGH, 2026-06-23).
// We rewrite each element's `style=` into the sanitiser's canonical shape so
// only MEANINGFUL (structural) differences remain. DOM-based and explicit, so
// it is robust in both the browser and jsdom (jsdom does NOT auto-normalise the
// style attribute on an innerHTML round-trip — verified). Exported for tests.
export function canonicalizeHtmlForCompare(html: string): string {
  const root = document.createElement("div");
  root.innerHTML = html;
  root.querySelectorAll<HTMLElement>("[style]").forEach((node) => {
    const canon = (node.getAttribute("style") || "")
      .split(";")
      .map((d) => d.trim())
      .filter(Boolean)
      .map((d) => {
        const i = d.indexOf(":");
        if (i === -1) return d.toLowerCase();
        return `${d.slice(0, i).trim().toLowerCase()}: ${d
          .slice(i + 1)
          .trim()
          .toLowerCase()}`;
      })
      .join("; ");
    if (canon) node.setAttribute("style", canon);
    else node.removeAttribute("style");
  });
  return root.innerHTML;
}

export function NotesReviewTab({ runId, onRegenerate, focusSheet }: NotesReviewTabProps) {
  // sheets / loading / error are the basic fetch lifecycle. We keep them
  // at the tab level (not in the individual cell editor) so one network
  // failure surfaces in a single banner instead of per-cell flicker.
  const [sheets, setSheets] = useState<NotesSheet[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  // Regenerate-notes confirm modal state. `pendingCount` is populated
  // by the edited_count fetch; a truthy value opens the dialog and
  // stores the message "This will overwrite N edited cells".
  const [pendingCount, setPendingCount] = useState<number | null>(null);

  // Which sheet the navigator has focused. Clicking a nav chip expands +
  // scrolls that sheet (via SheetSection's focus effect). `key` bumps on
  // every click so re-clicking an already-focused-but-manually-collapsed
  // sheet re-opens it. Initialised from the `focusSheet` prop so a deep
  // link / Notes sub-tab pick still auto-opens its section.
  const [active, setActive] = useState<{ sheet: string | null; key: number }>(
    () => ({ sheet: focusSheet ?? null, key: 0 }),
  );
  // Reset the navigator focus when the run changes. `active`'s lazy
  // initializer only runs at mount, so without this a sheet the reviewer
  // focused in run A (via a nav chip) would survive the runId change and
  // auto-expand + scroll a same-named section in run B — mirroring the
  // sheets/loadError reset in the fetch effect below. Seed from focusSheet
  // so a deep-linked / sub-tab-focused run still opens its section.
  useEffect(() => {
    setActive({ sheet: focusSheet ?? null, key: 0 });
    // focusSheet intentionally omitted: a later focusSheet change is handled
    // by the sync effect below; including it here would re-run on every nav
    // chip pick (which routes through setActive, not focusSheet).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId]);
  // Keep in sync if the parent changes focusSheet after mount.
  useEffect(() => {
    if (focusSheet) setActive((a) => ({ sheet: focusSheet, key: a.key + 1 }));
  }, [focusSheet]);

  useEffect(() => {
    // Clear prior-run state synchronously so the user never sees run
    // A's cells rendered while the fetch for run B is still in flight.
    // Without this reset, switching runs would briefly show stale
    // content bound to the NEW runId — any PATCH triggered in that
    // window would write A's content into B's notes_cells row.
    setSheets(null);
    setLoadError(null);
    let cancelled = false;
    fetchNotesCells(runId)
      .then((resp) => {
        // The DB orders cells by (sheet, row) alphabetically, which puts
        // Notes-Listofnotes before Notes-SummaryofAccPol. Reviewers expect
        // MBRS slot order (Corp Info → Acc Policies → List of Notes → …);
        // sortSheetsBySlot enforces that without a backend round-trip.
        if (!cancelled) setSheets(sortSheetsBySlot(resp.sheets));
      })
      .catch((err: Error) => {
        if (!cancelled) setLoadError(err.message);
      });
    return () => {
      cancelled = true;
    };
  }, [runId]);

  const isEmpty = sheets !== null && sheets.length === 0;

  // Regenerate click — pre-fetch edited_count so we only show the confirm
  // modal when there's actually something to overwrite (Step 12).
  //
  // Peer-review [HIGH] #2: previously this handler fell open on non-OK
  // responses and network errors, calling `onRegenerate` silently. That
  // bypassed the overwrite warning precisely when the safety check was
  // unavailable. Now we fail closed: an error opens the confirm modal
  // with copy that signals the safety check didn't run, and regenerate
  // only fires after the user explicitly confirms.
  //
  // `pendingCount` carries the known overwrite count, or the sentinel
  // UNKNOWN_COUNT (-1) when we couldn't determine it. `null` = no modal.
  const handleRegenerateClick = useCallback(async () => {
    try {
      const res = await fetch(
        `/api/runs/${runId}/notes_cells/edited_count`,
      );
      if (!res.ok) {
        // Endpoint reachable but unhappy (5xx, 404 on legacy deploys,
        // etc.) — we can't know whether edits exist, so we must ask.
        setPendingCount(UNKNOWN_COUNT);
        return;
      }
      const body = (await res.json()) as { count: number };
      if (!body.count || body.count <= 0) {
        onRegenerate?.(runId);
        return;
      }
      setPendingCount(body.count);
    } catch {
      // Network error / unreachable backend — ask rather than assume.
      setPendingCount(UNKNOWN_COUNT);
    }
  }, [runId, onRegenerate]);

  return (
    <div className="notes-review-tab" style={styles.root}>
      {/* Regenerate action floats to the right of the section; the
          "NOTES REVIEW" heading lives in the parent (RunDetailView) to
          match the AGENTS / CROSS-CHECKS pattern, so no duplicate
          heading + subtitle here. */}
      <header style={styles.topBar}>
        <button
          type="button"
          className={uiClass.btnGhost}
          style={styles.regenerateButton}
          onClick={handleRegenerateClick}
        >
          Regenerate notes
        </button>
      </header>

      {loadError && (
        <div style={styles.errorBanner} role="alert">
          Failed to load notes: {loadError}
        </div>
      )}

      {sheets === null && !loadError && (
        <p style={styles.dim}>Loading notes…</p>
      )}

      {isEmpty && (
        <p style={styles.dim}>
          No notes content for this run. (Face-statement-only runs skip
          the notes pipeline.)
        </p>
      )}

      {/* Sheet navigator — jump straight to a sheet instead of scrolling
          the whole stack. Only shown when there's more than one sheet to
          move between. Chips double as an at-a-glance index of which
          sheets the run produced and how many rows each holds. */}
      {sheets && sheets.length > 1 && (
        <nav style={styles.sheetNav} aria-label="Jump to notes sheet">
          {sheets.map((sh) => {
            const isActive = active.sheet === sh.sheet;
            return (
              <button
                key={sh.sheet}
                type="button"
                style={isActive ? styles.sheetNavChipActive : styles.sheetNavChip}
                aria-current={isActive ? "true" : undefined}
                onClick={() =>
                  setActive((a) => ({ sheet: sh.sheet, key: a.key + 1 }))
                }
              >
                {notesSheetDisplayName(sh.sheet)}
                <span style={styles.sheetNavChipCount} aria-hidden="true">
                  {sh.rows.length}
                </span>
              </button>
            );
          })}
        </nav>
      )}

      {sheets && sheets.length > 0 && (
        <div style={styles.sheetStack}>
          {sheets.map((sh) => (
            // `runId` in the key forces a full remount of every sheet
            // section when the parent switches to a different run.
            // Without it, React reuses the same SheetSection/CellRow
            // component instances across runs — TipTap's editor state
            // + save refs would carry over from the previous run and
            // leak edits into the wrong run_id on PATCH.
            <SheetSection
              key={`${runId}:${sh.sheet}`}
              runId={runId}
              sheet={sh}
              focus={active.sheet === sh.sheet}
              focusKey={active.key}
            />
          ))}
        </div>
      )}

      {pendingCount !== null && (
        <ConfirmRegenerateModal
          count={pendingCount}
          onCancel={() => setPendingCount(null)}
          onConfirm={() => {
            setPendingCount(null);
            onRegenerate?.(runId);
          }}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sheet section — one heading + a stack of CellRow rows per sheet.
// ---------------------------------------------------------------------------

function SheetSection({
  runId,
  sheet,
  focus = false,
  focusKey = 0,
}: {
  runId: number;
  sheet: NotesSheet;
  /** When true (the reviewer picked this notes sub-tab / nav chip), the
   *  section opens and scrolls into view. */
  focus?: boolean;
  /** Bumps on every nav-chip click so re-selecting an already-focused but
   *  manually-collapsed section re-opens it (focus alone wouldn't change). */
  focusKey?: number;
}) {
  // Collapsed by default so a run with 3-5 sheets doesn't mount every
  // TipTap editor on first paint. Matches the agent-card pattern above
  // in RunDetailView — reviewer clicks the heading to reveal rows. A focused
  // section starts open so the picked note is immediately readable.
  const [expanded, setExpanded] = useState(focus);
  const rowCount = sheet.rows.length;
  const sectionRef = useRef<HTMLElement | null>(null);

  // When this section becomes the focused one (sub-tab click), expand it and
  // bring it into view. Keyed on `focus` so switching between sub-tabs
  // re-scrolls; the manual heading toggle stays independent.
  useEffect(() => {
    if (!focus) return;
    setExpanded(true);
    sectionRef.current?.scrollIntoView?.({ block: "start", behavior: "smooth" });
  }, [focus, focusKey]);

  return (
    <section
      ref={sectionRef}
      style={{
        ...styles.sheetSection,
        // Orange left accent when open draws the eye to the active section
        // and visually separates an expanded sheet from its collapsed peers.
        borderLeftColor: expanded ? pwc.orange500 : pwc.grey300,
      }}
    >
      {/* Button-inside-h4 keeps the heading role so
          getByRole("heading", { level: 4, name }) still works while
          letting the whole header act as the toggle. */}
      <h4 style={styles.sheetHeadingWrap}>
        <button
          type="button"
          onClick={() => setExpanded((prev) => !prev)}
          aria-expanded={expanded}
          style={styles.sheetHeadingButton}
        >
          <span style={styles.sheetChevron} aria-hidden="true">
            {expanded ? "▾" : "▸"}
          </span>
          <span
            style={styles.sheetHeadingText}
            data-testid="sheet-title"
            title={sheet.sheet}
          >
            {notesSheetDisplayName(sheet.sheet)}
          </span>
          {/* Row count is visual-only metadata; aria-hidden keeps the
              heading's accessible name equal to the sheet title so
              getByRole("heading", { level: 4, name: "Notes-…" })
              stays exact. */}
          <span style={styles.sheetRowCount} aria-hidden="true">
            {rowCount} {rowCount === 1 ? "row" : "rows"}
          </span>
        </button>
      </h4>
      {expanded && (
        <div style={styles.rowStack}>
          {sheet.rows.map((cell) =>
            // Numeric notes (sheets 13/14) carry multi-column values, not
            // HTML prose — they get value inputs wired to the facts API
            // instead of a TipTap editor (PLAN-notes-template-registry).
            cell.kind === "numeric" ? (
              <NumericCellRow
                key={`${runId}:${sheet.sheet}:${cell.row}`}
                runId={runId}
                cell={cell}
              />
            ) : (
              // Include `runId` in the CellRow key as well — belt-and-
              // braces after the parent sheet remount. Ensures TipTap
              // editor instances never carry state across a runId change.
              <CellRow
                key={`${runId}:${sheet.sheet}:${cell.row}`}
                runId={runId}
                sheet={sheet.sheet}
                cell={cell}
              />
            ),
          )}
        </div>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------
// Cell row — label + evidence on the left, editor + actions on the right.
// ---------------------------------------------------------------------------

// Build a short text preview per table row so the Format tool's underline
// picker can list them. Enumerates <tr> across all tables in document order —
// the SAME order decorateHtmlForClipboard walks — so a picked row's index maps
// straight to `rowUnderlines`. Returns [] when the cell has no table.
function extractTableRowPreviews(html: string): string[] {
  if (!html) return [];
  const tmp = document.createElement("div");
  tmp.innerHTML = html;
  return Array.from(tmp.querySelectorAll("tr")).map((row) => {
    const cells = Array.from(row.children)
      .filter((c) => c.tagName === "TD" || c.tagName === "TH")
      .map((c) => (c.textContent ?? "").trim());
    const text = cells.join(" | ").trim();
    // Cap the preview so a long policy row doesn't blow out the popover.
    return text.length > 60 ? text.slice(0, 57) + "…" : text || "(empty row)";
  });
}

function CellRow({
  runId,
  sheet,
  cell,
}: {
  runId: number;
  sheet: string;
  cell: NotesCell;
}) {
  const [editable, setEditable] = useState(false);
  const [status, setStatus] = useState<SaveStatus>("idle");
  const [copiedAt, setCopiedAt] = useState<number | null>(null);
  // Per-cell paste-format tool. `formatOpen` shows/hides the popover (like the
  // Edit toggle); `formatOpts` is the TRANSIENT override Copy uses for this
  // cell. It is re-seeded from the saved global default every time the popover
  // opens, so it never persists and never leaks between cells (gotcha #16 — the
  // store stays style-free; this lives only at copy time).
  const [formatOpen, setFormatOpen] = useState(false);
  const [formatOpts, setFormatOpts] =
    useState<ClipboardFormatOptions>(loadGlobalFormat);
  // (Notes editor v2) The sanitiser-warning panel was removed: it listed
  // developer-phrased removals on every save and a paste from Excel/Word
  // produced a wall of them, which read as "something broke" when nothing
  // did. The backend still sanitises (and still returns the list for logs);
  // we just no longer surface it in the UI. Dangerous markup is dropped
  // silently and safely.
  // Keep a mutable ref to the current HTML so the debounced saver reads
  // the latest value without resubscribing every keystroke.
  const liveHtmlRef = useRef<string>(cell.html);
  const savedHtmlRef = useRef<string>(cell.html);
  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Monotonic sequence counter for save requests. Each scheduled PATCH
  // captures its own `seq` at fire time; when the response returns,
  // it's ignored unless it matches `latestSaveSeqRef`. Guards against
  // HTTP/2 response reordering: an older in-flight PATCH can land
  // AFTER a newer one and would otherwise overwrite the saved-status
  // ref with stale content.
  const saveSeqRef = useRef<number>(0);
  const latestSaveSeqRef = useRef<number>(0);
  // Per-cell save serialisation (peer-review #5). The seq counter above only
  // drops stale RESPONSES — it does not stop two PATCHes being in flight at
  // once, so the older one's DB WRITE can still land last and overwrite the
  // newer HTML. WYSIWYG formatting clicks make rapid consecutive mutations
  // likelier, so we keep at most ONE PATCH in flight per cell: if a save
  // fires while one is running, we flag a pending save and run it when the
  // in-flight one resolves. This also hardens plain typing.
  const saveInFlightRef = useRef(false);
  const savePendingRef = useRef(false);
  // Tracks whether this CellRow is still mounted so async PATCH
  // completions can bail out when the user has already navigated away
  // (peer-review [MEDIUM]). React 18 drops state updates on unmounted
  // components silently, but we still want to avoid the dangling
  // PATCH from racing against a fresh mount of the same cell.
  const isMountedRef = useRef(true);
  useEffect(() => {
    // Flush any pending debounced save on unmount (peer-review [MEDIUM] #3).
    //
    // Earlier behaviour: the unmount effect cleared the debounce timer
    // without firing the save, so a user who edited a cell and navigated
    // Back within 1.5s silently lost their change. Now we fire the PATCH
    // synchronously with `keepalive: true` so the request survives
    // component teardown / page navigation.
    //
    // `isMountedRef = false` still stops any in-flight fetch responses
    // from updating state on an unmounted CellRow (see setStatus guards
    // in scheduleSave); the keepalive flush is fire-and-forget so we
    // don't care about its response here.
    return () => {
      isMountedRef.current = false;
      if (saveTimerRef.current) {
        clearTimeout(saveTimerRef.current);
        saveTimerRef.current = null;
        // Only flush if the live HTML diverges from the last persisted
        // form — skips a no-op PATCH when the debounce was armed by a
        // keystroke that was then reverted before unmount.
        if (liveHtmlRef.current !== savedHtmlRef.current) {
          // Peer-review I-4: browsers cap keepalive fetch bodies at 64KB.
          // A payload over that silently fails (the promise rejects and
          // our catch swallows it), so the user's edit disappears. Skip
          // the keepalive entirely over the budget and surface the miss
          // via console.warn so the pattern is visible in logs. Ops can
          // then guide users toward a shorter edit or a deliberate
          // Done-click (which goes through the normal debounced path
          // that isn't body-capped).
          const body = JSON.stringify({ html: liveHtmlRef.current });
          const KEEPALIVE_BUDGET = 60_000;
          if (body.length > KEEPALIVE_BUDGET) {
            console.warn(
              `[NotesReviewTab] Skipping keepalive flush for ${sheet}!${cell.row}: ` +
              `body size ${body.length} exceeds 60KB keepalive budget. ` +
              `The user's last edit was NOT saved on unmount. Encourage ` +
              `the user to click Done before navigating when cells are ` +
              `this large, or to save more frequently.`,
            );
          } else {
            try {
              fetch(
                `/api/runs/${runId}/notes_cells/${encodeURIComponent(sheet)}/${cell.row}`,
                {
                  method: "PATCH",
                  headers: { "Content-Type": "application/json" },
                  body,
                  keepalive: true,
                },
              );
            } catch {
              // Synchronous rejection (rare — some browsers throw on
              // too-large bodies even under our 60KB cap, or when the
              // network is offline). Nothing we can do at teardown time.
            }
          }
        }
      }
    };
    // runId/sheet/cell.row are captured for the flush URL; if they change
    // React has already torn down this CellRow, so the cleanup sees the
    // correct coordinates for the row whose edits need flushing.
  }, [runId, sheet, cell.row]);

  const editor = useEditor({
    extensions: TIPTAP_EXTENSIONS,
    content: cell.html,
    editable,
    // TipTap v2.6+ prints a warning without this flag in SSR/jsdom;
    // the render happens synchronously on mount so tests can assert
    // DOM contents on the next microtask.
    immediatelyRender: true,
    // TipTap v3 flipped the default: useEditor no longer re-renders the
    // component on every transaction (selection included). Without this the
    // selection-based TableFormatBar — and the existing toolbar's active-
    // state highlights — go stale when the cursor moves between prose and a
    // table without a document edit (peer-review #1). Re-rendering on
    // transaction only affects the FOCUSED cell's editor (others receive no
    // transactions), so the cost is bounded.
    shouldRerenderOnTransaction: true,
    onUpdate: ({ editor: ed }) => {
      const nextHtml = ed.getHTML();
      liveHtmlRef.current = nextHtml;
      // Guard against TipTap's initial setContent-driven `onUpdate`
      // during mount (which would otherwise schedule a spurious PATCH
      // for every cell even though the user never touched it). We only
      // schedule a save when the content actually diverged from the
      // last persisted HTML.
      if (nextHtml === savedHtmlRef.current) return;
      // Suppress the phantom save when both the new and last-persisted HTML
      // are blank — TipTap normalises an empty cell ("") to "<p></p>" on
      // mount, which would otherwise mark an untouched empty cell dirty and
      // flip it to "Saved" (issue 3). Real typing produces non-blank HTML.
      if (isBlankHtml(nextHtml) && isBlankHtml(savedHtmlRef.current)) return;
      setStatus("dirty");
      scheduleSave();
    },
  });

  // Keep TipTap's internal editable flag in sync with our React state.
  // useEditor's `editable` option only runs on mount.
  useEffect(() => {
    if (editor) editor.setEditable(editable);
  }, [editor, editable]);

  // Defensive sync for `cell.html` prop changes. `useEditor({ content })`
  // only consumes `content` on mount, so a subsequent parent refetch
  // (say a future inline-regenerate, or a parent-driven refresh) that
  // delivers a new HTML payload for the SAME key would otherwise leave
  // the editor showing stale content. Latent today because runId is in
  // the key (cross-run switches already remount the component), but we
  // cover the same-runId refetch case so a future caller can't trip
  // into a stale-editor bug.
  useEffect(() => {
    if (!editor) return;
    if (editor.getHTML() === cell.html) return;
    editor.commands.setContent(cell.html, { emitUpdate: false });
    liveHtmlRef.current = cell.html;
    savedHtmlRef.current = cell.html;
  }, [editor, cell.html]);

  // Right-align numeric table cells in the rendered editor so the review
  // preview matches the clipboard / M-Tool paste. tagNumericCells toggles
  // the `.is-numeric` class on numeric <td>/<th> (CSS does the alignment).
  // Runs once after mount/content-set and re-runs on every edit — a
  // ProseMirror transaction can recreate cell DOM and drop the class, so
  // we re-tag on `update`. Display-only: classList changes never reach
  // getHTML(), so copy / PATCH are unaffected (gotcha #16 — store stays
  // style-free).
  useEffect(() => {
    if (!editor) return;
    const apply = () => tagNumericCells(editor.view.dom);
    apply();
    editor.on("update", apply);
    return () => {
      editor.off("update", apply);
    };
  }, [editor, cell.html]);

  const scheduleSave = useCallback(() => {
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    saveTimerRef.current = setTimeout(async () => {
      // Serialise per cell: if a PATCH is already in flight, don't start a
      // second one — flag a pending save that the in-flight one's `finally`
      // will run once it resolves (peer-review #5). This keeps the DB write
      // order monotonic so an older request can't land last and overwrite
      // newer HTML.
      if (saveInFlightRef.current) {
        savePendingRef.current = true;
        return;
      }
      saveInFlightRef.current = true;
      setStatus("saving");
      const attempted = liveHtmlRef.current;
      // Claim a fresh sequence number for this PATCH. `latestSaveSeqRef`
      // tracks the max-seen — if a newer scheduleSave fires while we're
      // in flight, its response arrives with a higher seq and the
      // result-handling below drops any stale responses.
      saveSeqRef.current += 1;
      const mySeq = saveSeqRef.current;
      latestSaveSeqRef.current = mySeq;
      try {
        const updated = await patchNotesCell(
          runId, sheet, cell.row, attempted,
        );
        if (!isMountedRef.current) return;
        if (mySeq !== latestSaveSeqRef.current) {
          // A newer save was scheduled after this one started — drop
          // our response so its (possibly stale) html doesn't
          // overwrite savedHtmlRef or flip the status banner off
          // while the newer PATCH is still running.
          return;
        }
        // Has the user typed MORE since this PATCH was sent? Compare the
        // current live HTML against what THIS request actually carried
        // (`attempted`). If they differ, the editor already shows newer
        // content and reconciling this older server form would clobber it
        // (peer-review #2 — covers BOTH the "pending already set" case and
        // the "PATCH returned during the next edit's debounce window" case
        // that the savePendingRef guard alone missed).
        const isStale = liveHtmlRef.current !== attempted;
        if (isStale) {
          // Newer edits exist; record the server form so the coalesced save
          // reconciles later. Don't touch the editor (it shows newer content).
          savedHtmlRef.current = updated.html;
        } else {
          // Reconcile the server-sanitised form back into the editor
          // (peer-review [HIGH]). Without this, the Copy button would
          // continue to emit the user's raw markup even though the server has
          // a cleaned version. Compare STYLE-CANONICALISED HTML so a
          // cosmetic-only diff (the browser's `rgb()` / trailing `;` that the
          // sanitiser strips) does NOT fire a setContent() that blips the
          // cursor after every colour/highlight/alignment save (peer-review
          // HIGH, 2026-06-23). Only a MEANINGFUL (structural) change reconciles.
          if (
            editor &&
            canonicalizeHtmlForCompare(editor.getHTML()) !==
              canonicalizeHtmlForCompare(updated.html)
          ) {
            const sel = editor.state.selection;
            editor.commands.setContent(updated.html, { emitUpdate: false });
            // Best-effort selection restore. ProseMirror clamps out-of-range
            // positions so a shorter doc after sanitisation won't throw.
            try {
              editor.commands.setTextSelection({ from: sel.from, to: sel.to });
            } catch {
              /* positions invalid after sanitisation — harmless */
            }
          }
          // Track the persisted content in the EDITOR's OWN serialisation form
          // (not the raw server string). The editor re-serialises styles into
          // the browser's form, which the sanitiser doesn't emit; storing the
          // server string here would defeat the onUpdate dirty-guard's cheap
          // `===` and mark every styled cell permanently dirty.
          const settled = editor ? editor.getHTML() : updated.html;
          liveHtmlRef.current = settled;
          savedHtmlRef.current = settled;
        }
        // Only show "Saved" when this response reflects the latest content.
        // If the user has typed newer text (isStale), the coalesced save is
        // still pending, so keep the cell marked dirty.
        setStatus(isStale ? "dirty" : "saved");
      } catch {
        if (!isMountedRef.current) return;
        if (mySeq !== latestSaveSeqRef.current) {
          // Similar guard for failures: an older PATCH failing after
          // a newer PATCH succeeded should not flip the cell back to
          // "Save failed". The newer response owns the badge.
          return;
        }
        // Leave savedHtmlRef unchanged so the "dirty vs saved" guard
        // above keeps treating this content as unsaved — the next real
        // keystroke will reschedule.
        setStatus("failed");
      } finally {
        // Release the in-flight lock and, if an edit arrived mid-flight,
        // run the coalesced save now (re-debounced) with the latest HTML.
        saveInFlightRef.current = false;
        if (savePendingRef.current) {
          savePendingRef.current = false;
          scheduleSave();
        }
      }
    }, SAVE_DEBOUNCE_MS);
  }, [runId, sheet, cell.row]);

  // Test hook: the jsdom harness cannot easily drive ProseMirror's
  // internal keydown sequence, so the component listens for a
  // CustomEvent that carries the new HTML and feeds it through the
  // same onUpdate path. Only fires in tests — real users never dispatch
  // `notes-review-test-edit`.
  const wrapperRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const node = wrapperRef.current;
    if (!node) return;
    const handler = (ev: Event) => {
      const ce = ev as CustomEvent<{ html: string }>;
      if (!editor) return;
      editor.commands.setContent(ce.detail.html, { emitUpdate: true });
    };
    node.addEventListener("notes-review-test-edit", handler);
    return () => node.removeEventListener("notes-review-test-edit", handler);
  }, [editor]);

  // Toolbar Copy ALWAYS uses the saved global default, read at click time.
  // The per-cell tweak is a one-off applied only via the popover's own copy
  // button — so a tweak (or a closed popover) never silently changes what the
  // plain Copy button does (peer-review [MEDIUM]).
  const handleCopy = useCallback(async () => {
    const html = editor?.getHTML() ?? cell.html;
    const ok = await copyHtmlAsRichText(html, loadGlobalFormat());
    if (ok) setCopiedAt(Date.now());
  }, [editor, cell.html]);

  // Popover "Copy with this format": the transient per-cell override, applied
  // to this single copy. `formatOpts` is re-seeded from the global default each
  // time the popover opens, so it is genuinely one-off.
  const handleCopyWithFormat = useCallback(async () => {
    const html = editor?.getHTML() ?? cell.html;
    const ok = await copyHtmlAsRichText(html, formatOpts);
    if (ok) setCopiedAt(Date.now());
  }, [editor, cell.html, formatOpts]);

  // Open the Format popover, re-seeding the transient options from the saved
  // global default so each session of the tool starts from the user's default
  // (and a previous tweak doesn't silently carry over). Editing and formatting
  // are mutually exclusive: opening Format leaves edit mode so the table can't
  // change underneath the row picker (peer-review [LOW] — stale row indices).
  const toggleFormat = useCallback(() => {
    setFormatOpen((open) => {
      if (!open) {
        setFormatOpts(loadGlobalFormat());
        setEditable(false);
      }
      return !open;
    });
  }, []);

  // Row previews for the underline picker — one entry per <tr> across the
  // cell's table(s), in the same document order decorateHtmlForClipboard uses,
  // so the 0-based index here lines up with `rowUnderlines` there.
  const tableRows = useMemo(
    () => extractTableRowPreviews(editor?.getHTML() ?? cell.html),
    // Recompute when the popover opens (cheap; the editor HTML is stable while
    // the popover is up since editing is a separate mode).
    [editor, cell.html, formatOpen],
  );

  const toggleRowUnderline = useCallback((rowIdx: number) => {
    setFormatOpts((prev) => {
      const has = prev.rowUnderlines.includes(rowIdx);
      return {
        ...prev,
        rowUnderlines: has
          ? prev.rowUnderlines.filter((i) => i !== rowIdx)
          : [...prev.rowUnderlines, rowIdx],
      };
    });
  }, []);

  // Auto-dismiss the "Copied" pill after 2 seconds so the UI goes back
  // to a neutral state without the user clicking anywhere.
  useEffect(() => {
    if (copiedAt === null) return;
    const t = setTimeout(() => setCopiedAt(null), 2000);
    return () => clearTimeout(t);
  }, [copiedAt]);

  return (
    <div data-testid="notes-review-row" style={styles.cellRow}>
      <aside style={styles.cellLeft}>
        <div style={styles.cellLabel}>{cell.label}</div>
        <div style={styles.cellRowNum}>Row {cell.row}</div>
        {cell.evidence && (
          <div
            data-testid="notes-review-evidence"
            style={styles.evidenceBlock}
            title="Evidence column — read-only"
          >
            <span style={styles.evidenceLabel}>Evidence</span>
            <span style={styles.evidenceText}>{cell.evidence}</span>
          </div>
        )}
      </aside>

      <div style={styles.cellRight} ref={wrapperRef}>
        <div style={styles.cellToolbar}>
          <div style={styles.cellToolbarSpacer} />
          <SaveStatusBadge status={status} />
          {copiedAt !== null && (
            <span style={styles.copiedChip}>Copied</span>
          )}
          <button
            type="button"
            style={styles.smallButton}
            onClick={() =>
              setEditable((v) => {
                // Entering edit mode closes the Format popover (mutually
                // exclusive — see toggleFormat).
                if (!v) setFormatOpen(false);
                return !v;
              })
            }
          >
            {editable ? "Done" : "Edit"}
          </button>
          <button
            type="button"
            style={{
              ...styles.smallButton,
              ...(formatOpen ? styles.smallButtonActive : null),
            }}
            aria-expanded={formatOpen}
            onClick={toggleFormat}
          >
            Format
          </button>
          <button
            type="button"
            style={styles.smallButton}
            onClick={handleCopy}
          >
            Copy
          </button>
        </div>
        {editable && editor && <EditorToolbar editor={editor} />}
        {formatOpen && (
          <FormatPopover
            options={formatOpts}
            onChange={setFormatOpts}
            tableRows={tableRows}
            onToggleRowUnderline={toggleRowUnderline}
            onCopy={() => {
              void handleCopyWithFormat();
            }}
          />
        )}
        <div data-testid="notes-review-editor">
          <EditorContent editor={editor} />
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Numeric cell row — label on the left, one value input per column on the
// right (Company: CY/PY; Group: Group CY/PY + Company CY/PY). Numeric notes
// live in the canonical fact store, so edits PATCH the facts API
// (PLAN-notes-template-registry Track B) rather than the prose notes_cells.
// ---------------------------------------------------------------------------

function NumericCellRow({ runId, cell }: { runId: number; cell: NotesCell }) {
  const values = cell.values ?? {};
  // Only render the columns this filing level actually uses, in a stable
  // canonical order (NUMERIC_VALUE_COLUMNS insertion order).
  const columns = Object.keys(NUMERIC_VALUE_COLUMNS).filter(
    (k) => k in values,
  );

  // Local draft strings keyed by column so typing doesn't fight the number
  // round-trip; seeded from the server values.
  const [drafts, setDrafts] = useState<Record<string, string>>(() => {
    const init: Record<string, string> = {};
    for (const k of columns) {
      const v = values[k];
      init[k] = v === null || v === undefined ? "" : String(v);
    }
    return init;
  });
  const [status, setStatus] = useState<SaveStatus>("idle");
  // Which column input currently has focus. Drives the "grouped at rest, raw
  // while focused" display below — the face-statement value inputs already do
  // this (ConceptsPage, issue 4) and the numeric notes rows were missing it,
  // so notes values showed `1595` where the face sheets showed `1,595`.
  const [focusedKey, setFocusedKey] = useState<string | null>(null);

  const saveColumn = useCallback(
    async (key: string) => {
      if (!cell.concept_uuid) return; // unmappable row — nothing to write
      const original = values[key];
      // Accountant-aware parse: "1,234" / "(95)" resolve instead of failing.
      const parsed = parseNumericInput(drafts[key] ?? "");
      if (parsed === INVALID_NUMBER) {
        setStatus("failed");
        return;
      }
      // Skip the network round-trip when the value is unchanged.
      if ((original ?? null) === (parsed ?? null)) return;
      const { period, entity_scope } = NUMERIC_VALUE_COLUMNS[key];
      setStatus("saving");
      try {
        await patchNotesFact(
          runId,
          cell.concept_uuid,
          parsed,
          period,
          entity_scope,
        );
        // Reflect the saved value locally so a re-blur doesn't re-send, and
        // normalise the draft to the canonical form ("(95)" → "-95") so the
        // user sees exactly what was stored.
        values[key] = parsed;
        setDrafts((d) => ({
          ...d,
          [key]: parsed === null ? "" : String(parsed),
        }));
        setStatus("saved");
      } catch {
        setStatus("failed");
      }
    },
    [cell.concept_uuid, drafts, runId, values],
  );

  return (
    <div data-testid="notes-numeric-row" style={styles.cellRow}>
      <aside style={styles.cellLeft}>
        <div style={styles.cellLabel}>{cell.label}</div>
        <div style={styles.cellRowNum}>Row {cell.row}</div>
      </aside>
      <div style={styles.cellRight}>
        <div style={styles.cellToolbar}>
          <div style={styles.cellToolbarSpacer} />
          <SaveStatusBadge status={status} />
        </div>
        <div style={styles.numericGrid}>
          {columns.map((key) => (
            <label key={key} style={styles.numericField}>
              <span style={styles.numericFieldLabel}>
                {NUMERIC_VALUE_COLUMNS[key].label}
              </span>
              <input
                type="text"
                inputMode="decimal"
                data-testid={`numeric-input-${cell.row}-${key}`}
                style={styles.numericInput}
                // Grouped (1,234) at rest; raw digits while this field is
                // focused so typing and the parse round-trip aren't disturbed.
                value={
                  focusedKey === key
                    ? drafts[key] ?? ""
                    : formatGroupedInput(drafts[key] ?? "")
                }
                onChange={(e) =>
                  // Keep the raw, comma-free form in the draft; the at-rest
                  // display re-adds separators on blur. parseNumericInput also
                  // accepts commas, so a stray separator wouldn't break a save.
                  setDrafts((d) => ({
                    ...d,
                    [key]: e.target.value.replace(/,/g, ""),
                  }))
                }
                onFocus={() => setFocusedKey(key)}
                onBlur={() => {
                  setFocusedKey(null);
                  saveColumn(key);
                }}
              />
            </label>
          ))}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Per-cell paste-format popover — table-wide knobs (shared controls) plus a
// per-row double-underline picker. Everything here is transient: it tweaks the
// options THIS cell's Copy uses, never the stored content (gotcha #16).
// ---------------------------------------------------------------------------

function FormatPopover({
  options,
  onChange,
  tableRows,
  onToggleRowUnderline,
  onCopy,
}: {
  options: ClipboardFormatOptions;
  onChange: (next: ClipboardFormatOptions) => void;
  tableRows: string[];
  onToggleRowUnderline: (rowIdx: number) => void;
  onCopy: () => void;
}) {
  return (
    <div style={styles.formatPopover} data-testid="notes-format-popover">
      <p style={styles.formatPopoverHint}>
        Format this copy only — starts from your default (Settings → Notes paste
        format) and resets next time.
      </p>

      {/* Table-wide knobs, identical to the settings section. */}
      <ClipboardFormatControls
        value={options}
        onChange={onChange}
        idPrefix="cell-fmt"
      />

      {/* Per-row double-underline picker (e.g. mark the totals row). */}
      {tableRows.length > 0 && (
        <div style={styles.rowUnderlineBlock}>
          <div style={styles.rowUnderlineHeading}>
            Double underline rows (e.g. totals)
          </div>
          <div style={styles.rowUnderlineList}>
            {tableRows.map((preview, idx) => (
              <label
                key={idx}
                style={styles.rowUnderlineItem}
                data-testid={`row-underline-${idx}`}
              >
                <input
                  type="checkbox"
                  checked={options.rowUnderlines.includes(idx)}
                  onChange={() => onToggleRowUnderline(idx)}
                  aria-label={`Double underline row: ${preview}`}
                />
                <span style={styles.rowUnderlinePreview}>{preview}</span>
              </label>
            ))}
          </div>
        </div>
      )}

      <button
        type="button"
        style={{ ...styles.smallButton, ...styles.formatCopyButton }}
        onClick={onCopy}
        data-testid="notes-format-copy"
      >
        Copy with this format
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Formatting toolbar — Bold / Italic / Lists / H3 / Table.
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// Editor toolbar — the single docked two-tier formatting bar (notes editor v2,
// docs/PRD-notes-editor-v2.md §7). Tier 1 (Text · Colour · Paragraph) always
// shows in edit mode; Tier 2 (Table: fill / borders / structure) activates
// only when the selection is inside a table. It replaces v1's separate
// FormatToolbar + TableFormatBar. Reactive because the parent CellRow
// re-renders on every editor transaction (`shouldRerenderOnTransaction`), so
// active states + the in-table tier track the selection without a doc edit.
// All cell formatting persists via the styled cell extensions
// (web/src/lib/cellFormatting.ts); marks/colour/align persist as inline HTML
// the backend sanitiser accepts in lock-step (notes/html_sanitize.py).
// ---------------------------------------------------------------------------

// Quick cell-fill presets (the convenience row). "Fill Grey" is depended on by
// the component tests — keep that label stable.
const FILL_PRESETS: ReadonlyArray<{ label: string; color: string }> = [
  { label: "White", color: "#ffffff" },
  { label: "Grey", color: "#f4f4f4" },
  { label: "Highlight", color: "#fff6e5" },
];

const BORDER_SIDE_BTNS: ReadonlyArray<{ side: BorderSide; label: string }> = [
  { side: "Top", label: "Top" },
  { side: "Right", label: "Right" },
  { side: "Bottom", label: "Bottom" },
  { side: "Left", label: "Left" },
];

function EditorToolbar({ editor }: { editor: Editor }) {
  // Border colour for newly-added grid lines — a tool setting, not cell state.
  const [borderColor, setBorderColor] = useState<string>(DEFAULT_BORDER_COLOR);

  const inTable = editor.isActive("table");

  // All buttons preventDefault on mousedown so clicking them never blurs the
  // editor / collapses a (multi-cell or text) selection before the command
  // runs — the same fix that makes range fill work (Step 0.1).
  const btn = (
    label: React.ReactNode,
    ariaLabel: string,
    onClick: () => void,
    active?: boolean,
  ) => (
    <button
      key={ariaLabel}
      type="button"
      aria-label={ariaLabel}
      onMouseDown={(e) => e.preventDefault()}
      style={active ? styles.toolbarButtonActive : styles.toolbarButton}
      onClick={onClick}
    >
      {label}
    </button>
  );

  // A constrained-palette colour swatch (text colour or highlight). `null`
  // value is the reset (remove colour / no highlight).
  const swatch = (s: PaletteSwatch, kind: "text" | "highlight") => {
    const apply = () => {
      const c = editor.chain().focus();
      if (kind === "text") {
        if (s.value === null) c.unsetColor().run();
        else c.setColor(s.value).run();
      } else {
        if (s.value === null) c.unsetHighlight().run();
        else c.toggleHighlight({ color: s.value }).run();
      }
    };
    return (
      <button
        key={`${kind}-${s.label}`}
        type="button"
        title={s.label}
        aria-label={`${kind === "text" ? "Text colour" : "Highlight"} ${s.label}`}
        onMouseDown={(e) => e.preventDefault()}
        onClick={apply}
        style={{
          ...styles.swatchButton,
          background: s.value ?? pwc.white,
        }}
      >
        {s.value === null ? "✕" : ""}
      </button>
    );
  };

  const sideIsOn = (side: BorderSide): boolean => {
    const v = currentCellAttrs(editor)?.[`border${side}`];
    return typeof v === "string" && v !== "" && v !== BORDER_NONE;
  };

  return (
    <div style={styles.editorToolbar} data-testid="editor-format-bar">
      {/* Tier 1 — always available in edit mode. */}
      <div role="toolbar" aria-label="Formatting" style={styles.toolbarRow}>
        <span style={styles.tableFormatGroupLabel}>Text</span>
        {btn(<span style={{ fontWeight: 700 }}>B</span>, "Bold",
          () => editor.chain().focus().toggleBold().run(), editor.isActive("bold"))}
        {btn(<span style={{ fontStyle: "italic" }}>I</span>, "Italic",
          () => editor.chain().focus().toggleItalic().run(), editor.isActive("italic"))}
        {btn(<span style={{ textDecoration: "underline" }}>U</span>, "Underline",
          () => editor.chain().focus().toggleUnderline().run(), editor.isActive("underline"))}
        {btn(<span style={{ textDecoration: "line-through" }}>S</span>, "Strikethrough",
          () => editor.chain().focus().toggleStrike().run(), editor.isActive("strike"))}
        {btn("x²", "Superscript",
          () => editor.chain().focus().toggleSuperscript().run(), editor.isActive("superscript"))}
        {btn("x₂", "Subscript",
          () => editor.chain().focus().toggleSubscript().run(), editor.isActive("subscript"))}

        <span style={styles.tableFormatDivider} aria-hidden="true" />

        <span style={styles.tableFormatGroupLabel}>Colour</span>
        {TEXT_COLORS.map((s) => swatch(s, "text"))}
        <span style={styles.tableFormatGroupLabel}>Highlight</span>
        {HIGHLIGHT_COLORS.map((s) => swatch(s, "highlight"))}

        <span style={styles.tableFormatDivider} aria-hidden="true" />

        <span style={styles.tableFormatGroupLabel}>Paragraph</span>
        {btn("L", "Align left",
          () => editor.chain().focus().setTextAlign("left").run(), editor.isActive({ textAlign: "left" }))}
        {btn("C", "Align centre",
          () => editor.chain().focus().setTextAlign("center").run(), editor.isActive({ textAlign: "center" }))}
        {btn("R", "Align right",
          () => editor.chain().focus().setTextAlign("right").run(), editor.isActive({ textAlign: "right" }))}
        {btn("• List", "Bullet list",
          () => editor.chain().focus().toggleBulletList().run(), editor.isActive("bulletList"))}
        {btn("1. List", "Numbered list",
          () => editor.chain().focus().toggleOrderedList().run(), editor.isActive("orderedList"))}
        {btn("H3", "Heading",
          () => editor.chain().focus().toggleHeading({ level: 3 }).run(), editor.isActive("heading", { level: 3 }))}
        {btn("⇤", "Decrease indent", () => outdentBlocks(editor))}
        {btn("⇥", "Increase indent", () => indentBlocks(editor))}
        {btn("Table", "Insert table",
          () => editor.chain().focus().insertTable({ rows: 2, cols: 2, withHeaderRow: true }).run())}
      </div>

      {/* Tier 2 — table controls, only inside a table. The data-testid is kept
          from v1's TableFormatBar so existing tests keep working. */}
      {inTable && (
        <div
          role="toolbar"
          aria-label="Table formatting"
          data-testid="table-format-bar"
          style={styles.tableFormatBar}
        >
          <span style={styles.tableFormatGroupLabel}>Fill</span>
          {FILL_PRESETS.map((p) =>
            btn(p.label, `Fill ${p.label}`, () => applyCellFill(editor, p.color)),
          )}
          {btn("No fill", "No fill", () => applyCellFill(editor, FILL_NONE))}

          <span style={styles.tableFormatDivider} aria-hidden="true" />

          <span style={styles.tableFormatGroupLabel}>Border</span>
          {BORDER_SIDE_BTNS.map(({ side, label }) =>
            btn(
              label,
              `Border ${label}`,
              () =>
                applyCellBorderSide(
                  editor,
                  side,
                  sideIsOn(side) ? BORDER_NONE : gridBorderValue(borderColor),
                ),
              sideIsOn(side),
            ),
          )}
          {btn("All", "Border all", () =>
            applyCellBorderAll(editor, gridBorderValue(borderColor)))}
          {btn("None", "Border none", () =>
            applyCellBorderAll(editor, BORDER_NONE))}
          <label style={styles.colorInputLabel} title="Border colour">
            <span style={styles.visuallyHidden}>Border colour</span>
            <input
              type="color"
              aria-label="Border colour"
              value={borderColor}
              style={styles.colorInput}
              onChange={(e) => setBorderColor(e.target.value)}
            />
          </label>

          <span style={styles.tableFormatDivider} aria-hidden="true" />

          {/* Per-cell alignment — applies across a drag-selected range (e.g.
              right-align a whole numeric column). Distinct from the Tier-1
              paragraph align and from the cosmetic numeric auto-right-align. */}
          <span style={styles.tableFormatGroupLabel}>Align</span>
          {(["left", "center", "right"] as CellAlign[]).map((a) =>
            btn(
              a === "left" ? "L" : a === "center" ? "C" : "R",
              `Cell align ${a}`,
              () => applyCellAlign(editor, a),
              (currentCellAttrs(editor)?.textAlign as string | undefined) === a,
            ),
          )}

          <span style={styles.tableFormatDivider} aria-hidden="true" />

          <span style={styles.tableFormatGroupLabel}>Table</span>
          {btn("Row ↑", "Insert row above", () => editor.chain().focus().addRowBefore().run())}
          {btn("Row ↓", "Insert row below", () => editor.chain().focus().addRowAfter().run())}
          {btn("Col ←", "Insert column left", () => editor.chain().focus().addColumnBefore().run())}
          {btn("Col →", "Insert column right", () => editor.chain().focus().addColumnAfter().run())}
          {btn("Merge", "Merge cells", () => editor.chain().focus().mergeCells().run())}
          {btn("Split", "Split cell", () => editor.chain().focus().splitCell().run())}
          {btn("Header", "Toggle header row", () => editor.chain().focus().toggleHeaderRow().run())}
          {btn("− Row", "Delete row", () => editor.chain().focus().deleteRow().run())}
          {btn("− Col", "Delete column", () => editor.chain().focus().deleteColumn().run())}
          {btn("Delete table", "Delete table", () => editor.chain().focus().deleteTable().run())}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Save status badge.
// ---------------------------------------------------------------------------

function SaveStatusBadge({ status }: { status: SaveStatus }) {
  if (status === "idle") return null;
  const label = {
    dirty: "Unsaved",
    saving: "Saving…",
    saved: "Saved",
    failed: "Save failed",
  }[status];
  const color = {
    dirty: pwc.grey700,
    saving: pwc.grey700,
    saved: pwc.success ?? "#2f855a",
    failed: pwc.error ?? "#c53030",
  }[status];
  return (
    <span style={{ ...styles.statusBadge, color }}>
      {label}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Regenerate confirm modal (Step 12).
// ---------------------------------------------------------------------------

function ConfirmRegenerateModal({
  count,
  onCancel,
  onConfirm,
}: {
  count: number;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  // count === UNKNOWN_COUNT means the edited_count endpoint failed
  // (5xx / network error). We can't say "this will overwrite N cells"
  // because we don't know N — instead we warn the user that the safety
  // check didn't run and any edits on this run could be overwritten.
  const isUnknown = count === UNKNOWN_COUNT;
  return (
    <div style={styles.modalBackdrop} role="dialog" aria-modal="true">
      <div style={styles.modalCard}>
        <h4 style={styles.modalTitle}>Regenerate notes?</h4>
        <p style={styles.modalBody}>
          {isUnknown ? (
            <>
              We couldn't verify whether your edits would be overwritten —
              the edited-count check failed. Regenerating now will replace
              every cell on this run's notes sheets. If you have unsaved
              edits, cancel and try again in a moment.
            </>
          ) : (
            <>
              You'll be taken to the Extract page. Re-upload the same PDF
              and start a new notes run there — when it completes, it will
              overwrite {count} edited cell{count === 1 ? "" : "s"} on this
              run. Your current edits stay in place until that new run
              finishes.
            </>
          )}
        </p>
        <div style={styles.modalActions}>
          <button
            type="button"
            className={uiClass.btnSecondary}
            style={styles.modalCancelButton}
            onClick={onCancel}
          >
            Cancel
          </button>
          <button
            type="button"
            className={uiClass.btnPrimary}
            style={styles.modalConfirmButton}
            onClick={onConfirm}
          >
            Continue
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Styles. Every rule is inline (gotcha #7). The scoped CSS file carries
// the rules we can't express inline — hover states, TipTap table
// borders, and the ProseMirror placeholder hooks.
// ---------------------------------------------------------------------------

const styles = {
  root: {
    display: "flex",
    flexDirection: "column" as const,
    gap: 16,
  } as React.CSSProperties,
  topBar: {
    display: "flex",
    justifyContent: "flex-end",
    alignItems: "center",
  } as React.CSSProperties,
  regenerateButton: {
    ...ui.buttonGhost,
    ...ui.buttonSm,
  } as React.CSSProperties,
  errorBanner: {
    padding: "8px 12px",
    background: pwc.errorBg,
    color: pwc.errorText,
    border: `1px solid ${pwc.errorBorder}`,
    borderRadius: 4,
    fontSize: 13,
  } as React.CSSProperties,
  dim: {
    color: pwc.grey700,
    fontSize: 13,
  } as React.CSSProperties,
  // Navigator chip bar — one chip per sheet, jumps to + opens that
  // section. Wraps on narrow widths. Sits above the stack.
  sheetNav: {
    display: "flex",
    flexWrap: "wrap" as const,
    gap: 6,
  } as React.CSSProperties,
  sheetNavChip: {
    display: "inline-flex",
    alignItems: "center",
    gap: 6,
    padding: "4px 10px",
    fontFamily: pwc.fontMono,
    fontSize: 11.5,
    fontWeight: 600,
    letterSpacing: 0.2,
    color: pwc.grey700,
    background: pwc.white,
    border: `1px solid ${pwc.grey200}`,
    borderRadius: pwc.radius.pill,
    cursor: "pointer",
  } as React.CSSProperties,
  sheetNavChipActive: {
    display: "inline-flex",
    alignItems: "center",
    gap: 6,
    padding: "4px 10px",
    fontFamily: pwc.fontMono,
    fontSize: 11.5,
    fontWeight: 600,
    letterSpacing: 0.2,
    color: pwc.orange700,
    background: pwc.orange50,
    border: `1px solid ${pwc.orange500}`,
    borderRadius: pwc.radius.pill,
    cursor: "pointer",
  } as React.CSSProperties,
  sheetNavChipCount: {
    fontFamily: pwc.fontMono,
    fontSize: 10.5,
    fontWeight: 400,
    color: pwc.grey500,
  } as React.CSSProperties,
  sheetStack: {
    display: "flex",
    flexDirection: "column" as const,
    gap: 10,
  } as React.CSSProperties,
  // Each sheet is a white card with a clear header band + an orange left
  // accent when open (set inline in SheetSection). The dominant header
  // and flat row list (below) make sheet boundaries obvious instead of
  // every container reading as an identical card.
  sheetSection: {
    display: "flex",
    flexDirection: "column" as const,
    border: `1px solid ${pwc.grey200}`,
    borderLeft: `3px solid ${pwc.grey300}`,
    borderRadius: 6,
    background: pwc.white,
    overflow: "hidden",
  } as React.CSSProperties,
  // The <h4> wrapper strips default browser margins so the button
  // fills the card header cleanly.
  sheetHeadingWrap: {
    margin: 0,
  } as React.CSSProperties,
  // Full-width button so the whole row is clickable, not just the text.
  // Grey header band makes the sheet boundary read as a section divider
  // distinct from the flat white rows below it.
  sheetHeadingButton: {
    display: "flex",
    alignItems: "center",
    gap: 10,
    width: "100%",
    padding: "11px 14px",
    background: pwc.grey100,
    border: "none",
    cursor: "pointer",
    fontFamily: "inherit",
    font: "inherit",
    color: "inherit",
    textAlign: "left" as const,
  } as React.CSSProperties,
  sheetHeadingText: {
    fontFamily: pwc.fontMono,
    fontSize: 14,
    fontWeight: 600,
    color: pwc.grey900,
    textTransform: "uppercase" as const,
    letterSpacing: 0.4,
  } as React.CSSProperties,
  sheetChevron: {
    color: pwc.grey500,
    fontSize: 12,
    width: 12,
    display: "inline-block",
  } as React.CSSProperties,
  sheetRowCount: {
    marginLeft: "auto",
    fontFamily: pwc.fontMono,
    fontSize: 12,
    color: pwc.grey500,
  } as React.CSSProperties,
  rowStack: {
    display: "flex",
    flexDirection: "column" as const,
    gap: 0,
    padding: "0 14px",
    borderTop: `1px solid ${pwc.grey200}`,
  } as React.CSSProperties,
  // Flat list rows separated by hairlines — not bordered cards. With the
  // sheet header carrying the visual weight, rows read as content nested
  // under the sheet rather than as peer containers.
  cellRow: {
    display: "grid",
    gridTemplateColumns: "220px 1fr",
    gap: 16,
    padding: "14px 4px",
    borderBottom: `1px solid ${pwc.grey100}`,
    background: pwc.white,
  } as React.CSSProperties,
  cellLeft: {
    display: "flex",
    flexDirection: "column" as const,
    gap: 4,
  } as React.CSSProperties,
  cellLabel: {
    fontWeight: 600,
    fontSize: 13,
    color: pwc.grey900,
  } as React.CSSProperties,
  cellRowNum: {
    fontSize: 11,
    color: pwc.grey500,
    fontFamily: pwc.fontMono,
  } as React.CSSProperties,
  evidenceBlock: {
    marginTop: 6,
    padding: "4px 6px",
    background: pwc.grey100,
    borderRadius: 3,
    fontSize: 11,
    color: pwc.grey700,
    display: "flex",
    flexDirection: "column" as const,
    gap: 2,
  } as React.CSSProperties,
  evidenceLabel: {
    textTransform: "uppercase" as const,
    letterSpacing: 0.3,
    fontWeight: 600,
    fontSize: 10,
  } as React.CSSProperties,
  evidenceText: {
    whiteSpace: "pre-wrap" as const,
  } as React.CSSProperties,
  cellRight: {
    display: "flex",
    flexDirection: "column" as const,
    gap: 6,
  } as React.CSSProperties,
  cellToolbar: {
    display: "flex",
    alignItems: "center",
    gap: 6,
    flexWrap: "wrap" as const,
  } as React.CSSProperties,
  cellToolbarSpacer: {
    flex: 1,
  } as React.CSSProperties,
  // The unified docked editor toolbar (notes editor v2): a column holding
  // Tier 1 (text/colour/paragraph) and, when in a table, Tier 2 (table).
  editorToolbar: {
    display: "flex",
    flexDirection: "column" as const,
    gap: 4,
    marginTop: 4,
  } as React.CSSProperties,
  toolbarRow: {
    display: "flex",
    flexWrap: "wrap" as const,
    alignItems: "center",
    gap: 4,
    padding: "6px 8px",
    background: pwc.white,
    border: `1px solid ${pwc.grey200}`,
    borderRadius: 4,
  } as React.CSSProperties,
  // A small constrained-palette colour/highlight swatch button.
  swatchButton: {
    width: 18,
    height: 18,
    padding: 0,
    border: `1px solid ${pwc.grey300 ?? "#d1d5db"}`,
    borderRadius: 3,
    cursor: "pointer",
    fontSize: 10,
    lineHeight: 1,
    color: pwc.grey700,
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
  } as React.CSSProperties,
  toolbarButton: {
    padding: "2px 8px",
    fontSize: 12,
    fontFamily: pwc.fontBody,
    background: pwc.white,
    border: `1px solid ${pwc.grey200}`,
    borderRadius: 3,
    color: pwc.grey700,
    cursor: "pointer",
  } as React.CSSProperties,
  toolbarButtonActive: {
    padding: "2px 8px",
    fontSize: 12,
    fontFamily: pwc.fontBody,
    background: pwc.orange500,
    border: `1px solid ${pwc.orange500}`,
    borderRadius: 3,
    color: pwc.white,
    cursor: "pointer",
  } as React.CSSProperties,
  smallButton: {
    padding: "3px 10px",
    fontSize: 12,
    fontWeight: 600,
    background: pwc.white,
    border: `1px solid ${pwc.grey300 ?? "#d1d5db"}`,
    borderRadius: 3,
    color: pwc.grey900,
    cursor: "pointer",
  } as React.CSSProperties,
  // Active state for the Format toggle (popover open).
  smallButtonActive: {
    background: pwc.grey100,
    borderColor: pwc.grey700,
  } as React.CSSProperties,
  // Selection-based table formatting bar (fill / borders / structure).
  tableFormatBar: {
    display: "flex",
    flexWrap: "wrap" as const,
    alignItems: "center",
    gap: 4,
    padding: "6px 8px",
    marginTop: 4,
    background: pwc.grey100,
    border: `1px solid ${pwc.grey200}`,
    borderRadius: 4,
  } as React.CSSProperties,
  tableFormatGroupLabel: {
    fontSize: 11,
    fontWeight: 700,
    textTransform: "uppercase" as const,
    letterSpacing: 0.4,
    color: pwc.grey700,
    marginRight: 2,
  } as React.CSSProperties,
  tableFormatDivider: {
    width: 1,
    alignSelf: "stretch",
    background: pwc.grey300 ?? "#d1d5db",
    margin: "0 4px",
  } as React.CSSProperties,
  colorInputLabel: {
    display: "inline-flex",
    alignItems: "center",
  } as React.CSSProperties,
  colorInput: {
    width: 26,
    height: 22,
    padding: 0,
    border: `1px solid ${pwc.grey300 ?? "#d1d5db"}`,
    borderRadius: 3,
    background: pwc.white,
    cursor: "pointer",
  } as React.CSSProperties,
  visuallyHidden: {
    position: "absolute" as const,
    width: 1,
    height: 1,
    overflow: "hidden" as const,
    clip: "rect(0 0 0 0)",
    whiteSpace: "nowrap" as const,
  } as React.CSSProperties,
  // Per-cell paste-format popover.
  formatPopover: {
    border: `1px solid ${pwc.grey300}`,
    borderRadius: 6,
    background: pwc.white,
    padding: 12,
    marginBottom: 8,
  } as React.CSSProperties,
  formatPopoverHint: {
    fontSize: 12,
    color: pwc.grey700,
    margin: "0 0 10px 0",
  } as React.CSSProperties,
  rowUnderlineBlock: {
    marginTop: 12,
    borderTop: `1px solid ${pwc.grey200}`,
    paddingTop: 10,
  } as React.CSSProperties,
  rowUnderlineHeading: {
    fontSize: 13,
    fontWeight: 500,
    color: pwc.grey700,
    marginBottom: 6,
  } as React.CSSProperties,
  rowUnderlineList: {
    display: "flex",
    flexDirection: "column" as const,
    gap: 4,
    maxHeight: 160,
    overflowY: "auto" as const,
  } as React.CSSProperties,
  rowUnderlineItem: {
    display: "flex",
    alignItems: "center",
    gap: 8,
    fontSize: 13,
    cursor: "pointer",
  } as React.CSSProperties,
  rowUnderlinePreview: {
    color: pwc.grey900,
    whiteSpace: "nowrap" as const,
    overflow: "hidden",
    textOverflow: "ellipsis",
  } as React.CSSProperties,
  formatCopyButton: {
    marginTop: 12,
    background: pwc.grey100,
  } as React.CSSProperties,
  // Numeric notes: a small grid of value inputs (1-4 columns by filing level).
  numericGrid: {
    display: "flex",
    flexWrap: "wrap" as const,
    gap: 12,
    padding: "4px 0",
  } as React.CSSProperties,
  numericField: {
    display: "flex",
    flexDirection: "column" as const,
    gap: 3,
    minWidth: 110,
  } as React.CSSProperties,
  numericFieldLabel: {
    fontSize: 11,
    fontWeight: 600,
    color: pwc.grey700,
  } as React.CSSProperties,
  numericInput: {
    padding: "4px 8px",
    fontSize: 13,
    fontFamily: pwc.fontBody,
    textAlign: "right" as const,
    border: `1px solid ${pwc.grey300 ?? "#d1d5db"}`,
    borderRadius: 3,
    color: pwc.grey900,
  } as React.CSSProperties,
  statusBadge: {
    fontSize: 11,
    fontWeight: 600,
    textTransform: "uppercase" as const,
    letterSpacing: 0.4,
  } as React.CSSProperties,
  copiedChip: {
    fontSize: 11,
    fontWeight: 600,
    color: pwc.success,
    textTransform: "uppercase" as const,
    letterSpacing: 0.4,
  } as React.CSSProperties,
  modalBackdrop: {
    position: "fixed" as const,
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    background: "rgba(17, 24, 39, 0.5)",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    zIndex: 1000,
  } as React.CSSProperties,
  modalCard: {
    background: pwc.white,
    borderRadius: 8,
    padding: 20,
    maxWidth: 400,
    width: "90%",
    boxShadow: "0 20px 40px rgba(0,0,0,0.2)",
  } as React.CSSProperties,
  modalTitle: {
    fontFamily: pwc.fontHeading,
    fontSize: 16,
    fontWeight: pwc.weight.medium,
    margin: 0,
    marginBottom: 8,
    color: pwc.grey900,
  } as React.CSSProperties,
  modalBody: {
    fontSize: 14,
    color: pwc.grey700,
    margin: 0,
    marginBottom: 16,
    lineHeight: 1.5,
  } as React.CSSProperties,
  modalActions: {
    display: "flex",
    justifyContent: "flex-end",
    gap: 8,
  } as React.CSSProperties,
  modalCancelButton: {
    ...ui.buttonSecondary,
    ...ui.buttonSm,
  } as React.CSSProperties,
  modalConfirmButton: {
    ...ui.buttonPrimary,
    ...ui.buttonSm,
  } as React.CSSProperties,
} as const;
