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
import { userMessage } from "../lib/errors";
import { useEditor, EditorContent } from "@tiptap/react";
import { StarterKit } from "@tiptap/starter-kit";
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
  StyledTable,
  StyledTableCell,
  StyledTableHeader,
  captureSelection,
  restoreSelection,
} from "../lib/cellFormatting";
import { Indent } from "../lib/notesIndent";
import { pwc } from "../lib/theme";
import { ui, uiClass } from "../lib/uiStyles";
import {
  fetchNotesCells,
  fetchNotesFormatStatus,
  launchNotesFormatter,
  revertNotesFormatter,
  patchNotesCell,
  removeInvalidNotesCell,
  patchNotesFact,
  parseNumericInput,
  sortSheetsBySlot,
  INVALID_NUMBER,
  NUMERIC_VALUE_COLUMNS,
  type NotesCell,
  type NotesFormatStatus,
  type NotesSheet,
} from "../lib/notesCells";
import { copyHtmlAsRichText } from "../lib/clipboard";
import { tagNumericCells } from "../lib/tableAlign";
import { formatGroupedInput } from "../lib/numberFormat";
import { notesFormatErrorMessage } from "../lib/vocabulary";
import {
  resolveTheme,
  themeToCssVars,
  parseThemeOptions,
  type ClipboardFormatOptions,
} from "../lib/clipboardFormat";
import { ClipboardFormatControls } from "./ClipboardFormatControls";
import { ConfirmDialog } from "./ConfirmDialog";
import { notesSheetDisplayName } from "../lib/sheetLabels";
import { ResizableDivider } from "./ResizableDivider";
import { NotesEditorToolbar } from "./NotesEditorToolbar";
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
   *  SheetNavigator. The section scrolls into view; null means no focus. */
  focusSheet?: string | null;
  /** Jump to a specific cell (sheet + row) and scroll it into view — driven by
   *  the workspace's notes checklist ("jump to where this note landed"). `key`
   *  bumps on every click so re-selecting the same cell re-scrolls. null = no
   *  cell focus. */
  focusCell?: { sheet: string; row: number; key: number } | null;
  /** Fired on EVERY cell focus (click / tab into), with the PDF pages that
   *  cell was extracted from (`NotesCell.source_pages`) — an EMPTY array when
   *  the cell has none, so the caller can clear stale pages and mark the
   *  selection as "no source page recorded". The workspace uses it to drive
   *  the Source PDF pane so a note and its source page sit side by side, the
   *  way a face figure already does. Optional — the standalone Notes tab
   *  renders identically without it. */
  onActiveCellPages?: (pages: number[]) => void;
}

interface SourceNotePlacement {
  sheet: string;
  row: number;
  row_label: string;
  kind: "primary" | "fan_out" | "carve_out";
}

interface SourceNoteInventoryRow {
  note_num: number;
  title: string;
  status: "placed" | "missing" | "skipped" | "suspected_gap";
  placements: SourceNotePlacement[];
  reviewer_added?: boolean;
  page_lo: number | null;
  page_hi: number | null;
}

function sourceNotePages(note: SourceNoteInventoryRow): number[] {
  if (note.page_lo == null) return [];
  const hi = Math.max(note.page_lo, note.page_hi ?? note.page_lo);
  return Array.from({ length: hi - note.page_lo + 1 }, (_, index) => note.page_lo! + index);
}

function sourceNoteForCell(
  notes: SourceNoteInventoryRow[],
  cellKey: string,
  current: number | null,
): number | null {
  const matches = notes.filter((note) =>
    note.placements.some(
      (placement) => `${placement.sheet}:${placement.row}` === cellKey,
    ),
  );
  if (current != null && matches.some((note) => note.note_num === current)) {
    return current;
  }
  return matches.length === 1 ? matches[0].note_num : null;
}

const NOTES_SHEET_INDEX: Record<string, number> = {
  "Notes-CI": 0,
  "Notes-SummaryofAccPol": 1,
  "Notes-Listofnotes": 2,
  "Notes-Issuedcapital": 3,
  "Notes-RelatedPartytran": 4,
};

