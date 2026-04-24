// Notes Review tab — post-run WYSIWYG editor for notes_cells rows.
//
// Covers Steps 9–12 of docs/PLAN-NOTES-RICH-EDITOR.md:
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
} from "react";
import { useEditor, EditorContent } from "@tiptap/react";
import type { Editor } from "@tiptap/react";
import { StarterKit } from "@tiptap/starter-kit";
import { Table } from "@tiptap/extension-table";
import { TableRow } from "@tiptap/extension-table-row";
import { TableHeader } from "@tiptap/extension-table-header";
import { TableCell } from "@tiptap/extension-table-cell";
import { pwc } from "../lib/theme";
import {
  fetchNotesCells,
  patchNotesCell,
  sortSheetsBySlot,
  type NotesCell,
  type NotesSheet,
} from "../lib/notesCells";
import { copyHtmlAsRichText } from "../lib/clipboard";
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
}

/** Editor lifecycle status chip per cell. */
type SaveStatus = "idle" | "dirty" | "saving" | "saved" | "failed";

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
 *  strips — the sanitiser-warning chip surfaces the removal, but the
 *  editor UI still suggested the format was supported. We disable the
 *  mismatched extensions here to keep the editor's affordances
 *  aligned with what the backend will accept. If we want to support
 *  these formats in the future, we extend ALLOWED_TAGS and re-enable
 *  here — both sides in one review. */
const TIPTAP_EXTENSIONS = [
  StarterKit.configure({
    code: false,
    codeBlock: false,
    blockquote: false,
    horizontalRule: false,
  }),
  Table.configure({ resizable: false }),
  TableRow,
  TableHeader,
  TableCell,
];

