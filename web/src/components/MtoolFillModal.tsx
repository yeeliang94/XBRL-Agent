import React, { useEffect, useRef, useState } from "react";
import { userMessage } from "../lib/errors";
import { pwc } from "../lib/theme";
import { ui, uiClass } from "../lib/uiStyles";
import { STATUS_SYMBOLS } from "../lib/runStatus";
import { denominationLabel } from "../lib/vocabulary";
import { FileDropzone } from "./FileDropzone";

/**
 * mTool fill modal (docs/PLAN.md Phase 4, Steps 9/11).
 *
 * Launched from the run-detail action row (a button, NOT a tab — gotcha #7).
 * Two steps in one dialog:
 *   1. Show the fill coverage the run will produce (writes + excluded counts),
 *      fetched from GET /api/runs/{id}/mtool-fill.
 *   2. Upload the operator's empty mTool template; POST it to
 *      /mtool-fill/patch; download the returned filled workbook and show the
 *      run report so the operator sees "clean" before taking it to mTool.
 */

interface FillMeta {
  counts: {
    writes: number;
    conflict_writes: number;
    excluded_matrix_socie: number;
    excluded_not_disclosed: number;
    excluded_out_of_scope: number;
    excluded_no_value: number;
  };
  sheets_covered: string[];
  filing_standard: string;
  filing_level: string;
  denomination: string | null;
}

interface NotesReport {
  status: string;
  // True when the operator chose the diagnostic "no styling" fill.
  styling_disabled?: boolean;
  counts: {
    written: number;
    created: number;
    unresolved: number;
    mismatches: number;
    errors: number;
    // Size-degradation tiers: compacted keeps the same look with slimmer
    // styling; reduced drops cosmetics; dropped writes the note unstyled.
    formatting_compacted?: number;
    formatting_reduced?: number;
    formatting_dropped?: number;
  };
  unresolved?: { label: string | null; detail?: string }[];
}

// One place a flagged note could be assigned to (backend candidates entry):
// either an existing text-block slot (key) or a visible cell a slot would be
// created at (sheet+cell).
interface NoteCandidate {
  key?: string;
  sheet?: string;
  cell?: string;
  label_cell?: string;
  matched_label?: string;
}

// A note the fill refused to guess on. `reason` drives the guidance UI;
// `index` is the stable id notes_targets decisions are keyed by.
interface UnresolvedNote {
  index?: number;
  label: string | null;
  detail?: string;
  reason?: string; // ambiguous | strict_near_miss | no_match | no_slot | no_payload_row
  candidates?: NoteCandidate[];
  matched_label?: string;
  ratio?: number;
  key?: string;
  sheet?: string;
  cell?: string;
}

// Dry-run notes diagnostic (POST /mtool-fill/notes-preview).
interface NotesPreview {
  notes_in_run: number;
  template_fn_slots: number;
  create_missing_notes: boolean;
  will_fill_existing: { index?: number; label: string | null; key: string }[];
  will_create: { index?: number; label: string | null; cell: string | null; label_cell: string | null }[];
  unresolved: UnresolvedNote[];
  errors: { detail?: string }[];
}

// The operator's placement decision for one flagged note, sent to the server
// as notes_targets — pin to an existing slot (key) or an explicit cell.
type NoteTarget = { key?: string; sheet?: string; cell?: string };

interface ReportSummary {
  status: string;
  numeric_status?: string;
  counts: Record<string, number>;
  unresolved: { sheet: string; label: string | null; detail?: string }[];
  skipped_formula: { sheet: string; cell?: string; label: string | null }[];
  mismatches: { cell: string; expected: string; found: string | null }[];
  notes?: NotesReport;
}

// Server's low-confidence auto-detection payload (422 detail.detected).
interface DetectedSheet {
  label_column: string | null;
  columns: Record<string, string>;
  confidence: string;
  notes: string[];
}
// The editable column map the user confirms/edits, sent back as column_map.
type ColumnMap = Record<string, { label_column: string; columns: Record<string, string> }>;

interface Props {
  runId: number;
  open: boolean;
  onClose: () => void;
}