function notesSheetNumber(sheet: string, filingStandard: string): number | null {
  const offset = NOTES_SHEET_INDEX[sheet];
  if (offset == null) return null;
  return (filingStandard.toLowerCase() === "mpers" ? 11 : 10) + offset;
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
  // Block alignment. List-item alignment and indentation are included so
  // paste-originated <li> formatting survives a save/reload cycle; table-cell
  // alignment is handled separately
  // (a per-cell `textAlign` attribute set from the Table toolbar group), so
  // cells are intentionally NOT in this type list.
  TextAlign.configure({ types: ["heading", "paragraph", "listItem"] }),
  // Paragraph indentation (custom; no first-party TipTap extension).
  Indent,
  // resizable: true enables drag-to-resize column widths. Widths serialise as
  // a standard `<colgroup><col style="width">` + cell `colwidth` attrs, which
  // the sanitiser accepts and which paste faithfully into Word/Excel.
  // StyledTable additionally round-trips the verbatim-Word `data-source-styled`
  // marker, which the CSS below reads to suppress the theme grid on those
  // tables (a plain Table would drop it on the first save).
  StyledTable.configure({ resizable: true }),
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

export function NotesReviewTab({
  runId,
  onRegenerate,
  focusSheet,
  focusCell,
  onActiveCellPages,
}: NotesReviewTabProps) {
  // sheets / loading / error are the basic fetch lifecycle. We keep them
  // at the tab level (not in the individual cell editor) so one network
  // failure surfaces in a single banner instead of per-cell flicker.
  const [sheets, setSheets] = useState<NotesSheet[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [sourceNotes, setSourceNotes] = useState<SourceNoteInventoryRow[] | null>(null);
  const [sourceNotesError, setSourceNotesError] = useState(false);
  const [selectedSourceNote, setSelectedSourceNote] = useState<number | null>(null);
  const [sourceRailWidth, setSourceRailWidth] = useState(240);
  const [filingStandard, setFilingStandard] = useState("mfrs");
  const actionsMenuRef = useRef<HTMLDetailsElement | null>(null);

  // Regenerate-notes confirm modal state. `pendingCount` is populated
  // by the edited_count fetch; a truthy value opens the dialog and
  // stores the message "This will overwrite N edited cells".
  const [pendingCount, setPendingCount] = useState<number | null>(null);

  // Formatter default is loaded once and threaded into each sheet. Empty lets
  // the server use the run's model.
  const [formatterDefaultModel, setFormatterDefaultModel] = useState<string>("");
  // (The /api/settings fetch that populates these is declared AFTER the
  // notes-cells load effect below, so the notes GET stays the first request —
  // order matters to the order-sensitive fetch mocks in the tests.)

  // Notes-table style theme (docs/PLAN-notes-table-theme.md). `firmTheme` is the
  // server-wide firm default (from /api/config); `runTheme` is this run's
  // optional override (wired in Phase 5 — null until then). The resolved theme
  // drives BOTH the editor preview (CSS vars on the root) AND the clipboard
  // Copy, so they match.
  const [firmTheme, setFirmTheme] = useState<Partial<ClipboardFormatOptions>>({});
  const [runTheme, setRunTheme] = useState<Partial<ClipboardFormatOptions> | null>(
    null,
  );
  const theme = useMemo(
    () => resolveTheme(runTheme, firmTheme),
    [runTheme, firmTheme],
  );
  const themeVars = useMemo(() => themeToCssVars(theme), [theme]);

  // Which sheet the navigator has focused. `key` bumps on every click so
  // re-clicking the active sheet still scrolls it into view.
  const [active, setActive] = useState<{ sheet: string | null; key: number }>(
    () => ({ sheet: focusSheet ?? null, key: 0 }),
  );
  // Reset the navigator focus when the run changes. `active`'s lazy
  // initializer only runs at mount, so without this a sheet the reviewer
  // focused in run A (via a nav chip) would survive the runId change and
  // scroll a same-named section in run B — mirroring the
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

  // Cell-level focus from the workspace notes checklist ("jump to where this
  // note landed"). Opens the target sheet (like a nav chip) AND remembers the
  // row so the matching SheetSection scrolls that cell into view. Keyed on
  // focusCell.key so re-clicking the same cell re-scrolls.
  const [focusRow, setFocusRow] = useState<number | null>(null);
  const [selectedCellKey, setSelectedCellKey] = useState<string | null>(null);
  const [noteSearch, setNoteSearch] = useState("");
  useEffect(() => {
    if (!focusCell) return;
    setActive((a) => ({ sheet: focusCell.sheet, key: a.key + 1 }));
    setFocusRow(focusCell.row);
    setSelectedCellKey(`${focusCell.sheet}:${focusCell.row}`);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- key is the stable
    // re-trigger; depending on the object identity would loop.
  }, [focusCell?.key]);

  useEffect(() => {
    setSelectedCellKey(null);
    setNoteSearch("");
    setSelectedSourceNote(null);
    setSourceNotes(null);
    setSourceNotesError(false);
  }, [runId]);

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

  // The coverage projection is the durable scout inventory plus its exact
  // structured placements. It is the source-first index for the review
  // workspace; no label matching is performed in the browser.
  useEffect(() => {
    const controller = new AbortController();
    fetch(`/api/runs/${runId}/notes-coverage`, { signal: controller.signal })
      .then((response) => {
        if (!response.ok) throw new Error(String(response.status));
        return response.json();
      })
      .then((payload) => {
        const rows = Array.isArray(payload?.rows)
          ? payload.rows.filter((row: SourceNoteInventoryRow) => row.note_num > 0)
          : [];
        setSourceNotes(rows);
        setSourceNotesError(false);
      })
      .catch((error) => {
        if (error instanceof DOMException && error.name === "AbortError") return;
        setSourceNotes([]);
        setSourceNotesError(true);
      });
    return () => controller.abort();
  }, [runId]);

  useEffect(() => {
    const closeOnOutsidePointer = (event: PointerEvent) => {
      const menu = actionsMenuRef.current;
      if (menu?.open && !menu.contains(event.target as Node)) menu.open = false;
    };
    document.addEventListener("pointerdown", closeOnOutsidePointer);
    return () => document.removeEventListener("pointerdown", closeOnOutsidePointer);
  }, []);

  const reloadNotes = useCallback(() => {
    return fetchNotesCells(runId).then((resp) => {
      setSheets(sortSheetsBySlot(resp.sheets));
      setLoadError(null);
    }).catch((err: Error) => {
      setLoadError(err.message);
    });
  }, [runId]);

  const handleCellRemoved = useCallback((removedSheet: string, removedRow: number) => {
    setSheets((current) => current?.map((item) => (
      item.sheet === removedSheet
        ? { ...item, rows: item.rows.filter((row) => row.row !== removedRow) }
        : item
    )) ?? current);
  }, []);

  // Firm-default theme (declared AFTER the notes-cells fetch so the cells load
  // first — the editor renders with the CSS-var fallbacks until the theme
  // arrives, which is a no-op for an un-customised firm). Fires once on mount.
  useEffect(() => {
    let cancelled = false;
    fetch("/api/config")
      .then((r) => (r.ok ? r.json() : null))
      .then((cfg) => {
        // Degrade silently to the historic look if config is unavailable — the
        // CSS-var fallbacks already render today's grey grid.
        if (!cancelled && cfg) setFirmTheme(cfg.notes_table_style ?? {});
      })
      .catch(() => {
        /* leave firmTheme = {} → CSS-var fallbacks show the historic look */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Formatter model list + configured default (see the state declarations
  // above). Declared here — after the notes-cells load — so the notes GET is
  // still the first request under order-sensitive test mocks. Best-effort:
  // the Format button falls back to the server default without it.
  useEffect(() => {
    let cancelled = false;
    fetch("/api/settings")
      .then((r) => (r.ok ? r.json() : null))
      .then((s) => {
        if (cancelled || !s) return;
        setFormatterDefaultModel(
          (s.default_models && s.default_models.notes_formatter) || "",
        );
      })
      .catch(() => {
        /* best-effort — server default (the run's model) still applies */
      });
    return () => { cancelled = true; };
  }, []);

  // Last value the SERVER confirmed for this run's override — restored on a
  // failed save so the UI never shows/copies an unsaved theme (peer-review
  // MEDIUM #5). Debounce timer coalesces per-keystroke number edits + keeps
  // saves in order (peer-review HIGH #2).
  const lastSavedRunThemeRef = useRef<Partial<ClipboardFormatOptions> | null>(null);
  const runSaveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // This run's optional style override (v22). Re-fetched per run; null = inherit
  // the firm default. Declared last so it never consumes the notes-cells fetch.
  useEffect(() => {
    let cancelled = false;
    setRunTheme(null); // reset on run switch before the fetch resolves
    lastSavedRunThemeRef.current = null;
    fetch(`/api/runs/${runId}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (!cancelled && d) {
          const override = d.notes_table_style ?? null;
          setRunTheme(override);
          lastSavedRunThemeRef.current = override;
          setFilingStandard(d.filing_standard || "mfrs");
        }
      })
      .catch(() => {
        /* no override → resolveTheme falls back to the firm default */
      });
    return () => {
      cancelled = true;
    };
  }, [runId]);

  const isEmpty = sheets !== null && sheets.length === 0;
  const noteEntries = useMemo(
    () => (sheets ?? []).flatMap((sheet) =>
      // The projection deliberately includes blank filing fields. They are
      // authorable review targets (including the numeric-note disclosure
      // slot), not absent extraction results, so the workspace must
      // keep them reachable just like the full-template view does.
      sheet.rows.map((cell) => ({
        key: `${sheet.sheet}:${cell.row}`,
        sheet,
        cell,
      })),
    ),
    [sheets],
  );
  const filteredSourceNotes = useMemo(() => {
    const query = noteSearch.trim().toLowerCase();
    const inventory = sourceNotes ?? [];
    if (!query) return inventory;
    return inventory.filter((note) =>
      `${note.note_num} ${note.title}`
        .toLowerCase()
        .includes(query),
    );
  }, [noteSearch, sourceNotes]);
  // Avoid a transient all-read-only render while the scout inventory request
  // settles. Inventory placement can replace this fallback selection later.
  const activeCellKey = selectedCellKey
    ?? (sourceNotes === null ? noteEntries[0]?.key : null)
    ?? null;

  useEffect(() => {
    if (sourceNotes == null || sourceNotes.length === 0) return;
    if (selectedSourceNote != null && sourceNotes.some((note) => note.note_num === selectedSourceNote)) return;
    if (selectedCellKey != null) {
      setSelectedSourceNote(
        sourceNoteForCell(sourceNotes, selectedCellKey, selectedSourceNote),
      );
      return;
    }
    const first = sourceNotes[0];
    setSelectedSourceNote(first.note_num);
    reportCellPages(sourceNotePages(first), onActiveCellPages);
    const placement = first.placements[0];
    if (placement) {
      setActive((current) => ({ sheet: placement.sheet, key: current.key + 1 }));
      setFocusRow(placement.row);
      setSelectedCellKey(`${placement.sheet}:${placement.row}`);
    }
  }, [onActiveCellPages, selectedCellKey, selectedSourceNote, sourceNotes]);

  // Legacy runs may have XBRL fields but no persisted scout inventory. Keep
  // the review surface usable and initialise the PDF state from the first
  // field, while the left rail states that the inventory is unavailable.
  useEffect(() => {
    if (
      sourceNotes == null ||
      sourceNotes.length > 0 ||
      noteEntries.length === 0 ||
      selectedCellKey
    ) return;
    const first = focusSheet
      ? noteEntries.find((entry) => entry.sheet.sheet === focusSheet) ?? noteEntries[0]
      : noteEntries[0];
    setSelectedCellKey(first.key);
    reportCellPages(first.cell.source_pages, onActiveCellPages);
  }, [focusSheet, noteEntries, onActiveCellPages, selectedCellKey, sourceNotes]);

  useEffect(() => {
    if (!focusCell || sourceNotes == null) return;
    const cellKey = `${focusCell.sheet}:${focusCell.row}`;
    setSelectedSourceNote((current) =>
      sourceNoteForCell(sourceNotes, cellKey, current),
    );
  }, [focusCell, sourceNotes]);

  const selectSourcePlacement = useCallback((
    note: SourceNoteInventoryRow,
    placement: SourceNotePlacement,
  ) => {
    setSelectedSourceNote(note.note_num);
    reportCellPages(sourceNotePages(note), onActiveCellPages);
    setActive((current) => ({ sheet: placement.sheet, key: current.key + 1 }));
    setFocusRow(placement.row);
    setSelectedCellKey(`${placement.sheet}:${placement.row}`);
  }, [onActiveCellPages]);

  const selectSourceNote = useCallback((note: SourceNoteInventoryRow) => {
    const placement = note.placements[0];
    if (placement) {
      selectSourcePlacement(note, placement);
      return;
    }
    setSelectedSourceNote(note.note_num);
    reportCellPages(sourceNotePages(note), onActiveCellPages);
    setFocusRow(null);
    setSelectedCellKey(null);
  }, [onActiveCellPages, selectSourcePlacement]);

  const handleWorkspaceCellActivate = useCallback((sheet: string, row: number) => {
    const cellKey = `${sheet}:${row}`;
    setSelectedCellKey(cellKey);
    // A filing field can be intentionally blank or have no source placement.
    // A field can also contain more than one source note. Preserve the note
    // the reviewer arrived from when it is one of the valid matches; otherwise
    // select only an unambiguous match and clear a stale or ambiguous claim.
    setSelectedSourceNote((current) =>
      sourceNoteForCell(sourceNotes ?? [], cellKey, current),
    );
  }, [sourceNotes]);

  // Per-run "Table style" panel state + handlers (docs/PLAN-notes-table-theme.md).
  const [styleOpen, setStyleOpen] = useState(false);
  const [styleError, setStyleError] = useState<string | null>(null);

  // Persist this run's override (or clear it) and re-paint instantly. The PATCH
  // works on any run status — review happens after extraction.
  const persistRunTheme = useCallback(
    (next: ClipboardFormatOptions | null) => {
      setRunTheme(next); // optimistic: tables re-theme immediately
      // Clamp/validate before sending; clearing (null) is always valid.
      const payload = next === null ? null : parseThemeOptions(next);
      const send = () =>
        fetch(`/api/runs/${runId}/notes_table_style`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ notes_table_style: payload }),
        })
          .then((r) => {
            if (!r.ok) throw new Error(String(r.status));
            lastSavedRunThemeRef.current = payload;
            setStyleError(null);
          })
          .catch(() => {
            setStyleError("Couldn't save this run's table style — check your connection.");
            setRunTheme(lastSavedRunThemeRef.current); // revert to last confirmed
          });
      if (runSaveTimer.current) clearTimeout(runSaveTimer.current);
      // "Use firm default" (null) saves immediately; knob edits debounce.
      if (next === null) send();
      else runSaveTimer.current = setTimeout(send, 500);
    },
    [runId],
  );

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
      <div
        className="notes-review-tab"
        data-testid="notes-source-first-workspace"
        style={{ ...styles.root, ...themeVars }}
      >
        <div
          className="notes-source-first-layout"
          style={{
            ...styles.workspaceLayout,
            gridTemplateColumns: `${sourceRailWidth}px 9px minmax(0, 1fr)`,
          }}
        >
          <aside style={styles.noteRail} aria-label="Source note inventory">
            <div style={styles.noteRailHeader}>
              <div>
                <strong style={styles.noteRailTitle}>Source notes</strong>
                <p style={styles.noteRailCount}>
                  {sourceNotes == null ? "Loading inventory…" : `${sourceNotes.length} found by document scan`}
                </p>
              </div>
            </div>
            <input
              type="search"
              aria-label="Search source notes"
              placeholder="Search source notes"
              value={noteSearch}
              onChange={(event) => setNoteSearch(event.target.value)}
              style={styles.noteRailSearch}
            />
            <nav style={styles.noteRailList} aria-label="Source notes">
              {sourceNotesError && (
                <p style={styles.noteRailEmpty} role="status">
                  The source-note inventory could not be loaded.
                </p>
              )}
              {!sourceNotesError && sourceNotes?.length === 0 && (
                <p style={styles.noteRailEmpty}>No source-note inventory is available for this run.</p>
              )}
              {!sourceNotesError && sourceNotes != null && sourceNotes.length > 0
                && filteredSourceNotes.length === 0 && (
                <p style={styles.noteRailEmpty} role="status">
                  No source notes match your search.
                </p>
              )}
              {filteredSourceNotes.map((note) => {
                const selected = note.note_num === selectedSourceNote;
                const pageLabel = note.page_lo == null
                  ? "Page not recorded"
                  : note.page_hi != null && note.page_hi !== note.page_lo
                    ? `pp. ${note.page_lo}–${note.page_hi}`
                    : `p. ${note.page_lo}`;
                const destinationLabel = note.placements.length === 0
                  ? "Not placed"
                  : note.placements.length === 1
                    ? notesSheetDisplayName(note.placements[0].sheet)
                    : `${note.placements.length} destinations`;
                const needsReview = note.status === "missing" || note.status === "suspected_gap";
                return (
                  <div key={note.note_num} style={styles.noteRailItemGroup}>
                    <button
                      type="button"
                      data-testid={`source-note-${note.note_num}`}
                      aria-current={selected ? "true" : undefined}
                      data-tooltip={needsReview ? "Placement needs review" : undefined}
                      title={needsReview ? "Placement needs review" : undefined}
                      onClick={() => selectSourceNote(note)}
                      style={{
                        ...styles.noteRailItem,
                        ...(selected ? styles.noteRailItemActive : {}),
                      }}
                    >
                      <span style={styles.noteRailNumber}>{note.note_num}</span>
                      <span style={styles.noteRailCopy}>
                        <span style={styles.noteRailLabel}>{note.title || `Note ${note.note_num}`}</span>
                        <span style={styles.noteRailDestination}>
                          {pageLabel} · {destinationLabel}
                        </span>
                      </span>
                      {needsReview && (
                        <span style={styles.noteRailAttention} aria-label="Needs review">!</span>
                      )}
                    </button>
                    {selected && note.placements.length > 1 && (
                      <div
                        style={styles.noteRailPlacements}
                        aria-label={`Destinations for note ${note.note_num}`}
                      >
                        {note.placements.map((placement) => (
                          <button
                            key={`${placement.sheet}:${placement.row}`}
                            type="button"
                            style={styles.noteRailPlacementButton}
                            onClick={() => selectSourcePlacement(note, placement)}
                          >
                            {notesSheetDisplayName(placement.sheet)} · row {placement.row}
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                );
              })}
            </nav>
          </aside>

          <ResizableDivider
            testId="resize-source-notes"
            label="Resize source note inventory"
            onDelta={(dx) => setSourceRailWidth((width) => Math.max(190, Math.min(420, width + dx)))}
          />

          <section style={styles.editorPane} aria-label="XBRL notes fields">
            {sheets !== null && !isEmpty && (
              <div style={styles.workspaceToolbar}>
                <nav style={styles.workspaceSheetNav} aria-label="Notes sheet navigator">
                  {sheets.map((sheet) => {
                    const number = notesSheetNumber(sheet.sheet, filingStandard);
                    const selected = active.sheet === sheet.sheet;
                    const label = number == null ? notesSheetDisplayName(sheet.sheet) : `Sheet ${number}`;
                    return (
                      <button
                        key={sheet.sheet}
                        type="button"
                        aria-current={selected ? "true" : undefined}
                        aria-label={`${label} — ${notesSheetDisplayName(sheet.sheet)}`}
                        title={notesSheetDisplayName(sheet.sheet)}
                        style={selected ? styles.workspaceSheetButtonActive : styles.workspaceSheetButton}
                        onClick={() => {
                          setFocusRow(null);
                          setActive((current) => ({ sheet: sheet.sheet, key: current.key + 1 }));
                        }}
                      >
                        {label}
                      </button>
                    );
                  })}
                </nav>
                <details ref={actionsMenuRef} style={styles.actionsMenu}>
                  <summary
                    style={styles.actionsMenuSummary}
                    aria-label="Notes actions"
                    data-tooltip="Notes actions"
                  >
                    •••
                  </summary>
                  <div style={styles.actionsMenuPanel}>
                    <button
                      type="button"
                      style={styles.actionsMenuItem}
                      aria-expanded={styleOpen}
                      onClick={() => {
                        setStyleOpen((value) => !value);
                        if (actionsMenuRef.current) actionsMenuRef.current.open = false;
                      }}
                    >
                      Table style
                    </button>
                    <button
                      type="button"
                      style={styles.actionsMenuItem}
                      onClick={() => {
                        if (actionsMenuRef.current) actionsMenuRef.current.open = false;
                        handleRegenerateClick();
                      }}
                      disabled={!onRegenerate}
                      title={onRegenerate ? undefined : "Re-extract isn't available in this view"}
                    >
                      Re-extract notes…
                    </button>
                  </div>
                </details>
              </div>
            )}

            {styleOpen && (
              <div style={styles.stylePanel} data-testid="notes-table-style-panel">
                <p style={styles.stylePanelHint}>
                  Applies to every table in this run and to copied content.
                  {runTheme ? " This run has its own style." : " Using the firm default."}
                </p>
                {styleError && (
                  <p style={{ ...styles.stylePanelHint, color: pwc.error }} role="alert">
                    {styleError}
                  </p>
                )}
                <ClipboardFormatControls
                  value={theme}
                  onChange={(next) => persistRunTheme(next)}
                  idPrefix="run-fmt"
                />
                <button
                  type="button"
                  className={uiClass.btnGhost}
                  style={styles.regenerateButton}
                  disabled={!runTheme}
                  onClick={() => persistRunTheme(null)}
                >
                  Use firm default
                </button>
              </div>
            )}

            {loadError ? (
              <p style={styles.dim}>Notes could not be loaded. Refresh the page to try again.</p>
            ) : sheets === null ? (
              <p style={styles.dim}>Loading XBRL fields…</p>
            ) : noteEntries.length === 0 ? (
              <p style={styles.dim}>No notes were extracted for this run.</p>
            ) : (
              <div style={styles.sheetStack}>
                {sheets.map((sheet) => (
                  <SheetSection
                    key={`${runId}:${sheet.sheet}`}
                    runId={runId}
                    sheet={sheet}
                    theme={theme}
                    formatterDefaultModel={formatterDefaultModel}
                    focus={active.sheet === sheet.sheet}
                    focusKey={active.key}
                    focusRow={active.sheet === sheet.sheet ? focusRow : null}
                    onFormatted={reloadNotes}
                    onCellRemoved={handleCellRemoved}
                    onActiveCellPages={onActiveCellPages}
                    selectedCellKey={activeCellKey}
                    onCellActivate={handleWorkspaceCellActivate}
                  />
                ))}
              </div>
            )}
          </section>
        </div>
        <ConfirmDialog
          isOpen={pendingCount !== null}
          title="Re-extract notes?"
          message={
            pendingCount === UNKNOWN_COUNT ? (
              <>
                We couldn&apos;t verify whether your edits would be overwritten —
                the safety check failed. Re-extracting will replace every cell on
                this run&apos;s notes sheets. If you have unsaved edits, cancel and
                try again in a moment.
              </>
            ) : (
              <>
                This starts a fresh notes extraction on this PDF. When it
                finishes it will replace {pendingCount ?? 0} edited cell
                {pendingCount === 1 ? "" : "s"} on this run&apos;s notes sheets.
                Your current edits stay in place until the new run completes.
              </>
            )
          }
          confirmLabel="Re-extract notes"
          danger={false}
          onConfirm={() => {
            setPendingCount(null);
            onRegenerate?.(runId);
          }}
          onCancel={() => setPendingCount(null)}
        />
      </div>
  );
}

// ---------------------------------------------------------------------------
// Sheet section — one heading + a stack of CellRow rows per sheet.
// ---------------------------------------------------------------------------

function SheetSection({
  runId,
  sheet,
  theme,
  formatterDefaultModel,
  onFormatted,
  onCellRemoved,
  focus = false,
  focusKey = 0,
  focusRow = null,
  onActiveCellPages,
  selectedCellKey = null,
  onCellActivate,
}: {
  runId: number;
  sheet: NotesSheet;
  /** Resolved notes-table theme — threaded to CellRow so Copy uses it. */
  theme: ClipboardFormatOptions;
  formatterDefaultModel: string;
  onFormatted: () => Promise<void>;
  onCellRemoved: (sheet: string, row: number) => void;
  /** When true (the reviewer picked this notes sub-tab / nav chip), the
   *  section scrolls into view. */
  focus?: boolean;
  /** Bumps on every nav-chip click so re-selecting the same section scrolls. */
  focusKey?: number;
  /** When set (and this is the focused section), scroll this specific row into
   *  view instead of the section top — the notes-checklist "jump to cell". */
  focusRow?: number | null;
  /** Threaded to each row so focusing a cell reports its source PDF pages. */
  onActiveCellPages?: (pages: number[]) => void;
  selectedCellKey?: string | null;
  onCellActivate?: (sheet: string, row: number) => void;
}) {
  const [formatStatus, setFormatStatus] = useState<NotesFormatStatus | null>(null);
  const [formatError, setFormatError] = useState<string | null>(null);
  const [rowSaveStatuses, setRowSaveStatuses] = useState<Record<number, SaveStatus>>({});
  // Which model the AI formatter runs on. Seeds from the configured
  // notes_formatter default; empty falls through to the server's fallback
  // (the run's extraction model — api/notes_formatter.py).
  // Confirm dialog for removing this sheet's formatting (shared dialog).
  const [confirmRevertFormat, setConfirmRevertFormat] = useState(false);
  const sectionRef = useRef<HTMLElement | null>(null);

  // When this section becomes focused, bring the target row or section into
  // view after the always-open field stack has laid out.
  useEffect(() => {
    if (!focus) return;
    const raf =
      typeof requestAnimationFrame === "function"
        ? requestAnimationFrame
        : (cb: FrameRequestCallback) => setTimeout(() => cb(0), 0);
    raf(() => {
      if (focusRow != null) {
        // Jump to the specific cell (notes checklist "jump to where it landed").
        const el = sectionRef.current?.querySelector<HTMLElement>(
          `[data-cell-row="${focusRow}"]`,
        );
        (el ?? sectionRef.current)?.scrollIntoView?.({
          block: "center",
          behavior: "smooth",
        });
      } else {
        sectionRef.current?.scrollIntoView?.({ block: "start", behavior: "smooth" });
      }
    });
  }, [focus, focusKey, focusRow]);

  // Hydrate format state on mount so a pass launched in another tab/session
  // (or one that finished while this section was unmounted) is reflected here:
  // a still-running task resumes the "Formatting..." indicator + polling below,
  // and a finished one shows its summary instead of a stale idle button.
  useEffect(() => {
    if ((sheet.kind ?? "prose") !== "prose") return;
    let cancelled = false;
    fetchNotesFormatStatus(runId, sheet.sheet)
      .then((state) => {
        if (cancelled || state.status === "idle") return;
        setFormatStatus(state);
      })
      .catch(() => {
        /* Non-fatal: the Format button still works from a clean state. */
      });
    return () => {
      cancelled = true;
    };
  }, [runId, sheet.sheet, sheet.kind]);

  useEffect(() => {
    if (formatStatus?.status !== "running") return;
    let cancelled = false;
    const timer = setInterval(() => {
      fetchNotesFormatStatus(runId, sheet.sheet)
        .then(async (state) => {
          if (cancelled) return;
          setFormatStatus(state);
          if (state.status === "done") {
            clearInterval(timer);
            if (!state.error) await onFormatted();
          }
        })
        .catch((err: Error) => {
          if (!cancelled) {
            clearInterval(timer);
            setFormatError(userMessage(err));
            setFormatStatus(null);
          }
        });
    }, 2000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [formatStatus?.status, onFormatted, runId, sheet.sheet]);

  const handleFormat = useCallback(async () => {
    setFormatError(null);
    try {
      const state = await launchNotesFormatter(
        runId, sheet.sheet, formatterDefaultModel || undefined,
      );
      setFormatStatus(state);
    } catch (err) {
      setFormatError(userMessage(err));
    }
  }, [formatterDefaultModel, runId, sheet.sheet]);

  const handleRevert = useCallback(async () => {
    setFormatError(null);
    try {
      await revertNotesFormatter(runId, sheet.sheet);
      const state = await fetchNotesFormatStatus(runId, sheet.sheet);
      setFormatStatus(state);
      await onFormatted();
    } catch (err) {
      setFormatError(
        userMessage(err),
      );
    }
  }, [onFormatted, runId, sheet.sheet]);

  const handleRowSaveStatus = useCallback((row: number, status: SaveStatus) => {
    // Only pending states are tracked; anything else PRUNES the row's entry.
    // A CellRow withdraws itself on unmount (reports "idle") so switching the
    // selected field mid-edit can't wedge Format at "Save pending".
    setRowSaveStatuses((prev) => {
      const pending =
        status === "dirty" || status === "saving" || status === "failed";
      if (!pending) {
        if (!(row in prev)) return prev;
        const next = { ...prev };
        delete next[row];
        return next;
      }
      return prev[row] === status ? prev : { ...prev, [row]: status };
    });
  }, []);

  const canFormat = (sheet.kind ?? "prose") === "prose";
  const hasPendingRowSave = Object.keys(rowSaveStatuses).length > 0;
  const isFormatting = formatStatus?.status === "running";
  const totalTokens =
    (formatStatus?.prompt_tokens ?? 0) + (formatStatus?.completion_tokens ?? 0);
  const formatButtonLabel = hasPendingRowSave
    ? "Save pending"
    : isFormatting
      ? "Formatting..."
      : "Format";

  return (
    <section ref={sectionRef} style={styles.workspaceSheetSection}>
      <div style={styles.sheetHeadingButton}>
        <h4 style={styles.sheetHeadingWrap}>
          <span
            style={styles.sheetHeadingText}
            data-testid="sheet-title"
            title={sheet.sheet}
          >
            {notesSheetDisplayName(sheet.sheet)}
          </span>
        </h4>
        {canFormat && (
          <button
            type="button"
            className={uiClass.btnGhost}
            style={styles.sheetFormatButton}
            disabled={isFormatting || hasPendingRowSave}
            onClick={handleFormat}
            aria-label={`AI format ${notesSheetDisplayName(sheet.sheet)}`}
            title={
              hasPendingRowSave
                ? "Resolve notes save status before formatting."
                : undefined
            }
            data-testid="notes-format-button"
          >
            {formatButtonLabel}
          </button>
        )}
      </div>
      {(formatStatus?.status === "done" || formatError) && (
        <div
          style={{
            ...styles.formatSummary,
            color: formatError || formatStatus?.error ? pwc.errorText : pwc.grey700,
          }}
          role={formatError || formatStatus?.error ? "alert" : "status"}
          data-testid="notes-format-summary"
        >
          <span style={styles.formatSummaryText}>
            {formatError ||
              (formatStatus?.error
                ? notesFormatErrorMessage(
                    formatStatus.error_type,
                    formatStatus.error,
                  )
                : (
                  `${formatStatus?.summary || "Formatting complete."} ` +
                  `Changed ${formatStatus?.changed_rows ?? 0} row(s).` +
                  (typeof formatStatus?.confidence === "number"
                    ? ` Confidence ${(formatStatus.confidence * 100).toFixed(0)}%.`
                    : "") +
                  (totalTokens > 0
                    ? ` ~${totalTokens.toLocaleString()} tokens.`
                    : "")
                ))}
          </span>
          {formatStatus?.can_revert
            && formatStatus.error_type !== "reverted"
            && !formatError && (
            <button
              type="button"
              className={uiClass.btnGhost}
              style={styles.sheetFormatButton}
              onClick={() => setConfirmRevertFormat(true)}
              data-testid="notes-format-revert"
            >
              Remove formatting changes
            </button>
          )}
        </div>
      )}
      <ConfirmDialog
        isOpen={confirmRevertFormat}
        title="Remove formatting changes?"
        message="This sheet's cells go back to how they looked before the last formatting pass. The figures and wording are unchanged — only the styling is removed."
        confirmLabel="Remove formatting"
        onConfirm={() => {
          setConfirmRevertFormat(false);
          void handleRevert();
        }}
        onCancel={() => setConfirmRevertFormat(false)}
      />
      {isFormatting && (
        <div
          style={styles.formattingBanner}
          role="status"
          data-testid="notes-format-running-banner"
        >
          Formatting in progress — edits you make now are preserved and
          skipped by the formatter. Styling applies to the preview and
          paste, not the Excel download.
        </div>
      )}
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
                onActiveCellPages={onActiveCellPages}
                selected={selectedCellKey === `${sheet.sheet}:${cell.row}`}
                onActivate={() => onCellActivate?.(sheet.sheet, cell.row)}
              />
            ) : selectedCellKey !== `${sheet.sheet}:${cell.row}` ? (
              <WorkspaceReadOnlyCellRow
                key={`${runId}:${sheet.sheet}:${cell.row}`}
                cell={cell}
                theme={theme}
                onActiveCellPages={onActiveCellPages}
                onActivate={() => onCellActivate?.(sheet.sheet, cell.row)}
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
                theme={theme}
                onSaveStatusChange={handleRowSaveStatus}
                onCellRemoved={onCellRemoved}
                onActiveCellPages={onActiveCellPages}
                onActivate={() => onCellActivate?.(sheet.sheet, cell.row)}
              />
            ),
          )}
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Lightweight workspace row — keeps every field visible without constructing
// a TipTap editor for every prose slot. Selecting the row swaps in CellRow,
// which owns editing, saving, and the full toolbar.
// ---------------------------------------------------------------------------

function WorkspaceReadOnlyCellRow({
  cell,
  theme,
  onActiveCellPages,
  onActivate,
}: {
  cell: NotesCell;
  theme: ClipboardFormatOptions;
  onActiveCellPages?: (pages: number[]) => void;
  onActivate: () => void;
}) {
  const [copied, setCopied] = useState(false);
  const contentRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!copied) return;
    const timer = setTimeout(() => setCopied(false), 2000);
    return () => clearTimeout(timer);
  }, [copied]);

  // Numeric alignment is a render-time concern and must not be persisted in
  // notes_cells. Apply the same shared tagger used by the selected TipTap
  // editor so the lightweight browse view still matches clipboard output.
  useEffect(() => {
    if (contentRef.current) tagNumericCells(contentRef.current);
  }, [cell.html]);

  const activate = () => {
    onActivate();
    reportCellPages(cell.source_pages, onActiveCellPages);
  };

  return (
    <div
      data-testid="notes-review-row"
      data-cell-row={cell.row}
      className="notes-review-row"
      style={styles.workspaceCellRow}
      onMouseDown={(event) => {
        if (!(event.target as HTMLElement).closest("button")) activate();
      }}
    >
      <aside style={styles.cellLeft}>
        <div style={styles.cellLabel}>{cell.label}</div>
        <StyleSourceChip source={cell.style_source} />
      </aside>
      <div style={styles.cellRight}>
        <div style={styles.cellToolbar}>
          <div style={styles.cellToolbarSpacer} />
          {copied && <span style={styles.copiedChip}>Copied</span>}
          <button type="button" style={styles.smallButton} onClick={activate}>
            Review
          </button>
          <button
            type="button"
            style={styles.smallButton}
            onClick={async () => {
              if (await copyHtmlAsRichText(cell.html, theme)) setCopied(true);
            }}
          >
            Copy
          </button>
        </div>
        {/* notes_cells HTML is sanitised before persistence by every write path;
            this read-only projection avoids constructing a TipTap instance. */}
        <div
          ref={contentRef}
          className="tiptap ProseMirror"
          data-testid="notes-readonly-content"
          style={styles.workspaceReadonlySurface}
          dangerouslySetInnerHTML={{ __html: cell.html }}
        />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Style-source chip — surfaces how a prose cell got its table styling at
// extraction time (schema v29). We only render a chip for the cases the
// operator wants to hunt down: "unstyled" (rendered plain — the default now
// that the house-style floor is off) and "floor" (deterministic house style,
// not a PDF observation). "ops" (agent observed the formatting) and null
// (blank / reviewer-authored / legacy) render nothing, to keep the column
// quiet. The chip is a hint to run the notes formatter on that cell.
function StyleSourceChip({
  source,
}: {
  source?: "ops" | "source" | "floor" | "unstyled" | "formatter" | null;
}) {
  if (source !== "unstyled" && source !== "floor") return null;
  const label = source === "unstyled" ? "Unstyled" : "House style";
  const title =
    source === "unstyled"
      ? "This cell rendered plain — the agent recorded no table formatting. Run the notes formatter to style it."
      : "Styled by the deterministic house style, not a PDF observation. Review or run the notes formatter if it doesn't match the source.";
  return (
    <span
      data-testid="notes-style-source-chip"
      data-style-source={source}
      style={styles.styleSourceChip}
      title={title}
    >
      {label}
    </span>
  );
}

/** Report a focused cell's source PDF pages to the workspace. An empty list is
 *  significant: it clears evidence left behind by the previous selection. */
function reportCellPages(
  pages: number[] | undefined,
  cb?: (pages: number[]) => void,
) {
  // Always report — INCLUDING an empty list. The old guard (only report
  // non-empty) left the PREVIOUS note's pages showing when a page-less cell
  // was focused, silently mislabelling them as this cell's source. The
  // workspace tracks "a cell is selected" separately, so an empty report now
  // renders the honest "No source page recorded" state instead of a stale
  // page (run-168 peer-review finding).
  cb?.(pages ?? []);
}

// Cell row — label + evidence on the left, editor + actions on the right.
// ---------------------------------------------------------------------------

function CellRow({
  runId,
  sheet,
  cell,
  theme,
  onSaveStatusChange,
  onCellRemoved,
  onActiveCellPages,
  onActivate,
}: {
  runId: number;
  sheet: string;
  cell: NotesCell;
  /** Resolved notes-table theme — Copy decorates the paste with it so the
   *  clipboard output matches the editor preview. */
  theme: ClipboardFormatOptions;
  onSaveStatusChange?: (row: number, status: SaveStatus) => void;
  onCellRemoved: (sheet: string, row: number) => void;
  /** Fired on focus/click with this cell's source PDF pages so the workspace
   *  can jump the Source PDF pane to where the note came from. */
  onActiveCellPages?: (pages: number[]) => void;
  onActivate?: () => void;
}) {
  const [editable, setEditable] = useState(false);
  const [editorFocused, setEditorFocused] = useState(false);
  const [status, setStatus] = useState<SaveStatus>("idle");
  const [copiedAt, setCopiedAt] = useState<number | null>(null);
  // The API's warning strings are deliberately developer-facing (and verbose
  // after a Word/Excel paste), so never render them verbatim. A compact,
  // human-readable signal is enough to explain why a saved edit looks a little
  // different without turning the review screen into a diagnostics panel.
  const [formatAdjusted, setFormatAdjusted] = useState(false);
  const [confirmRemove, setConfirmRemove] = useState(false);
  const [removeBusy, setRemoveBusy] = useState(false);
  const [removeError, setRemoveError] = useState<string | null>(null);
  // Keep a mutable ref to the current HTML so the debounced saver reads
  // the latest value without resubscribing every keystroke.
  const liveHtmlRef = useRef<string>(cell.html);
  const savedHtmlRef = useRef<string>(cell.html);
  // The optimistic version token for this cell (schema v37). Seeded from the
  // loaded cell and refreshed from every PATCH response, so a second save in
  // the same session does not 409 against our own write. Null on a cell the
  // projection surfaced but that has no row yet — nothing to conflict with.
  const revisionRef = useRef<number | null>(cell.content_revision ?? null);
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
    onSaveStatusChange?.(cell.row, status);
  }, [cell.row, onSaveStatusChange, status]);

  useEffect(() => {
    return () => {
      // Withdraw this row's save-status entry when selection unmounts it.
      // Without this, a row unmounting while "dirty" leaves a stale entry
      // that wedges the sheet's Format button at "Save pending" — the
      // flush-on-unmount effect below saves the content anyway.
      onSaveStatusChange?.(cell.row, "idle");
    };
  }, [cell.row, onSaveStatusChange]);

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
          const body = JSON.stringify(
            revisionRef.current == null
              ? { html: liveHtmlRef.current }
              : {
                  html: liveHtmlRef.current,
                  expected_revision: revisionRef.current,
                },
          );
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
    onFocus: () => setEditorFocused(true),
    onBlur: () => setEditorFocused(false),
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

  const toggleEditable = useCallback(() => {
    if (!editor) return;
    if (editable) {
      editor.commands.blur();
      setEditable(false);
      return;
    }
    editor.setEditable(true);
    setEditable(true);
    setEditorFocused(true);
    editor.commands.focus();
  }, [editable, editor]);

  // Defensive sync for `cell.html` prop changes. `useEditor({ content })`
  // only consumes `content` on mount, so a subsequent parent refetch
  // (say a future inline-regenerate, or a parent-driven refresh) that
  // delivers a new HTML payload for the SAME key would otherwise leave
  // the editor showing stale content. Latent today because runId is in
  // the key (cross-run switches already remount the component), and a
  // normal formatting save updates the internal refs, NOT this `cell.html`
  // prop — so this effect does not fire on a self-originated save. It only
  // fires on a genuine parent-driven refetch, where we still capture/restore
  // the selection (same as the save-reconcile) so a future caller can't
  // collapse a mid-formatting multi-cell selection.
  useEffect(() => {
    if (!editor) return;
    if (editor.getHTML() === cell.html) return;
    const captured = captureSelection(editor);
    editor.commands.setContent(cell.html, { emitUpdate: false });
    try {
      restoreSelection(editor, captured);
    } catch {
      /* positions invalid after a structural change — harmless */
    }
    // Keep the persisted baseline in TipTap's own serialisation form. Table
    // parsing legitimately inserts browser/editor structure such as <tbody>
    // and paragraph wrappers inside cells. Saving the raw API string here
    // makes a later representation-only transaction look like a user edit,
    // which arms the debounce and advances content_revision on unmount.
    const settled = editor.getHTML();
    liveHtmlRef.current = settled;
    savedHtmlRef.current = settled;
    setFormatAdjusted(false);
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
          runId, sheet, cell.row, attempted, revisionRef.current,
        );
        // Refresh the token on every save, or the NEXT save in this session
        // would send a stale one and 409 against our own write.
        if (typeof updated.content_revision === "number") {
          revisionRef.current = updated.content_revision;
        }
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
            // Capture the selection BEFORE setContent (which resets it to the
            // doc start), then rebuild it after. A multi-cell border edit holds
            // a CellSelection that a text range CANNOT represent — restoring it
            // as text collapses the highlight and forces a re-select after every
            // formatting save (captureSelection/restoreSelection in
            // cellFormatting.ts preserve the cell anchors).
            const captured = captureSelection(editor);
            editor.commands.setContent(updated.html, { emitUpdate: false });
            // Best-effort restore. ProseMirror clamps out-of-range text positions
            // so a shorter doc after sanitisation won't throw; the try/catch also
            // covers a structural change invalidating the captured cell anchors.
            try {
              restoreSelection(editor, captured);
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
        // Show a single neutral acknowledgement for the most recent save when
        // the backend removed unsupported markup or styling. A subsequent
        // clean save clears it, so this remains a statement about the visible
        // document rather than a sticky historical warning.
        if (!isStale) {
          setFormatAdjusted((updated.sanitizer_warnings?.length ?? 0) > 0);
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
      const ce = ev as CustomEvent<{ html?: string; useCurrentHtml?: boolean }>;
      if (!editor) return;
      const html = ce.detail.useCurrentHtml ? editor.getHTML() : ce.detail.html;
      if (html == null) return;
      editor.commands.setContent(html, { emitUpdate: true });
    };
    node.addEventListener("notes-review-test-edit", handler);
    return () => node.removeEventListener("notes-review-test-edit", handler);
  }, [editor]);

  // Copy is a single action that decorates the paste with the RESOLVED notes
  // table theme (per-run override ?? firm default), so the clipboard output
  // matches the on-screen preview exactly (docs/PLAN-notes-table-theme.md).
  const handleCopy = useCallback(async () => {
    const html = editor?.getHTML() ?? cell.html;
    const ok = await copyHtmlAsRichText(html, theme);
    if (ok) setCopiedAt(Date.now());
  }, [editor, cell.html, theme]);

  const handleRemoveInvalid = useCallback(async () => {
    setRemoveBusy(true);
    setRemoveError(null);
    try {
      await removeInvalidNotesCell(runId, sheet, cell.row);
      setConfirmRemove(false);
      onCellRemoved(sheet, cell.row);
    } catch (error) {
      setRemoveError(userMessage(error));
    } finally {
      setRemoveBusy(false);
    }
  }, [cell.row, onCellRemoved, runId, sheet]);

  // Auto-dismiss the "Copied" pill after 2 seconds so the UI goes back
  // to a neutral state without the user clicking anywhere.
  useEffect(() => {
    if (copiedAt === null) return;
    const t = setTimeout(() => setCopiedAt(null), 2000);
    return () => clearTimeout(t);
  }, [copiedAt]);

  return (
    <div
      data-testid="notes-review-row"
      data-cell-row={cell.row}
      className="notes-review-row"
      style={{
        ...styles.workspaceCellRow,
        ...styles.workspaceCellRowSelected,
      }}
      // Focusing (click or keyboard-tab) any part of this row tells the
      // workspace which PDF pages the note came from, so the Source PDF pane
      // follows the note the way it follows a face figure. Capture phase means
      // this also fires when focus lands on the nested editor.
      onFocusCapture={() => {
        onActivate?.();
        reportCellPages(cell.source_pages, onActiveCellPages);
      }}
      onMouseDown={() => {
        onActivate?.();
        reportCellPages(cell.source_pages, onActiveCellPages);
      }}
    >
      <aside style={styles.cellLeft}>
        <div style={styles.cellLabel}>{cell.label}</div>
        {cell.invalid_target && (
          <div
            style={{ ...ui.alertWarning, marginTop: pwc.space.sm, fontSize: 12 }}
          >
            <div role="status">
              Not a filing field. Copy it into a writable field, then remove this
              quarantined copy before filing.
            </div>
            <button
              type="button"
              className={uiClass.btnGhost}
              style={{ ...ui.buttonGhost, display: "block", marginTop: pwc.space.sm }}
              onClick={() => setConfirmRemove(true)}
              disabled={removeBusy}
            >
              {removeBusy ? "Removing…" : "Remove quarantined content"}
            </button>
            {removeError && <div style={{ marginTop: 4 }}>{removeError}</div>}
            <ConfirmDialog
              isOpen={confirmRemove}
              title="Remove quarantined content?"
              message="This permanently erases the quarantined copy and adds a filing tombstone. There is no undo. Copy anything you need into a writable field before continuing."
              confirmLabel="Remove content"
              busyLabel="Removing…"
              busy={removeBusy}
              onConfirm={() => void handleRemoveInvalid()}
              onCancel={() => {
                if (!removeBusy) setConfirmRemove(false);
              }}
            />
          </div>
        )}
        <StyleSourceChip source={cell.style_source} />
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
          {formatAdjusted && (
            <span
              data-testid="format-adjusted-notice"
              role="status"
              style={styles.formatAdjustedNotice}
              title="Unsupported pasted formatting was removed when this note was saved"
            >
              Formatting adjusted
            </span>
          )}
          {copiedAt !== null && (
            <span style={styles.copiedChip}>Copied</span>
          )}
          <button
            type="button"
            style={styles.smallButton}
            onClick={toggleEditable}
            disabled={cell.invalid_target}
            title={cell.invalid_target
              ? "Move this content to a writable filing field before editing"
              : undefined}
          >
            {editable ? "Done" : "Edit"}
          </button>
          <button
            type="button"
            style={styles.smallButton}
            onClick={handleCopy}
            title="Copies using your Notes paste format defaults in Settings"
          >
            Copy
          </button>
        </div>
          {editable && editorFocused && editor && <NotesEditorToolbar editor={editor} />}
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

function NumericCellRow({
  runId,
  cell,
  onActiveCellPages,
  selected = false,
  onActivate,
}: {
  runId: number;
  cell: NotesCell;
  onActiveCellPages?: (pages: number[]) => void;
  selected?: boolean;
  onActivate?: () => void;
}) {
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
    <div
      data-testid="notes-numeric-row"
      data-cell-row={cell.row}
      className="notes-review-row"
      style={{
        ...styles.workspaceCellRow,
        ...(selected ? styles.workspaceCellRowSelected : {}),
      }}
      onFocusCapture={() => {
        onActivate?.();
        reportCellPages(cell.source_pages, onActiveCellPages);
      }}
      onMouseDown={() => {
        onActivate?.();
        reportCellPages(cell.source_pages, onActiveCellPages);
      }}
    >
      <aside style={styles.cellLeft}>
        <div style={styles.cellLabel}>{cell.label}</div>
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
  workspaceLayout: {
    display: "grid",
    gridTemplateColumns: "240px 9px minmax(0, 1fr)",
    gap: 0,
    alignItems: "start",
    minWidth: 0,
  } as React.CSSProperties,
  noteRail: {
    position: "sticky" as const,
    top: pwc.space.lg,
    height: "calc(100vh - 148px)",
    minHeight: 420,
    display: "flex",
    flexDirection: "column" as const,
    minWidth: 0,
    padding: `${pwc.space.sm}px ${pwc.space.lg}px ${pwc.space.lg}px 0`,
  } as React.CSSProperties,
  noteRailHeader: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "flex-start",
    padding: "1px 5px 10px",
  } as React.CSSProperties,
  noteRailTitle: {
    color: pwc.grey900,
    fontFamily: pwc.fontBody,
    fontSize: 12,
  } as React.CSSProperties,
  noteRailCount: {
    margin: "2px 0 0",
    color: pwc.grey700,
    fontSize: 10,
  } as React.CSSProperties,
  noteRailSearch: {
    ...ui.input,
    width: "100%",
    minHeight: 34,
    padding: "0 9px",
    border: "none",
    borderRadius: 7,
    background: pwc.grey50,
    fontSize: 11,
  } as React.CSSProperties,
  noteRailList: {
    minHeight: 0,
    marginTop: 7,
    overflowY: "auto" as const,
    display: "flex",
    flexDirection: "column" as const,
    gap: 1,
    paddingRight: 3,
  } as React.CSSProperties,
  noteRailItemGroup: {
    display: "flex",
    flexDirection: "column" as const,
    gap: 2,
  } as React.CSSProperties,
  noteRailItem: {
    display: "grid",
    gridTemplateColumns: "28px minmax(0, 1fr) 16px",
    gap: 7,
    alignItems: "center",
    width: "100%",
    minHeight: 38,
    padding: "6px 7px",
    border: "none",
    borderRadius: 7,
    background: "transparent",
    color: pwc.grey700,
    textAlign: "left" as const,
    cursor: "pointer",
  } as React.CSSProperties,
  noteRailItemActive: {
    background: pwc.grey100,
    color: pwc.grey900,
  } as React.CSSProperties,
  noteRailNumber: {
    fontFamily: pwc.fontMono,
    fontSize: 10,
    color: pwc.grey500,
    textAlign: "center" as const,
  } as React.CSSProperties,
  noteRailCopy: {
    minWidth: 0,
    display: "flex",
    flexDirection: "column" as const,
    gap: 1,
  } as React.CSSProperties,
  noteRailLabel: {
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap" as const,
    fontSize: 11,
    fontWeight: 600,
  } as React.CSSProperties,
  noteRailDestination: {
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap" as const,
    fontSize: 9.5,
    color: pwc.grey500,
  } as React.CSSProperties,
  noteRailAttention: {
    width: 16,
    height: 16,
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    borderRadius: 4,
    color: pwc.orange700,
    background: pwc.orange50,
    fontSize: 10,
    fontWeight: 700,
  } as React.CSSProperties,
  noteRailPlacements: {
    display: "flex",
    flexDirection: "column" as const,
    gap: 2,
    padding: `0 ${pwc.space.sm}px ${pwc.space.xs}px 35px`,
  } as React.CSSProperties,
  noteRailPlacementButton: {
    minHeight: 28,
    padding: `${pwc.space.xs}px ${pwc.space.sm}px`,
    border: 0,
    borderRadius: pwc.radius.sm,
    background: pwc.grey50,
    color: pwc.grey700,
    fontSize: 9.5,
    textAlign: "left" as const,
    cursor: "pointer",
  } as React.CSSProperties,
  noteRailEmpty: {
    margin: 0,
    padding: `${pwc.space.md}px ${pwc.space.sm}px`,
    color: pwc.grey700,
    fontSize: 11,
    lineHeight: 1.45,
  } as React.CSSProperties,
  editorPane: {
    minWidth: 0,
    paddingLeft: pwc.space.lg,
  } as React.CSSProperties,
  workspaceToolbar: {
    position: "sticky" as const,
    top: 0,
    zIndex: 20,
    minHeight: 48,
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    gap: pwc.space.md,
    padding: `${pwc.space.sm}px 0 ${pwc.space.md}px`,
    background: pwc.white,
    borderBottom: `1px solid ${pwc.grey100}`,
  } as React.CSSProperties,
  workspaceSheetNav: {
    display: "flex",
    alignItems: "center",
    gap: pwc.space.xs,
    minWidth: 0,
    overflowX: "auto" as const,
  } as React.CSSProperties,
  workspaceSheetButton: {
    minHeight: 34,
    padding: `0 ${pwc.space.md}px`,
    border: "none",
    borderRadius: pwc.radius.sm,
    background: "transparent",
    color: pwc.grey700,
    fontSize: 12,
    fontWeight: 600,
    whiteSpace: "nowrap" as const,
    cursor: "pointer",
  } as React.CSSProperties,
  workspaceSheetButtonActive: {
    minHeight: 34,
    padding: `0 ${pwc.space.md}px`,
    border: "none",
    borderRadius: pwc.radius.sm,
    background: pwc.grey100,
    color: pwc.grey900,
    fontSize: 12,
    fontWeight: 650,
    whiteSpace: "nowrap" as const,
    cursor: "pointer",
  } as React.CSSProperties,
  actionsMenu: {
    position: "relative" as const,
    flexShrink: 0,
  } as React.CSSProperties,
  actionsMenuSummary: {
    width: 34,
    minHeight: 34,
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    borderRadius: pwc.radius.sm,
    color: pwc.grey700,
    cursor: "pointer",
    listStyle: "none",
    fontWeight: 700,
  } as React.CSSProperties,
  actionsMenuPanel: {
    position: "absolute" as const,
    top: 40,
    right: 0,
    zIndex: 30,
    width: 210,
    padding: pwc.space.xs,
    border: `1px solid ${pwc.grey200}`,
    borderRadius: pwc.radius.md,
    background: pwc.white,
    boxShadow: pwc.shadow.elevated,
  } as React.CSSProperties,
  actionsMenuItem: {
    width: "100%",
    minHeight: 36,
    padding: `0 ${pwc.space.sm}px`,
    border: "none",
    borderRadius: pwc.radius.sm,
    background: "transparent",
    color: pwc.grey900,
    textAlign: "left" as const,
    fontSize: 12,
    cursor: "pointer",
  } as React.CSSProperties,
  regenerateButton: {
    ...ui.buttonGhost,
    ...ui.buttonSm,
  } as React.CSSProperties,
  // Per-run "Table style" panel (docs/PLAN-notes-table-theme.md).
  stylePanel: {
    border: "none",
    borderRadius: 0,
    background: pwc.grey50,
    padding: 12,
    marginBottom: 12,
  } as React.CSSProperties,
  stylePanelHint: {
    fontSize: 12,
    color: pwc.grey700,
    margin: "0 0 10px 0",
  } as React.CSSProperties,
  dim: {
    color: pwc.grey700,
    fontSize: 13,
  } as React.CSSProperties,
  // Navigator chip bar — one chip per sheet, jumps to + opens that
  // section. Wraps on narrow widths. Sits above the stack.
  sheetStack: {
    display: "flex",
    flexDirection: "column" as const,
    gap: 10,
  } as React.CSSProperties,
  // Each sheet is a flat section. A neutral bottom divider carries the section
  // boundary without reintroducing the old card or active orange-rail style.
  workspaceSheetSection: {
    display: "flex",
    flexDirection: "column" as const,
    borderBottom: `1px solid ${pwc.grey200}`,
    background: pwc.white,
    contentVisibility: "auto" as const,
    containIntrinsicSize: "560px",
  } as React.CSSProperties,
  // The <h4> wrapper strips default browser margins so the button
  // fills the card header cleanly.
  sheetHeadingWrap: {
    margin: 0,
    minWidth: 0,
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
  sheetFormatButton: {
    ...ui.buttonGhost,
    ...ui.buttonSm,
    flexShrink: 0,
  } as React.CSSProperties,
  formatSummary: {
    display: "flex",
    alignItems: "center",
    gap: 10,
    padding: "7px 14px",
    borderTop: `1px solid ${pwc.grey200}`,
    background: pwc.white,
    fontSize: 12,
  } as React.CSSProperties,
  formatSummaryText: {
    flex: 1,
    minWidth: 0,
  } as React.CSSProperties,
  formattingBanner: {
    padding: "7px 14px",
    borderTop: `1px solid ${pwc.grey200}`,
    background: pwc.grey100,
    fontSize: 12,
    color: pwc.grey700,
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
  workspaceCellRow: {
    display: "grid",
    gridTemplateColumns: "minmax(150px, 190px) minmax(0, 1fr)",
    gap: pwc.space.lg,
    padding: `${pwc.space.lg}px ${pwc.space.xs}px`,
    borderBottom: `1px solid ${pwc.grey100}`,
    background: pwc.white,
    scrollMarginTop: 64,
  } as React.CSSProperties,
  workspaceCellRowSelected: {
    background: pwc.grey100,
  } as React.CSSProperties,
  workspaceReadonlySurface: {
    minHeight: 56,
    padding: `${pwc.space.sm}px ${pwc.space.md}px`,
    border: `1px solid ${pwc.grey200}`,
    borderRadius: pwc.radius.sm,
    color: pwc.grey900,
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
  styleSourceChip: {
    display: "inline-block",
    marginTop: 4,
    padding: "1px 6px",
    borderRadius: 8,
    border: `1px solid ${pwc.grey300}`,
    background: pwc.grey100,
    color: pwc.grey700,
    fontSize: 10,
    fontWeight: 600,
    letterSpacing: 0.2,
    whiteSpace: "nowrap" as const,
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
  formatAdjustedNotice: {
    padding: "2px 5px",
    fontSize: 10,
    fontWeight: 600,
    color: pwc.grey700,
    background: pwc.grey100,
    border: `1px solid ${pwc.grey200}`,
    borderRadius: 3,
  } as React.CSSProperties,
  copiedChip: {
    fontSize: 11,
    fontWeight: 600,
    color: pwc.success,
    textTransform: "uppercase" as const,
    letterSpacing: 0.4,
  } as React.CSSProperties,
} as const;