export function NotesReviewTab({ runId, onRegenerate }: NotesReviewTabProps) {
  // sheets / loading / error are the basic fetch lifecycle. We keep them
  // at the tab level (not in the individual cell editor) so one network
  // failure surfaces in a single banner instead of per-cell flicker.
  const [sheets, setSheets] = useState<NotesSheet[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  // Regenerate-notes confirm modal state. `pendingCount` is populated
  // by the edited_count fetch; a truthy value opens the dialog and
  // stores the message "This will overwrite N edited cells".
  const [pendingCount, setPendingCount] = useState<number | null>(null);

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
}: {
  runId: number;
  sheet: NotesSheet;
}) {
  // Collapsed by default so a run with 3-5 sheets doesn't mount every
  // TipTap editor on first paint. Matches the agent-card pattern above
  // in RunDetailView — reviewer clicks the heading to reveal rows.
  const [expanded, setExpanded] = useState(false);
  const rowCount = sheet.rows.length;

  return (
    <section style={styles.sheetSection}>
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
          >
            {sheet.sheet}
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
          {sheet.rows.map((cell) => (
            // Include `runId` in the CellRow key as well — belt-and-
            // braces after the parent sheet remount. Ensures TipTap
            // editor instances never carry state across a runId change.
            <CellRow
              key={`${runId}:${sheet.sheet}:${cell.row}`}
              runId={runId}
              sheet={sheet.sheet}
              cell={cell}
            />
          ))}
        </div>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------
// Cell row — label + evidence on the left, editor + actions on the right.
// ---------------------------------------------------------------------------

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
  // Peer-review [MEDIUM] #4: the backend strips disallowed tags/attrs
  // from the HTML on save and returns the list of removals in
  // `sanitizer_warnings`. Surface them here so a user who pasted a
  // `<script>` or `style=` knows their markup was altered, rather than
  // silently seeing their content swap to the cleaned form.
  const [sanitizerWarnings, setSanitizerWarnings] = useState<string[]>([]);
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
    onUpdate: ({ editor: ed }) => {
      const nextHtml = ed.getHTML();
      liveHtmlRef.current = nextHtml;
      // Guard against TipTap's initial setContent-driven `onUpdate`
      // during mount (which would otherwise schedule a spurious PATCH
      // for every cell even though the user never touched it). We only
      // schedule a save when the content actually diverged from the
      // last persisted HTML.
      if (nextHtml === savedHtmlRef.current) return;
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

  const scheduleSave = useCallback(() => {
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    saveTimerRef.current = setTimeout(async () => {
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
        // Track the persisted form so a subsequent re-render of the
        // editor with the same HTML does not re-mark the cell dirty.
        savedHtmlRef.current = updated.html;
        liveHtmlRef.current = updated.html;
        // Surface sanitiser warnings (peer-review [MEDIUM] #4). An
        // empty or missing list clears any prior warning — the row
        // only shows warnings for the most recent save, not forever.
        setSanitizerWarnings(updated.sanitizer_warnings ?? []);
        // Reconcile the server-sanitised form back into the editor
        // (peer-review [HIGH]). Without this, the Copy button would
        // continue to emit the user's raw markup (with style attrs,
        // disallowed tags, etc) even though the server has a cleaned
        // version. Skip when the editor already matches — that avoids
        // a spurious cursor reset on the common round-trip where the
        // sanitiser was a no-op.
        if (editor && editor.getHTML() !== updated.html) {
          const sel = editor.state.selection;
          editor.commands.setContent(updated.html, { emitUpdate: false });
          // Best-effort selection restore. ProseMirror clamps out-of-
          // range positions so a shorter doc after sanitisation won't
          // throw; the try/catch is belt-and-braces for older tiptap.
          try {
            editor.commands.setTextSelection({ from: sel.from, to: sel.to });
          } catch {
            /* positions invalid after sanitisation — harmless */
          }
        }
        setStatus("saved");
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

  const handleCopy = useCallback(async () => {
    const html = editor?.getHTML() ?? cell.html;
    const ok = await copyHtmlAsRichText(html);
    if (ok) setCopiedAt(Date.now());
  }, [editor, cell.html]);

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
          {editable && editor && <FormatToolbar editor={editor} />}
          <div style={styles.cellToolbarSpacer} />
          <SaveStatusBadge status={status} />
          {copiedAt !== null && (
            <span style={styles.copiedChip}>Copied</span>
          )}
          <button
            type="button"
            style={styles.smallButton}
            onClick={() => setEditable((v) => !v)}
          >
            {editable ? "Done" : "Edit"}
          </button>
          <button
            type="button"
            style={styles.smallButton}
            onClick={handleCopy}
          >
            Copy
          </button>
        </div>
        <div data-testid="notes-review-editor">
          <EditorContent editor={editor} />
        </div>
        {sanitizerWarnings.length > 0 && (
          <div
            data-testid="sanitizer-warning"
            role="status"
            aria-live="polite"
            style={styles.sanitizerWarning}
          >
            <span style={styles.sanitizerWarningLabel}>
              Sanitiser removed content:
            </span>
            <ul style={styles.sanitizerWarningList}>
              {sanitizerWarnings.map((w, i) => (
                <li key={i}>{w}</li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Formatting toolbar — Bold / Italic / Lists / H3 / Table.
// ---------------------------------------------------------------------------

function FormatToolbar({ editor }: { editor: Editor }) {
  const btn = (
    label: string,
    onClick: () => void,
    isActive?: boolean,
  ) => (
    <button
      key={label}
      type="button"
      aria-label={label}
      style={isActive ? styles.toolbarButtonActive : styles.toolbarButton}
      onClick={onClick}
    >
      {label}
    </button>
  );
  return (
    <div role="toolbar" aria-label="Format" style={styles.toolbar}>
      {btn("Bold", () => editor.chain().focus().toggleBold().run(), editor.isActive("bold"))}
      {btn("Italic", () => editor.chain().focus().toggleItalic().run(), editor.isActive("italic"))}
      {btn("• List", () => editor.chain().focus().toggleBulletList().run(), editor.isActive("bulletList"))}
      {btn("1. List", () => editor.chain().focus().toggleOrderedList().run(), editor.isActive("orderedList"))}
      {btn("H3", () => editor.chain().focus().toggleHeading({ level: 3 }).run(), editor.isActive("heading", { level: 3 }))}
      {btn("Table", () =>
        editor
          .chain()
          .focus()
          .insertTable({ rows: 2, cols: 2, withHeaderRow: true })
          .run(),
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
            style={styles.modalCancelButton}
            onClick={onCancel}
          >
            Cancel
          </button>
          <button
            type="button"
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
    padding: "8px 14px",
    fontFamily: pwc.fontHeading,
    fontSize: 13,
    fontWeight: 600,
    color: pwc.orange500,
    background: pwc.white,
    border: `1px solid ${pwc.orange500}`,
    borderRadius: 4,
    cursor: "pointer",
  } as React.CSSProperties,
  errorBanner: {
    padding: "8px 12px",
    background: "#fff1f2",
    color: "#991b1b",
    border: "1px solid #fecaca",
    borderRadius: 4,
    fontSize: 13,
  } as React.CSSProperties,
  dim: {
    color: pwc.grey700,
    fontSize: 13,
  } as React.CSSProperties,
  sheetStack: {
    display: "flex",
    flexDirection: "column" as const,
    gap: 10,
  } as React.CSSProperties,
  // Card chrome matching the agent cards in RunDetailView — white
  // background, grey border, rounded corners. Gives each sheet a clear
  // visual boundary the way face-statement agent cards do.
  sheetSection: {
    display: "flex",
    flexDirection: "column" as const,
    border: `1px solid ${pwc.grey200}`,
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
  sheetHeadingButton: {
    display: "flex",
    alignItems: "center",
    gap: 10,
    width: "100%",
    padding: "10px 14px",
    background: "transparent",
    border: "none",
    cursor: "pointer",
    fontFamily: "inherit",
    font: "inherit",
    color: "inherit",
    textAlign: "left" as const,
  } as React.CSSProperties,
  sheetHeadingText: {
    fontFamily: pwc.fontMono,
    fontSize: 13,
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
    gap: 10,
    padding: 14,
    borderTop: `1px solid ${pwc.grey100}`,
  } as React.CSSProperties,
  cellRow: {
    display: "grid",
    gridTemplateColumns: "220px 1fr",
    gap: 16,
    padding: 12,
    border: `1px solid ${pwc.grey200}`,
    borderRadius: 6,
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
  sanitizerWarning: {
    marginTop: 6,
    padding: "6px 8px",
    background: "#fff8e1",
    border: "1px solid #f59e0b",
    borderRadius: 3,
    fontSize: 12,
    color: "#78350f",
    display: "flex",
    flexDirection: "column" as const,
    gap: 2,
  } as React.CSSProperties,
  sanitizerWarningLabel: {
    textTransform: "uppercase" as const,
    letterSpacing: 0.3,
    fontWeight: 600,
    fontSize: 10,
  } as React.CSSProperties,
  sanitizerWarningList: {
    margin: 0,
    paddingLeft: 16,
    fontSize: 12,
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
  toolbar: {
    display: "flex",
    gap: 2,
    flexWrap: "wrap" as const,
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
  statusBadge: {
    fontSize: 11,
    fontWeight: 600,
    textTransform: "uppercase" as const,
    letterSpacing: 0.4,
  } as React.CSSProperties,
  copiedChip: {
    fontSize: 11,
    fontWeight: 600,
    color: "#059669",
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
    fontWeight: 600,
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
    padding: "8px 14px",
    fontSize: 13,
    fontWeight: 600,
    background: pwc.white,
    border: `1px solid ${pwc.grey200}`,
    borderRadius: 4,
    color: pwc.grey700,
    cursor: "pointer",
  } as React.CSSProperties,
  modalConfirmButton: {
    padding: "8px 14px",
    fontSize: 13,
    fontWeight: 600,
    background: pwc.orange500,
    border: `1px solid ${pwc.orange500}`,
    borderRadius: 4,
    color: pwc.white,
    cursor: "pointer",
  } as React.CSSProperties,
} as const;