const styles = {
  overlay: {
    ...ui.scrim,
  } as React.CSSProperties,
  modal: {
    ...ui.dialog,
    // Responsive: fill most of the viewport up to a comfortable cap so the
    // notes-preview cell references and column editor stop wrapping (they were
    // cramped at the old fixed 560px).
    maxWidth: "min(1040px, 92vw)",
    overflowY: "auto" as const,
  } as React.CSSProperties,
  headerRow: {
    display: "flex",
    alignItems: "flex-start",
    justifyContent: "space-between",
    gap: pwc.space.md,
  } as React.CSSProperties,
  heading: {
    fontFamily: pwc.fontHeading,
    fontWeight: pwc.weight.medium,
    fontSize: 18,
    color: pwc.grey900,
    margin: 0,
    marginBottom: pwc.space.md,
  } as React.CSSProperties,
  closeX: {
    border: "none",
    background: "transparent",
    color: pwc.grey500,
    fontSize: 18,
    lineHeight: 1,
    cursor: "pointer",
    padding: pwc.space.xs,
  } as React.CSSProperties,
  sub: {
    fontSize: 13,
    color: pwc.grey700,
    margin: `0 0 ${pwc.space.lg}px`,
    lineHeight: 1.5,
  } as React.CSSProperties,
  statLine: {
    fontSize: 13,
    color: pwc.grey900,
    margin: `2px 0`,
  } as React.CSSProperties,
  actions: {
    display: "flex",
    gap: pwc.space.sm,
    justifyContent: "flex-end",
    marginTop: pwc.space.xl,
  } as React.CSSProperties,
  planSummary: {
    display: "flex",
    alignItems: "center",
    gap: pwc.space.md,
    flexWrap: "wrap" as const,
    margin: `${pwc.space.sm}px 0`,
  } as React.CSSProperties,
  planChip: {
    display: "inline-flex",
    alignItems: "center",
    gap: 6,
    fontSize: 12,
    color: pwc.grey900,
  } as React.CSSProperties,
  noteCard: {
    borderTop: `1px solid ${pwc.grey200}`,
    padding: `${pwc.space.sm}px 0`,
    fontSize: 12,
  } as React.CSSProperties,
};

/** One group of the notes plan ("Ready to fill", "Needs your decision", …) —
 * a collapsible section with a status dot + count so the three outcomes never
 * blur into one undifferentiated list. */
function PlanSection({
  symbol,
  title,
  count,
  defaultOpen,
  hint,
  children,
}: {
  symbol: string;
  title: string;
  count: number;
  defaultOpen?: boolean;
  hint?: string;
  children: React.ReactNode;
}) {
  if (count === 0) return null;
  return (
    <details open={defaultOpen} style={{ marginTop: pwc.space.sm }}>
      <summary
        style={{
          cursor: "pointer",
          display: "flex",
          alignItems: "center",
          gap: 8,
          fontSize: 13,
          fontWeight: pwc.weight.medium,
          color: pwc.grey900,
        }}
      >
        <span aria-hidden="true" style={ui.statusSymbol}>{symbol}</span>
        {title}
        <span style={{ color: pwc.grey500, fontWeight: pwc.weight.regular }}>({count})</span>
      </summary>
      {hint && (
        <div style={{ color: pwc.grey700, fontSize: 12, margin: "4px 0 0 15px" }}>{hint}</div>
      )}
      <div style={{ margin: "4px 0 2px 15px" }}>{children}</div>
    </details>
  );
}

/** Plain-language explanation of why a note wasn't placed automatically. */
function unresolvedReasonText(u: UnresolvedNote): string {
  switch (u.reason) {
    case "ambiguous":
      return "This title appears in more than one place in the template — choose where it should go.";
    case "strict_near_miss":
      return `Found a close (but not identical) match: “${u.matched_label ?? "?"}”. To avoid guessing, it wasn't filled automatically.`;
    case "no_match":
      return "No matching row was found in this template. It will be skipped — you can add the note in mTool afterwards.";
    case "no_slot":
      return "This note has no spot in the template yet. Turn on “Add missing note spots” above to add one.";
    case "no_payload_row":
      return "The template is missing the hidden row this note is stored in — fill it manually in mTool.";
    default:
      return u.detail ?? "Couldn't be placed automatically.";
  }
}

/** The near-miss suggestion as a notes_targets decision, if the entry carries
 * one (existing slot key, or the visible cell a slot would be created at). */
function suggestionTarget(u: UnresolvedNote): NoteTarget | null {
  if (u.key) return { key: u.key };
  if (u.sheet && u.cell) return { sheet: u.sheet, cell: u.cell };
  return null;
}

/** Human-readable name for a candidate placement in the picker. */
function candidateOptionLabel(c: NoteCandidate): string {
  const where = c.key ? `existing note spot ${c.key}` : `${c.sheet} ${c.cell}`;
  return c.matched_label ? `${where} — ${c.matched_label}` : where;
}

/** Turn the server's detected layout (per-sheet detection with confidence)
 *  into the editable ColumnMap the user confirms and we send back. Shared by
 *  the up-front detect pre-flight and the low-confidence 422 fallback. */
function detectedToColumnMap(
  detected: Record<string, DetectedSheet>,
): ColumnMap {
  const seed: ColumnMap = {};
  for (const [sheet, d] of Object.entries(detected)) {
    seed[sheet] = { label_column: d.label_column ?? "", columns: { ...d.columns } };
  }
  return seed;
}

export function MtoolFillModal({ runId, open, onClose }: Props) {
  const [meta, setMeta] = useState<FillMeta | null>(null);
  const [notesCount, setNotesCount] = useState<number | null>(null);
  const [fillNotes, setFillNotes] = useState(true);
  const [createMissingNotes, setCreateMissingNotes] = useState(false);
  // Note styling mode: "styled" (default, recommended) or "none" — the
  // diagnostic fill that writes words + table structure with no formatting,
  // so an operator can isolate whether a fill problem is styling-related.
  const [notesStyling, setNotesStyling] = useState<"styled" | "none">("styled");
  const [preview, setPreview] = useState<NotesPreview | null>(null);
  const [previewBusy, setPreviewBusy] = useState(false);
  const [previewErr, setPreviewErr] = useState<string | null>(null);
  // The operator's placement decisions for flagged notes, keyed by the note's
  // index in the run's notes doc (the preview's stable id). Sent as
  // notes_targets on both re-check and fill.
  const [noteTargets, setNoteTargets] = useState<Record<number, NoteTarget>>({});
  // Set when the server needs the column layout confirmed — a next step, not
  // a failure, so it renders as guidance rather than a red error.
  const [columnPrompt, setColumnPrompt] = useState<string | null>(null);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [report, setReport] = useState<ReportSummary | null>(null);
  const [patchErr, setPatchErr] = useState<string | null>(null);
  // The column layout — detected UP FRONT the moment a template is chosen
  // (POST /mtool-fill/detect-columns) so the operator confirms columns
  // alongside the notes check, not after a failed Fill. Editable; sent as
  // column_map on Fill. The submit path still handles a low-confidence 422 as
  // a defensive fallback for the rare case detection wasn't run.
  const [columnMap, setColumnMap] = useState<ColumnMap | null>(null);
  const [columnConfidence, setColumnConfidence] = useState<string | null>(null);
  const [detectBusy, setDetectBusy] = useState(false);
  const [detectErr, setDetectErr] = useState<string | null>(null);
  // Monotonic token so a slow column-detect for template A can't land its
  // result after the user has switched to template B (a stale columnMap would
  // be sent as an explicit override and MIS-TARGET writes). Bumped on every
  // file change; runDetect ignores its own response once superseded.
  const detectSeq = useRef(0);

  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [open, onClose]);

  useEffect(() => {
    if (!open) return;
    setMeta(null);
    setNotesCount(null);
    setNotesStyling("styled");
    setLoadErr(null);
    setFile(null);
    setReport(null);
    setPatchErr(null);
    setColumnMap(null);
    setColumnConfidence(null);
    setDetectErr(null);
    setColumnPrompt(null);
    setPreview(null);
    setPreviewErr(null);
    setNoteTargets({});
    fetch(`/api/runs/${runId}/mtool-fill`)
      .then(async (r) => {
        if (!r.ok) throw new Error((await r.json()).detail || `HTTP ${r.status}`);
        return r.json();
      })
      .then((doc) => setMeta(doc.meta))
      .catch((e) => setLoadErr(userMessage(e)));
    // Notes count is best-effort — a load failure just hides the notes line.
    fetch(`/api/runs/${runId}/mtool-notes-fill`)
      .then((r) => (r.ok ? r.json() : null))
      .then((doc) => setNotesCount(doc?.meta?.counts?.notes ?? null))
      .catch(() => setNotesCount(null));
  }, [open, runId]);

  if (!open) return null;

  const notesTargetsPayload = () =>
    Object.keys(noteTargets).length > 0 ? JSON.stringify(noteTargets) : null;

  const submit = async () => {
    if (!file) return;
    setBusy(true);
    setPatchErr(null);
    setColumnPrompt(null);
    setReport(null);
    try {
      const form = new FormData();
      form.append("template", file);
      form.append("strict", "true");
      form.append("fill_notes", fillNotes ? "true" : "false");
      form.append("create_missing_notes", createMissingNotes ? "true" : "false");
      if (fillNotes) form.append("notes_styling", notesStyling);
      if (columnMap) form.append("column_map", JSON.stringify(columnMap));
      const targets = fillNotes ? notesTargetsPayload() : null;
      if (targets) form.append("notes_targets", targets);
      const resp = await fetch(`/api/runs/${runId}/mtool-fill/patch`, {
        method: "POST",
        body: form,
      });
      if (!resp.ok) {
        const body = await resp.json().catch(() => ({}));
        const detail = body?.detail;
        // Low-confidence auto-detection: the server hands back its best guess
        // in detail.detected. Seed the editor so the user can confirm + retry.
        // This is a guided next step, not a failure — don't paint it red.
        if (detail && typeof detail === "object" && detail.detected) {
          setColumnMap(detectedToColumnMap(detail.detected as Record<string, DetectedSheet>));
          setColumnConfidence("low");
          setColumnPrompt(
            "One more step — we couldn't tell for sure which columns hold your labels and figures. " +
              "Check the columns below (we've pre-filled our best guess), then click Fill & download again."
          );
          return;
        }
        throw new Error(
          typeof detail === "string" ? detail : detail ? JSON.stringify(detail) : `HTTP ${resp.status}`
        );
      }
      // Trigger the filled-workbook download FIRST — it's the payload the user
      // came for. The report header is a nice-to-have summary.
      const blob = await resp.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `mtool_filled_run${runId}.xlsx`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      // Parse the report header AFTER the download so a malformed/clipped
      // header (e.g. a proxy truncating it) can't turn a successful fill into a
      // "Fill failed" error — treat a parse failure as "report unavailable".
      const header = resp.headers.get("X-mTool-Report");
      if (header) {
        try {
          setReport(JSON.parse(header) as ReportSummary);
        } catch {
          setReport(null);
        }
      }
    } catch (e) {
      setPatchErr(userMessage(e));
    } finally {
      setBusy(false);
    }
  };

  // Dry-run diagnostic: what would fill / get created / stay unresolved, and
  // how many fn_* slots the uploaded template exposes. Writes nothing. Sends
  // the operator's placement decisions so a re-check reflects them.
  const runPreview = async () => {
    if (!file) return;
    setPreviewBusy(true);
    setPreviewErr(null);
    setPreview(null);
    try {
      const form = new FormData();
      form.append("template", file);
      form.append("create_missing_notes", createMissingNotes ? "true" : "false");
      form.append("notes_styling", notesStyling);
      const targets = notesTargetsPayload();
      if (targets) form.append("notes_targets", targets);
      const resp = await fetch(`/api/runs/${runId}/mtool-fill/notes-preview`, {
        method: "POST",
        body: form,
      });
      const body = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        const detail = (body as { detail?: unknown })?.detail;
        throw new Error(typeof detail === "string" ? detail : `HTTP ${resp.status}`);
      }
      setPreview(body as NotesPreview);
    } catch (e) {
      setPreviewErr(userMessage(e));
    } finally {
      setPreviewBusy(false);
    }
  };

  // Up-front column pre-flight: detect the template's layout the moment a file
  // is chosen so the operator confirms columns alongside the notes check,
  // instead of hitting a post-submit 422. Writes nothing.
  const runDetect = async (f: File) => {
    const seq = ++detectSeq.current;
    const stale = () => seq !== detectSeq.current;
    setDetectBusy(true);
    setDetectErr(null);
    setColumnMap(null);
    setColumnConfidence(null);
    setColumnPrompt(null);
    try {
      const form = new FormData();
      form.append("template", f);
      const resp = await fetch(`/api/runs/${runId}/mtool-fill/detect-columns`, {
        method: "POST",
        body: form,
      });
      const body = await resp.json().catch(() => ({}));
      if (stale()) return; // a newer file was chosen — drop this response
      if (!resp.ok) {
        const detail = (body as { detail?: unknown })?.detail;
        throw new Error(typeof detail === "string" ? detail : `HTTP ${resp.status}`);
      }
      const detected = (body as { detected?: Record<string, DetectedSheet> }).detected;
      if (detected) setColumnMap(detectedToColumnMap(detected));
      setColumnConfidence((body as { confidence?: string }).confidence ?? null);
    } catch (e) {
      if (stale()) return;
      setDetectErr(userMessage(e));
    } finally {
      if (!stale()) setDetectBusy(false);
    }
  };

  const c = meta?.counts;
  const totalExcluded = c
    ? c.excluded_matrix_socie + c.excluded_not_disclosed + c.excluded_out_of_scope + c.excluded_no_value
    : 0;

  return (
    <div
      style={styles.overlay}
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
      role="dialog"
      aria-modal="true"
      aria-label="Fill mTool template"
    >
      <div style={styles.modal}>
        <div style={styles.headerRow}>
          <h2 style={styles.heading}>Fill mTool template</h2>
          {/* Corner close — Esc + scrim-click already close, but a visible ✕
              is the discoverable affordance (E6). */}
          <button
            type="button"
            aria-label="Close"
            onClick={onClose}
            style={styles.closeX}
          >
            ✕
          </button>
        </div>
        <p style={styles.sub}>
          Upload the empty template you exported from mTool. We fill in this run&apos;s
          figures and written notes and give you back one file, ready to open in mTool
          for Validate &amp; Generate. Totals are left to mTool&apos;s own formulas; the
          statement of changes in equity (SOCIE) isn&apos;t filled yet.
        </p>

        {loadErr && (
          <div style={ui.alertError}>Could not load fill data: {loadErr}</div>
        )}

        {meta && (
          <div style={{ marginBottom: pwc.space.lg }}>
            <div style={styles.statLine}>
              <strong>{c!.writes}</strong> values will be written across{" "}
              {meta.sheets_covered.length} sheet(s) &middot; {meta.filing_standard.toUpperCase()}{" "}
              {meta.filing_level} &middot; denomination: {meta.denomination ? denominationLabel(meta.denomination) : "unknown"}
            </div>
            {totalExcluded > 0 && (
              <div style={{ ...styles.statLine, color: pwc.grey700 }}>
                Excluded: {c!.excluded_matrix_socie} SOCIE/matrix, {c!.excluded_not_disclosed}{" "}
                not-disclosed, {c!.excluded_out_of_scope} out-of-scope
              </div>
            )}
            {c!.conflict_writes > 0 && (
              <div style={{ ...styles.statLine, color: pwc.orange700 }}>
                ⚠ {c!.conflict_writes} value(s) still in conflict will be written — resolve
                them in Review values first.
              </div>
            )}
            {notesCount !== null && (
              <div style={styles.statLine}>
                <strong>{notesCount}</strong> written note(s) will be filled in
              </div>
            )}
          </div>
        )}

        {notesCount !== null && notesCount > 0 && (
          <label style={{ ...styles.statLine, display: "flex", alignItems: "center", gap: 6, marginBottom: 4 }}>
            <input
              type="checkbox"
              checked={fillNotes}
              onChange={(e) => {
                setFillNotes(e.target.checked);
                setPreview(null); // plan no longer reflects the toggles
                setPreviewErr(null);
                setNoteTargets({});
              }}
              aria-label="Also fill notes"
            />
            Also fill the written notes (accounting policies, disclosures)
          </label>
        )}

        {notesCount !== null && notesCount > 0 && fillNotes && (
          <label
            style={{ ...styles.statLine, display: "flex", alignItems: "flex-start", gap: 6, marginBottom: pwc.space.md }}
          >
            <input
              type="checkbox"
              checked={createMissingNotes}
              onChange={(e) => {
                setCreateMissingNotes(e.target.checked);
                setPreview(null); // create-toggle changes the plan
                setPreviewErr(null);
                setNoteTargets({});
              }}
              aria-label="Add missing note spots"
              style={{ marginTop: 2 }}
            />
            <span>
              Add missing note spots
              <span style={{ display: "block", color: pwc.grey700, fontSize: 12 }}>
                If a note has no spot in the template yet, add one next to its label.
                Off by default — run “Check notes” first, and verify the result opens
                correctly in mTool.
              </span>
            </span>
          </label>
        )}

        {notesCount !== null && notesCount > 0 && fillNotes && (
          <fieldset
            style={{
              border: `1px solid ${pwc.grey200}`,
              borderRadius: pwc.radius.md,
              padding: `${pwc.space.sm}px ${pwc.space.md}px`,
              margin: `0 0 ${pwc.space.md}px`,
            }}
            data-testid="notes-styling-options"
          >
            <legend style={{ fontSize: 12, color: pwc.grey700, padding: `0 4px` }}>
              Note styling
            </legend>
            <label style={{ ...styles.statLine, display: "flex", alignItems: "flex-start", gap: 6 }}>
              <input
                type="radio"
                name="notes-styling"
                checked={notesStyling === "styled"}
                onChange={() => setNotesStyling("styled")}
                aria-label="Styled notes (recommended)"
                style={{ marginTop: 2 }}
              />
              <span>
                Styled <span style={{ color: pwc.grey700 }}>(recommended)</span>
                <span style={{ display: "block", color: pwc.grey700, fontSize: 12 }}>
                  Notes look like they do here — table borders, header shading, aligned
                  numbers. A very large table automatically steps down to simpler styling
                  so it still fits mTool&apos;s size limit; the result below tells you if
                  that happened.
                </span>
              </span>
            </label>
            <label style={{ ...styles.statLine, display: "flex", alignItems: "flex-start", gap: 6 }}>
              <input
                type="radio"
                name="notes-styling"
                checked={notesStyling === "none"}
                onChange={() => setNotesStyling("none")}
                aria-label="No styling (diagnostic)"
                style={{ marginTop: 2 }}
              />
              <span>
                No styling <span style={{ color: pwc.grey700 }}>(diagnostic)</span>
                <span style={{ display: "block", color: pwc.grey700, fontSize: 12 }}>
                  Fills the words and table layout with no formatting at all. Use this to
                  test whether a problem with the filled file is caused by styling — not
                  for real filings.
                </span>
              </span>
            </label>
          </fieldset>
        )}

        <div style={{ marginBottom: pwc.space.md }}>
          <FileDropzone
            accept=".xlsx"
            label={
              file
                ? `Selected: ${file.name} — drop another to replace`
                : "Drop your empty mTool template (.xlsx) here or choose a file"
            }
            buttonLabel="Choose template"
            inputLabel="mTool template file"
            testId="mtool-template-dropzone"
            onFile={(f) => {
              detectSeq.current += 1; // invalidate any in-flight detect for the old file
              setFile(f);
              setColumnMap(null); // a different template has a different layout
              setColumnConfidence(null);
              setDetectErr(null);
              setDetectBusy(false);
              setColumnPrompt(null);
              setPreview(null); // a different template ⇒ a different plan
              setPreviewErr(null);
              setNoteTargets({}); // decisions were made against the old template
              runDetect(f); // confirm the column layout up front
            }}
          />
        </div>

        {detectBusy && (
          <div style={{ ...styles.statLine, color: pwc.grey700 }}>
            Checking the template's column layout…
          </div>
        )}
        {detectErr && (
          <div style={{ ...ui.alertError, marginBottom: pwc.space.md }}>
            Couldn&apos;t read the template&apos;s columns: {detectErr}
          </div>
        )}

        {notesCount !== null && notesCount > 0 && fillNotes && (
          <div style={{ marginBottom: pwc.space.md }}>
            <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
              <button
                type="button"
                onClick={runPreview}
                disabled={!file || previewBusy}
                className={uiClass.btnGhost}
                style={{ ...ui.buttonGhost, fontSize: 12 }}
                // Explain why it's inert before a file is chosen, rather than
                // looking like a silent no-op (E6).
                title={file ? undefined : "Choose a template file first"}
              >
                {previewBusy ? "Checking…" : "Check notes against this template"}
              </button>
              <span style={{ color: pwc.grey500, fontSize: 12 }}>
                {file
                  ? "Nothing is written — this only shows where each note would go."
                  : "Choose a template file above to enable this."}
              </span>
            </div>
            {previewErr && (
              <div style={{ ...ui.alertError, marginTop: pwc.space.sm }}>
                Check failed: {previewErr}
              </div>
            )}
            {preview && (
              <div
                style={{
                  border: `1px solid ${pwc.grey200}`,
                  borderRadius: pwc.radius.md,
                  padding: pwc.space.md,
                  marginTop: pwc.space.sm,
                  fontSize: 12,
                }}
                aria-label="Notes preview"
              >
                <div style={{ color: pwc.grey700 }}>
                  This template has <strong>{preview.template_fn_slots}</strong> note spot(s)
                  already set up; this run has <strong>{preview.notes_in_run}</strong> note(s) to place.
                </div>

                {/* One glanceable summary row — the three outcomes at a glance. */}
                <div style={styles.planSummary}>
                  <span style={styles.planChip}>
                    <span aria-hidden="true" style={ui.statusSymbol}>{STATUS_SYMBOLS.success}</span>
                    {preview.will_fill_existing.length} ready to fill
                  </span>
                  <span style={styles.planChip}>
                    <span aria-hidden="true" style={ui.statusSymbol}>
                      {createMissingNotes ? STATUS_SYMBOLS.derived : STATUS_SYMBOLS.inactive}
                    </span>
                    {preview.will_create.length} will be added
                  </span>
                  <span style={{ ...styles.planChip, color: preview.unresolved.length ? pwc.warningText : pwc.grey900 }}>
                    <span aria-hidden="true" style={ui.statusSymbol}>
                      {preview.unresolved.length ? STATUS_SYMBOLS.attention : STATUS_SYMBOLS.inactive}
                    </span>
                    {preview.unresolved.length} need your decision
                  </span>
                </div>

                {preview.errors.length > 0 && (
                  <div style={{ ...ui.alertError, marginTop: pwc.space.sm }}>
                    <div style={{ fontWeight: pwc.weight.medium }}>
                      {preview.errors.length} problem(s) would stop the notes from landing:
                    </div>
                    <ul style={{ margin: "4px 0 0", paddingLeft: 18 }}>
                      {preview.errors.slice(0, 4).map((e, i) => (
                        <li key={i}>{e.detail ?? "error"}</li>
                      ))}
                      {preview.errors.length > 4 && <li>… and {preview.errors.length - 4} more</li>}
                    </ul>
                  </div>
                )}

                {/* Notes that need a human call — each with a plain-language
                    reason and, where the tool found options, a picker. This is
                    the notes twin of the numeric column-layout confirm step. */}
                <PlanSection
                  symbol={STATUS_SYMBOLS.attention}
                  title="Needs your decision"
                  count={preview.unresolved.length}
                  defaultOpen
                  hint="The tool never guesses. Place these yourself, or leave them to fill manually in mTool later."
                >
                  {preview.unresolved.map((u, i) => {
                    const idx = u.index ?? -1;
                    const chosen = idx >= 0 ? noteTargets[idx] : undefined;
                    const suggestion = u.reason === "strict_near_miss" ? suggestionTarget(u) : null;
                    return (
                      <div key={i} style={styles.noteCard}>
                        <div style={{ fontWeight: pwc.weight.medium, color: pwc.grey900 }}>{u.label}</div>
                        <div style={{ color: pwc.grey700, margin: "2px 0 4px" }}>{unresolvedReasonText(u)}</div>
                        {u.reason === "ambiguous" && (u.candidates?.length ?? 0) > 0 && idx >= 0 && (
                          <select
                            aria-label={`Choose where “${u.label}” goes`}
                            value={chosen ? JSON.stringify(chosen) : ""}
                            onChange={(e) => {
                              const v = e.target.value;
                              setNoteTargets((t) => {
                                const next = { ...t };
                                if (v) next[idx] = JSON.parse(v) as NoteTarget;
                                else delete next[idx];
                                return next;
                              });
                            }}
                            style={{ fontSize: 12, maxWidth: "100%" }}
                          >
                            <option value="">Skip for now (not filled)</option>
                            {u.candidates!.map((cand, ci) => {
                              const target: NoteTarget = cand.key
                                ? { key: cand.key }
                                : { sheet: cand.sheet, cell: cand.cell };
                              return (
                                <option key={ci} value={JSON.stringify(target)}>
                                  Place at {candidateOptionLabel(cand)}
                                </option>
                              );
                            })}
                          </select>
                        )}
                        {suggestion && idx >= 0 && (
                          <label style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                            <input
                              type="checkbox"
                              checked={!!chosen}
                              aria-label={`Use the close match for “${u.label}”`}
                              onChange={(e) =>
                                setNoteTargets((t) => {
                                  const next = { ...t };
                                  if (e.target.checked) next[idx] = suggestion;
                                  else delete next[idx];
                                  return next;
                                })
                              }
                            />
                            Use this match
                          </label>
                        )}
                      </div>
                    );
                  })}
                  {Object.keys(noteTargets).length > 0 && (
                    <div style={{ marginTop: pwc.space.sm, display: "flex", alignItems: "center", gap: 8 }}>
                      <span style={{ color: pwc.grey700 }}>
                        {Object.keys(noteTargets).length} placed — applied when you fill.
                      </span>
                      <button
                        type="button"
                        onClick={runPreview}
                        disabled={previewBusy}
                        className={uiClass.btnGhost}
                        style={{ ...ui.buttonGhost, fontSize: 12 }}
                      >
                        Re-check
                      </button>
                    </div>
                  )}
                </PlanSection>

                <PlanSection
                  symbol={createMissingNotes ? STATUS_SYMBOLS.derived : STATUS_SYMBOLS.inactive}
                  title="Will be added"
                  count={preview.will_create.length}
                  hint="These notes have no spot in the template yet — one is created next to each label."
                >
                  <ul style={{ margin: 0, paddingLeft: 18, color: pwc.grey700 }}>
                    {preview.will_create.map((w, i) => (
                      <li key={i}>
                        {w.label ?? "(placed by you)"} → {w.cell}
                      </li>
                    ))}
                  </ul>
                </PlanSection>

                <PlanSection
                  symbol={STATUS_SYMBOLS.success}
                  title="Ready to fill"
                  count={preview.will_fill_existing.length}
                  hint="These match a spot that already exists in the template."
                >
                  <ul style={{ margin: 0, paddingLeft: 18, color: pwc.grey700 }}>
                    {preview.will_fill_existing.map((w, i) => (
                      <li key={i}>{w.label}</li>
                    ))}
                  </ul>
                </PlanSection>

                {!createMissingNotes && preview.unresolved.length > 0 && preview.will_create.length === 0 && (
                  <div style={{ color: pwc.grey700, marginTop: pwc.space.sm }}>
                    Tip: turning on “Add missing note spots” above lets the tool add spots
                    for notes the template doesn't have yet.
                  </div>
                )}
              </div>
            )}
          </div>
        )}

        {patchErr && (
          <div style={ui.alertError}>Fill failed: {patchErr}</div>
        )}
        {columnPrompt && (
          <div style={{ ...ui.alertWarning, marginTop: pwc.space.sm }}>{columnPrompt}</div>
        )}

        {columnMap && (
          <div
            style={{
              border: `1px solid ${pwc.grey300}`,
              borderRadius: pwc.radius.md,
              padding: pwc.space.md,
              marginTop: pwc.space.md,
              fontSize: 12,
            }}
            aria-label="Column layout editor"
          >
            <div style={{ fontWeight: pwc.weight.medium, marginBottom: 2 }}>
              {columnConfidence === "high"
                ? "Columns detected — check they look right"
                : "Confirm the columns"}
            </div>
            <div style={{ color: pwc.grey700, marginBottom: pwc.space.sm }}>
              {columnConfidence === "high"
                ? "We matched which column holds the row labels and which hold the figures. Adjust any that look wrong, then Fill & download."
                : "Tell us which column holds the row labels, and which hold the figures (letters like D, E, F)."}
            </div>
            {Object.entries(columnMap).map(([sheet, cfg]) => (
              <div key={sheet} style={{ marginBottom: pwc.space.sm }}>
                <div style={{ color: pwc.grey700, marginBottom: 2 }}>{sheet}</div>
                <label style={{ marginRight: pwc.space.md }}>
                  Labels{" "}
                  <input
                    aria-label={`${sheet} label column`}
                    value={cfg.label_column}
                    onChange={(e) =>
                      setColumnMap((m) =>
                        m
                          ? { ...m, [sheet]: { ...m[sheet], label_column: e.target.value.toUpperCase() } }
                          : m
                      )
                    }
                    style={{ width: 44, textTransform: "uppercase" }}
                  />
                </label>
                {Object.keys(cfg.columns).map((role) => (
                  <label key={role} style={{ marginRight: pwc.space.md }}>
                    {role.replace(/_/g, " ")}{" "}
                    <input
                      aria-label={`${sheet} ${role} column`}
                      value={cfg.columns[role]}
                      onChange={(e) =>
                        setColumnMap((m) =>
                          m
                            ? {
                                ...m,
                                [sheet]: {
                                  ...m[sheet],
                                  columns: { ...m[sheet].columns, [role]: e.target.value.toUpperCase() },
                                },
                              }
                            : m
                        )
                      }
                      style={{ width: 44, textTransform: "uppercase" }}
                    />
                  </label>
                ))}
              </div>
            ))}
          </div>
        )}

        {report && (
          <div
            style={{
              ...(report.status === "ok" ? ui.alertSuccess : ui.alertWarning),
              marginTop: pwc.space.md,
            }}
          >
            <div style={{ fontWeight: pwc.weight.medium }}>
              {report.status === "ok"
                ? `Clean — ${report.counts.written} values written. Safe to Validate in mTool.`
                : `Degraded — review before Validate.`}
            </div>
            {(report.numeric_status ?? report.status) !== "ok" && (
              <ul style={{ margin: "6px 0 0", paddingLeft: 18, fontSize: 12 }}>
                {report.counts.unresolved > 0 && (
                  <li>{report.counts.unresolved} label(s) couldn&apos;t be placed (not written)</li>
                )}
                {report.counts.skipped_formula > 0 && (
                  <li>{report.counts.skipped_formula} formula cell(s) skipped</li>
                )}
                {report.counts.mismatches > 0 && (
                  <li>{report.counts.mismatches} write(s) failed read-back</li>
                )}
                {report.counts.errors > 0 && <li>{report.counts.errors} error(s)</li>}
              </ul>
            )}
            {report.notes && (
              <div style={{ marginTop: 6, fontSize: 12 }}>
                <strong>Notes: </strong>
                {[
                  `${report.notes.counts.written} filled`,
                  report.notes.counts.created > 0 && `${report.notes.counts.created} note spot(s) created`,
                  report.notes.counts.unresolved > 0 && `${report.notes.counts.unresolved} not placed`,
                  report.notes.counts.mismatches > 0 && `${report.notes.counts.mismatches} failed read-back`,
                  report.notes.counts.errors > 0 && `${report.notes.counts.errors} error(s)`,
                ]
                  .filter(Boolean)
                  .join(", ")}
                {report.notes.styling_disabled && (
                  <div style={{ color: pwc.grey700 }}>
                    Written without styling — you chose the diagnostic “No styling” option,
                    so plain-looking notes are expected.
                  </div>
                )}
                {!report.notes.styling_disabled &&
                  ((report.notes.counts.formatting_compacted ?? 0) > 0 ||
                    (report.notes.counts.formatting_reduced ?? 0) > 0 ||
                    (report.notes.counts.formatting_dropped ?? 0) > 0) && (
                    <div style={{ color: pwc.grey700 }}>
                      {[
                        (report.notes.counts.formatting_compacted ?? 0) > 0 &&
                          `${report.notes.counts.formatting_compacted} large note(s) used slimmer styling (looks the same)`,
                        (report.notes.counts.formatting_reduced ?? 0) > 0 &&
                          `${report.notes.counts.formatting_reduced} note(s) lost minor styling to fit`,
                        (report.notes.counts.formatting_dropped ?? 0) > 0 &&
                          `${report.notes.counts.formatting_dropped} note(s) written without styling (too large — consider splitting the note)`,
                      ]
                        .filter(Boolean)
                        .join(" · ")}
                    </div>
                  )}
              </div>
            )}
          </div>
        )}

        <div style={styles.actions}>
          <button type="button" onClick={onClose} className={uiClass.btnGhost} style={ui.buttonGhost}>
            Close
          </button>
          <button
            type="button"
            onClick={submit}
            disabled={!file || busy}
            className={uiClass.btnPrimary}
            style={ui.buttonPrimary}
          >
            {busy ? "Filling…" : "Fill & download"}
          </button>
        </div>
      </div>
    </div>
  );
}
