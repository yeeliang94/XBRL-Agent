import React, { useEffect, useRef, useState } from "react";
import { ApiError, userMessage } from "../lib/errors";
import { pwc } from "../lib/theme";
import { ui, uiClass } from "../lib/uiStyles";
import { friendlyMtoolSheetName, MtoolSheetSelection } from "./MtoolSheetSelection";
import { FileDropzone } from "./FileDropzone";
import type { FilingCoverage } from "./FilingCoverageFailurePanel";

/** Template-first preparation: upload, automatic checks, fill, then download.
 * Run warnings are advisory. Exact destinations and the original workbook
 * package are preserved by the shared backend patcher.
 */
async function responseError(response: Response, body?: unknown): Promise<ApiError> {
  const error = ApiError.fromResponse(response.status, body ?? await response.json().catch(() => ({})));
  const reference = response.headers.get("X-Request-ID");
  return new ApiError(reference ? `${error.message} Support reference: ${reference}.` : error.message,
    { status: error.status, technical: error.technical });
}

function fillErrorMessage(error: unknown): string {
  if (error instanceof TypeError && /fetch|network|load failed/i.test(error.message)) {
    return "The connection was interrupted. Check your connection and try again.";
  }
  return userMessage(error);
}

interface FillMeta {
  counts: {
    writes: number;
    conflict_writes: number;
    excluded_matrix_socie: number;
    excluded_not_disclosed: number;
    excluded_out_of_scope: number;
    excluded_no_value: number;
    semantic_mapped?: number;
    semantic_missing?: number;
  };
  sheets_covered: string[];
  filing_standard: string;
  filing_level: string;
  denomination: string | null;
}

interface NotesError {
  label?: string | null;
  key?: string;
  error?: string;
  detail?: string;
}

function notesErrorMessage(error: NotesError): string {
  const identity = [error.label, error.key].filter(Boolean).join(" · ");
  const cause = error.error ?? error.detail ?? "Notes could not be filled. Try again or complete them in mTool.";
  return identity ? `${identity}: ${cause}` : cause;
}

interface NotesReport {
  status: string;
  errors?: NotesError[];
  mismatches?: { label?: string; key?: string; found?: boolean; detail?: string }[];
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
    // Verbatim-passthrough notes whose Word-source styling was stripped for
    // size — they filed with standard styling instead (can land on any tier,
    // so the counters above don't show it).
    source_styling_dropped?: number;
    white_grid_dropped?: number;
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
  reason?: string; // ambiguous | identity_mismatch | strict_near_miss | no_match | no_slot | no_payload_row
  candidates?: NoteCandidate[];
  matched_label?: string;
  ratio?: number;
  key?: string;
  sheet?: string;
  cell?: string;
  source_sheet?: string;
  source_row?: number;
}

// Dry-run notes diagnostic (POST /mtool-fill/notes-preview).
interface NotesPreview {
  notes_in_run: number;
  template_fn_slots: number;
  create_missing_notes: boolean;
  will_fill_existing: { index?: number; label: string | null; key: string }[];
  will_create: { index?: number; label: string | null; cell: string | null; label_cell: string | null }[];
  unresolved: UnresolvedNote[];
  errors: NotesError[];
}

// The operator's placement decision for one flagged note, sent to the server
// as notes_targets — pin to an existing slot (key) or an explicit cell.
type NoteTarget = { key?: string; sheet?: string; cell?: string };

interface ReportSummary {
  sheet_selection?: { selected_sheets: string[]; excluded_sheets: string[]; excluded_figures: number; excluded_notes: number | null };
  status: string;
  numeric_status?: string;
  request_id?: string;
  preflight?: Preflight;
  counts: Record<string, number>;
  unresolved: { sheet: string; label: string | null; detail?: string }[];
  skipped_formula: { sheet: string; cell?: string; label: string | null }[];
  mismatches: { cell: string; expected: string; found: string | null }[];
  ambiguous?: { sheet: string; label: string | null; detail?: string }[];
  fuzzy_matched?: { sheet: string; label: string | null; matched_label?: string }[];
  errors?: { detail?: string; error?: string }[];
  notes?: NotesReport;
  // The template's own declared unit vs the run's denomination. A mismatch is
  // the 1000×-inflation risk — reported, never silently acted on.
  unit_scale_warnings?: {
    sheet: string;
    column: string;
    template_declares: string;
    run_denomination: string;
  }[];
  // Rows whose unit (money / shares / …) isn't in the SSM taxonomy index.
  unit_class_unknown?: { sheet: string; label: string }[];
  period_compatibility?: PeriodCompatibilityIssue[];
  // Step 11A: the report arrives first, the file is fetched separately.
  artifact_id?: string;
  download_url?: string;
  filename?: string;
  receipt_id?: number | null;
  template_known?: boolean;
  filing_coverage?: FilingCoverage;
}

interface PeriodCompatibilityIssue {
  code: string;
  sheet: string;
  period: string;
  source_period?: string | null;
  template_periods?: string[];
}

/** One reason this run isn't ready to become a filing (mtool/preflight.py). */
interface PreflightItem {
  code: string;
  count: number;
  message: string;
  examples: string[];
}

interface Preflight {
  ok: boolean;
  blockers: PreflightItem[];
  warnings: PreflightItem[];
  field_semantics?: FieldSemantics;
}

interface FieldSemantics {
  readiness: "ready" | "needs_review";
  counts: {
    catalog_templates: number;
    selected_templates: number;
    template_slots: number;
    writable_fields: number;
    unresolved_fields: number;
    quarantined_values: number;
  };
  manifest_versions: string[];
  taxonomy_versions?: string[];
  reviewed_exceptions: { exception_code: string; count: number }[];
}

// Server's low-confidence auto-detection payload (422 detail.detected).
interface DetectedSheet {
  label_column: string | null;
  columns: Record<string, string>;
  confidence: string;
  // The real gate: true when a human must confirm before anything is written
  // (group layout or an unrecognised period/entity layout — finding 3).
  requires_confirmation?: boolean;
  basis?: string;
  dimensional?: boolean;
  notes: string[];
}
// The editable column map the user confirms/edits, sent back as column_map.
type ColumnMap = Record<string, { label_column: string; columns: Record<string, string> }>;

interface Props {
  runId: number;
  open: boolean;
  onClose: () => void;
}

type DetectedTemplateSettings = {
  filing_standard: string | null;
  filing_level: string | null;
  sheets: string[];
  declared_unit_scales: string[];
  filing_family_match: boolean;
};

const DETECTED_STATEMENTS: Record<string, string> = {
  "SOFP-CuNonCu": "Financial position · current / non-current",
  "SOFP-Sub-CuNonCu": "Financial position details · current / non-current",
  "SOFP-OrderOfLiquidity": "Financial position · liquidity order",
  "SOPL-Function": "Profit or loss · by function",
  "SOPL-Analysis-Function": "Profit or loss analysis · by function",
  "SOPL-Nature": "Profit or loss · by nature",
  "SOCI-NetOfTax": "Comprehensive income · net of tax",
  "SOCI-BeforeTax": "Comprehensive income · before tax",
  "SOCF-Indirect": "Cash flows · indirect",
  "SOCF-Direct": "Cash flows · direct",
  "SOCIE": "Changes in equity",
};

function detectedStatementLabels(sheets: string[]): string[] {
  return Array.from(new Set(sheets.map((sheet) => DETECTED_STATEMENTS[sheet]).filter((label): label is string => !!label)));
}

function detectedScaleLabel(scales: string[]): string {
  if (scales.length === 0) return "Not stated in template";
  if (scales.length > 1) return "Different scales across sheets";
  return ({ units: "RM", thousands: "RM '000", millions: "RM mil" } as Record<string, string>)[scales[0]] ?? scales[0];
}

const styles = {
  overlay: {
    ...ui.scrim,
  } as React.CSSProperties,
  modal: {
    ...ui.dialog,
    width: "min(760px, 94vw)",
    maxWidth: "min(760px, 94vw)",
    maxHeight: "90vh",
    overflow: "hidden",
    display: "flex",
    flexDirection: "column",
    outline: "none",
  } as React.CSSProperties,
  headerRow: {
    display: "flex",
    alignItems: "flex-start",
    justifyContent: "space-between",
    gap: pwc.space.md,
    marginBottom: pwc.space.sm,
  } as React.CSSProperties,
  heading: {
    ...ui.dialogTitle,
    fontSize: 20,
  } as React.CSSProperties,
  closeX: {
    border: "none",
    background: "transparent",
    color: pwc.grey500,
    fontSize: 18,
    lineHeight: 1,
    cursor: "pointer",
    padding: pwc.space.xs,
    minWidth: 40,
    minHeight: 40,
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
  } as React.CSSProperties,
  sub: {
    ...ui.bodyText,
    margin: `0 0 ${pwc.space.lg}px`,
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
    flexWrap: "wrap",
    marginTop: pwc.space.lg,
  } as React.CSSProperties,
  noteCard: {
    borderTop: `1px solid ${pwc.grey200}`,
    padding: `${pwc.space.sm}px 0`,
    fontSize: 12,
  } as React.CSSProperties,
  selectedFile: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    gap: pwc.space.md,
    padding: `${pwc.space.sm}px 0 ${pwc.space.md}px`,
    borderBottom: `1px solid ${pwc.grey200}`,
  } as React.CSSProperties,
  summaryGrid: {
    display: "grid",
    gridTemplateColumns: "repeat(auto-fit, minmax(84px, 1fr))",
    gap: pwc.space.md,
    padding: `${pwc.space.md}px 0`,
  } as React.CSSProperties,
  summaryValue: {
    fontSize: 20,
    fontWeight: pwc.weight.semibold,
    color: pwc.grey900,
    lineHeight: 1.2,
  } as React.CSSProperties,
};

/** One collapsible group of note-placement details. */
function PlanSection({
  title,
  count,
  defaultOpen,
  hint,
  children,
}: {
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

/** Coerce a preflight response into a shape the UI can trust.
 *
 * A malformed or unreachable answer must read as "we don't know", not as
 * "ready". The server repeats the assessment when it prepares the workbook. */
function normalisePreflight(body: unknown): Preflight | null {
  if (!body || typeof body !== "object") return null;
  const raw = body as Partial<Preflight>;
  if (!Array.isArray(raw.blockers) || !Array.isArray(raw.warnings)) return null;
  return {
    ok: raw.ok !== false,
    blockers: raw.blockers,
    warnings: raw.warnings,
    field_semantics: normaliseFieldSemantics(
      (body as { field_semantics?: unknown }).field_semantics,
    ),
  };
}

function normaliseFieldSemantics(body: unknown): FieldSemantics | undefined {
  if (!body || typeof body !== "object") return undefined;
  const raw = body as Partial<FieldSemantics>;
  if (raw.readiness !== "ready" && raw.readiness !== "needs_review") {
    return undefined;
  }
  if (!raw.counts || typeof raw.counts !== "object") return undefined;
  const countKeys: (keyof FieldSemantics["counts"])[] = [
    "catalog_templates",
    "selected_templates",
    "template_slots",
    "writable_fields",
    "unresolved_fields",
    "quarantined_values",
  ];
  if (countKeys.some((key) => typeof raw.counts?.[key] !== "number")) {
    return undefined;
  }
  if (!Array.isArray(raw.manifest_versions) || !Array.isArray(raw.reviewed_exceptions)) {
    return undefined;
  }
  if (raw.reviewed_exceptions.some((item) => (
    !item
    || typeof item.exception_code !== "string"
    || typeof item.count !== "number"
  ))) {
    return undefined;
  }
  return raw as FieldSemantics;
}

/** One category of problem rows from the fill report, listed in FULL.
 *
 * Nothing is capped here. The report used to ride an HTTP header limited to 20
 * rows and 6 KB, and the UI rendered only counts — so "which rows didn't
 * land?" was a question the operator could not answer from the screen. */
function RowDetail({ title, rows, defaultOpen = false }: { title: string; rows: string[]; defaultOpen?: boolean }) {
  if (rows.length === 0) return null;
  return (
    <details style={{ marginTop: 6, fontSize: 12 }} open={defaultOpen}>
      <summary style={{ cursor: "pointer" }}>
        {title} <span style={{ color: pwc.grey500 }}>({rows.length})</span>
      </summary>
      <ul style={{ margin: "4px 0 0", paddingLeft: 18, color: pwc.grey700 }}>
        {rows.map((r, i) => (
          <li key={i}>{r}</li>
        ))}
      </ul>
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

/** Whether destination-sheet scoping contributed to this placement result. */
function noteReasonUsesSheetScope(reason?: string): boolean {
  return reason === "ambiguous" || reason === "identity_mismatch" || reason === "strict_near_miss" ||
    reason === "no_match" || reason === "no_slot";
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
  const where = c.key
    ? c.sheet && c.cell
      ? `${c.sheet} ${c.cell} (existing note spot ${c.key})`
      : `existing note spot ${c.key}`
    : `${c.sheet} ${c.cell}`;
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
    // Category-based sheets (SOCIE equity components, issued-capital share
    // classes, etc.) are resolved from taxonomy dimensions. Their physical
    // columns are not period roles and must never be shown as CY/PY inputs.
    if (d.dimensional) continue;
    seed[sheet] = { label_column: d.label_column ?? "", columns: { ...d.columns } };
  }
  return seed;
}

export function MtoolFillModal({ runId, open, onClose }: Props) {
  const replacementInputRef = useRef<HTMLInputElement>(null);
  const [meta, setMeta] = useState<FillMeta | null>(null);
  const [notesCount, setNotesCount] = useState<number | null>(null);
  const [fillNotes, setFillNotes] = useState(true);
  // Default ON: a template freshly exported from mTool has no note spots
  // provisioned, so leaving this off silently placed zero notes (run 75) and
  // read as a broken fill rather than a missing opt-in. The safe posture is
  // still served by the automatic preview, which shows what would be created
  // before anything is written.
  const [createMissingNotes, setCreateMissingNotes] = useState(true);
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
  const [notesSheets, setNotesSheets] = useState<string[]>([]);
  const [selectedSheets, setSelectedSheets] = useState<string[] | null>(null);
  const availableSheets = Array.from(new Set([...(meta?.sheets_covered ?? []), ...notesSheets])).sort();
  const [busy, setBusy] = useState(false);
  const [report, setReport] = useState<ReportSummary | null>(null);
  const [patchErr, setPatchErr] = useState<string | null>(null);
  // The column layout — detected UP FRONT the moment a template is chosen
  // (POST /mtool-fill/detect-columns) so the operator confirms columns
  // alongside the notes check, not after a failed Fill. Editable; sent as
  // column_map on Fill. The submit path still handles a low-confidence 422 as
  // a defensive fallback for the rare case detection wasn't run.
  const [columnMap, setColumnMap] = useState<ColumnMap | null>(null);
  const [, setDimensionalSheets] = useState<string[]>([]);
  const [columnConfidence, setColumnConfidence] = useState<string | null>(null);
  const [, setPeriodCompatibility] = useState<PeriodCompatibilityIssue[]>([]);
  const [detectBusy, setDetectBusy] = useState(false);
  const [detectErr, setDetectErr] = useState<string | null>(null);
  const [templateSettings, setTemplateSettings] = useState<DetectedTemplateSettings | null>(null);
  const [unitScaleWarnings, setUnitScaleWarnings] = useState<Array<{ template_declares: string; run_denomination: string }>>([]);
  // Readiness reminders never require an override to prepare a workbook.
  const [preflight, setPreflight] = useState<Preflight | null>(null);
  const [readinessErr, setReadinessErr] = useState<string | null>(null);
  // Step 11A: the workbook waits behind its own request until the operator has
  // seen the report (and, when degraded, said so).
  const [downloadErr, setDownloadErr] = useState<string | null>(null);
  const [downloaded, setDownloaded] = useState(false);
  const [fillQueued, setFillQueued] = useState(false);
  // Monotonic token so a slow column-detect for template A can't land its
  // result after the user has switched to template B (a stale columnMap would
  // be sent as an explicit override and MIS-TARGET writes). Bumped on every
  // file change; runDetect ignores its own response once superseded.
  const detectSeq = useRef(0);
  const previewSeq = useRef(0);
  const sessionSeq = useRef(0);
  const preflightSeq = useRef(0);
  const fillButtonRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useRef<HTMLDivElement>(null);
  const [downloading, setDownloading] = useState(false);

  useEffect(() => {
    if (!open) return;
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    dialogRef.current?.focus();
    return () => { previousFocus?.focus(); };
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      if (e.key !== "Tab") return;
      const focusable = Array.from(dialogRef.current?.querySelectorAll<HTMLElement>(
        "button, input, select, summary, a[href], [tabindex='0']",
      ) ?? []).filter((el) => !el.matches(":disabled") && el.getClientRects().length > 0);
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (!first) { e.preventDefault(); dialogRef.current?.focus(); return; }
      const focusOutsideControls = !focusable.some((el) => el === document.activeElement);
      if (e.shiftKey && (document.activeElement === first || focusOutsideControls)) {
        e.preventDefault(); last.focus();
      } else if (!e.shiftKey && (document.activeElement === last || focusOutsideControls)) {
        e.preventDefault(); first.focus();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [open, onClose]);

  useEffect(() => {
    if (!open) return;
    const session = ++sessionSeq.current;
    const current = () => session === sessionSeq.current;
    setBusy(false);
    setDownloading(false);
    setDetectBusy(false);
    setPreviewBusy(false);
    setReadinessErr(null);
    setMeta(null);
    setNotesCount(null);
    setNotesSheets([]);
    setSelectedSheets(null);
    // This modal stays MOUNTED between sessions, so any choice not reset here
    // silently persists into the next fill. Both of these advertise a default
    // in their own label ("On by default"), which would be a lie on the second
    // open after a single untick — and a stale create-missing is exactly what
    // made run 75 place zero notes.
    setFillNotes(true);
    setCreateMissingNotes(true);
    setLoadErr(null);
    setFile(null);
    setReport(null);
    setPatchErr(null);
    setColumnMap(null);
    setDimensionalSheets([]);
    setColumnConfidence(null);
    setPeriodCompatibility([]);
    setDetectErr(null);
    setTemplateSettings(null);
    setUnitScaleWarnings([]);
    setColumnPrompt(null);
    setPreview(null);
    setPreviewErr(null);
    setNoteTargets({});
    setPreflight(null);
    setDownloadErr(null);
    setDownloaded(false);
    setFillQueued(false);
    const preflightRequest = ++preflightSeq.current;
    fetch(`/api/runs/${runId}/mtool-fill/preflight`)
      .then(async (r) => {
        if (!r.ok) throw await responseError(r);
        return r.json();
      })
      .then((body) => {
        if (!current() || preflightRequest !== preflightSeq.current) return;
        const result = normalisePreflight(body);
        setPreflight(result);
        setReadinessErr(result ? null : "The check returned an unreadable result.");
      })
      .catch((e) => { if (current() && preflightRequest === preflightSeq.current) setReadinessErr(fillErrorMessage(e)); });
    fetch(`/api/runs/${runId}/mtool-fill`)
      .then(async (r) => {
        if (!r.ok) throw await responseError(r);
        return r.json();
      })
      .then((doc) => { if (current()) setMeta(doc.meta); })
      .catch((e) => { if (current()) setLoadErr(fillErrorMessage(e)); });
    fetch(`/api/runs/${runId}/mtool-notes-fill`)
      .then(async (r) => {
        if (!r.ok) throw await responseError(r);
        return r.json();
      })
      .then((doc) => { if (current()) {
        setNotesCount(doc?.meta?.counts?.notes ?? null);
        setNotesSheets(Array.from(new Set<string>((doc?.footnotes ?? [])
          .map((note: { source_sheet?: string }) => note.source_sheet)
          .filter((sheet: unknown): sheet is string => typeof sheet === "string"))));
      } })
      .catch((e) => { if (current()) setPreviewErr(`Could not load the notes summary. ${fillErrorMessage(e)} Notes will still be attempted during filling.`); });
    return () => {
      sessionSeq.current += 1;
      detectSeq.current += 1;
      previewSeq.current += 1;
    };
  }, [open, runId]);

  useEffect(() => {
    setReport(null);
    setDownloaded(false);
    setDownloadErr(null);
  }, [file, fillNotes, createMissingNotes, noteTargets, columnMap, selectedSheets]);

  useEffect(() => {
    if (file) fillButtonRef.current?.focus();
  }, [file]);

  const notesTargetsPayload = () =>
    Object.keys(noteTargets).length > 0 ? JSON.stringify(noteTargets) : null;

  const submit = async () => {
    if (!file || busy || selectedSheets?.length === 0) return;
    const session = sessionSeq.current;
    const current = () => session === sessionSeq.current;
    detectSeq.current += 1;
    previewSeq.current += 1;
    setDetectBusy(false);
    setPreviewBusy(false);
    preflightSeq.current += 1;
    setBusy(true);
    setPatchErr(null);
    setColumnPrompt(null);
    setReport(null);
    setDownloaded(false);
    setDownloadErr(null);
    try {
      const form = new FormData();
      form.append("template", file);
      if (selectedSheets !== null) form.append("selected_sheets", JSON.stringify(selectedSheets));
      form.append("strict", "true");
      form.append("fill_notes", fillNotes ? "true" : "false");
      form.append("create_missing_notes", createMissingNotes ? "true" : "false");
      if (fillNotes) form.append("notes_styling", "styled");
      if (columnMap) form.append("column_map", JSON.stringify(columnMap));
      const targets = fillNotes ? notesTargetsPayload() : null;
      if (targets) form.append("notes_targets", targets);
      const resp = await fetch(`/api/runs/${runId}/mtool-fill/patch`, {
        method: "POST",
        body: form,
      });
      if (!current()) return;
      if (!resp.ok) {
        const body = await resp.json().catch(() => ({}));
        if (!current()) return;
        const detail = body?.detail;
        // Low-confidence / unconfirmed auto-detection: the server hands back
        // its best guess in detail.detected. Seed the editor so the user can
        // confirm + retry. This is a guided next step, not a failure.
        if (detail && typeof detail === "object" && detail.detected) {
          const detected = detail.detected as Record<string, DetectedSheet>;
          const editable = detectedToColumnMap(detected);
          setDimensionalSheets(
            Object.entries(detected)
              .filter(([, sheet]) => sheet.dimensional)
              .map(([sheet]) => sheet),
          );
          setColumnMap(Object.keys(editable).length > 0 ? editable : null);
          setColumnConfidence("low");
          if (Object.keys(editable).length > 0) {
            setColumnPrompt(
              "Check the period columns below, then click Fill again."
            );
          }
          return;
        }
        // The run isn't ready to file. Show the reasons rather than an error.
        if (detail && typeof detail === "object" && detail.preflight) {
          setPreflight(normalisePreflight(detail.preflight));
          return;
        }
        if (detail && typeof detail === "object" && detail.filing_coverage) {
          throw new Error("We couldn't place any saved figures in this template. Check that it is the right mTool template and try again.");
        }
        throw await responseError(resp, body);
      }
      // The response is the REPORT, not the file. The workbook waits behind
      // its own request until the operator has seen this (Step 11A).
      const result = (await resp.json()) as ReportSummary;
      if (!current()) return;
      setReport(result);
      if (result.preflight) { setPreflight(normalisePreflight(result.preflight)); setReadinessErr(null); }
    } catch (e) {
      if (current()) {
        setPatchErr(fillErrorMessage(e));
      }
    } finally {
      if (current()) setBusy(false);
    }
  };

  const requestSubmit = () => {
    if (detectBusy) {
      setFillQueued(true);
      return;
    }
    void submit();
  };

  useEffect(() => {
    if (!fillQueued || detectBusy) return;
    setFillQueued(false);
    if (columnConfidence === "low" && columnMap) return;
    void submit();
    // `submit` intentionally uses the state from the render in which template
    // detection completed. A low-confidence layout remains a human decision.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fillQueued, detectBusy, columnConfidence, columnMap]);

  // Step two of the split: fetch the workbook the fill produced. A degraded
  // fill only releases it once the operator has acknowledged the report.
  const download = async () => {
    if (!report?.download_url || downloading) return;
    const session = sessionSeq.current;
    const current = () => session === sessionSeq.current;
    setDownloading(true);
    // Download for review is the explicit acknowledgment of this report.
    // The server records it before releasing a workbook with review items.
    setDownloadErr(null);
    try {
      const url =
        report.status === "ok"
          ? report.download_url
          : `${report.download_url}?acknowledge_degraded=${encodeURIComponent(
              "operator selected Download for review after seeing the report",
            )}`;
      const resp = await fetch(url);
      if (!resp.ok) {
        const body = await resp.json().catch(() => ({}));
        throw await responseError(resp, body);
      }
      const blob = await resp.blob();
      if (!current()) return;
      const objectUrl = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = objectUrl;
      a.download = report.filename ?? `mtool_filled_run${runId}.xlsx`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(objectUrl);
      setDownloaded(true);
    } catch (e) {
      if (current()) setDownloadErr(fillErrorMessage(e));
    } finally {
      if (current()) setDownloading(false);
    }
  };

  // Dry-run diagnostic: what would fill / get created / stay unresolved, and
  // how many fn_* slots the uploaded template exposes. Writes nothing. Sends
  // the operator's placement decisions so a re-check reflects them.
  const runPreview = async (selectedFile?: File, resetTargets = false, sheets = selectedSheets, createMissing = createMissingNotes) => {
    const targetFile = selectedFile ?? file;
    if (!targetFile) return;
    const seq = ++previewSeq.current;
    const stale = () => seq !== previewSeq.current;
    setPreviewBusy(true);
    setPreviewErr(null);
    setPreview(null);
    try {
      const form = new FormData();
      form.append("template", targetFile);
      if (sheets !== null) form.append("selected_sheets", JSON.stringify(sheets));
      form.append("create_missing_notes", createMissing ? "true" : "false");
      form.append("notes_styling", "styled");
      const targets = resetTargets ? null : notesTargetsPayload();
      if (targets) form.append("notes_targets", targets);
      const resp = await fetch(`/api/runs/${runId}/mtool-fill/notes-preview`, {
        method: "POST",
        body: form,
      });
      const body = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        throw await responseError(resp, body);
      }
      if (stale()) return;
      const candidate = body as Partial<NotesPreview>;
      if (
        Array.isArray(candidate.will_fill_existing) &&
        Array.isArray(candidate.will_create) &&
        Array.isArray(candidate.unresolved) &&
        Array.isArray(candidate.errors)
      ) {
        setPreview(candidate as NotesPreview);
      } else {
        throw new Error("Notes preview returned an invalid response.");
      }
    } catch (e) {
      if (!stale()) setPreviewErr(fillErrorMessage(e));
    } finally {
      if (!stale()) setPreviewBusy(false);
    }
  };

  // Up-front column pre-flight: detect the template's layout the moment a file
  // is chosen so the operator confirms columns alongside the notes check,
  // instead of hitting a post-submit 422. Writes nothing.
  const runDetect = async (f: File, sheets = selectedSheets) => {
    const seq = ++detectSeq.current;
    const stale = () => seq !== detectSeq.current;
    setDetectBusy(true);
    setDetectErr(null);
    setTemplateSettings(null);
    setUnitScaleWarnings([]);
    setColumnMap(null);
    setColumnConfidence(null);
    setColumnPrompt(null);
    try {
      const form = new FormData();
      form.append("template", f);
      if (sheets !== null) form.append("selected_sheets", JSON.stringify(sheets));
      const resp = await fetch(`/api/runs/${runId}/mtool-fill/detect-columns`, {
        method: "POST",
        body: form,
      });
      const body = await resp.json().catch(() => ({}));
      if (stale()) return; // a newer file was chosen — drop this response
      if (!resp.ok) {
        throw await responseError(resp, body);
      }
      const detected = (body as { detected?: Record<string, DetectedSheet> }).detected;
      setTemplateSettings((body as { detected_settings?: DetectedTemplateSettings }).detected_settings ?? null);
      setUnitScaleWarnings((body as { unit_scale_warnings?: Array<{ template_declares: string; run_denomination: string }> }).unit_scale_warnings ?? []);
      setPeriodCompatibility(
        Array.isArray((body as { period_compatibility?: unknown }).period_compatibility)
          ? (body as { period_compatibility: PeriodCompatibilityIssue[] }).period_compatibility
          : [],
      );
      setDimensionalSheets(
        detected
          ? Object.entries(detected)
              .filter(([, sheet]) => sheet.dimensional)
              .map(([sheet]) => sheet)
          : [],
      );
      const semanticSource = (
        body as { filing_inspection?: { semantic_source?: string } }
      ).filing_inspection?.semantic_source;
      // Verified period layouts can proceed without a redundant editor.
      // Candidate mTool workbooks may contain some taxonomy identifiers while
      // other values still use legacy columns, so those remain confirmable.
      // `requires_confirmation` outranks `confidence`: a group layout or an
      // unrecognised template can look confident while nothing has actually
      // corroborated which column is which (finding 3).
      const mustConfirm = Boolean(
        (body as { requires_confirmation?: boolean }).requires_confirmation,
      );
      const needsColumnConfirmation =
        semanticSource !== "generated-targets" && mustConfirm;
      if (detected && needsColumnConfirmation) {
        const editable = detectedToColumnMap(detected);
        setColumnMap(Object.keys(editable).length > 0 ? editable : null);
      }
      setColumnConfidence(
        mustConfirm ? "low" : ((body as { confidence?: string }).confidence ?? null),
      );
      if (mustConfirm && needsColumnConfirmation) {
        setColumnPrompt(
          "Check the period columns below before filling. " +
            (detected
              ? Object.values(detected)
                  .flatMap((d) => d.notes ?? [])
                  .find((n) => n.includes("group") || n.includes("categories") || n.includes("haven't seen")) ??
                ""
              : ""),
        );
      }
    } catch (e) {
      if (stale()) return;
      setDetectErr(fillErrorMessage(e));
    } finally {
      if (!stale()) setDetectBusy(false);
    }
  };

  const reviewGroups = [
    { title: "Filing blockers", items: preflight?.blockers ?? [], urgent: true },
    { title: "Source completeness needs review", items: (preflight?.warnings ?? []).filter((item) => item.code === "notes_integrity_shadow_needs_review"), urgent: true },
  ].filter((group) => group.items.length > 0);
  const coverageSkipped = report?.filing_coverage
    ? report.filing_coverage.unmapped + report.filing_coverage.ambiguous
    : 0;
  const notFilledCount = report
    ? coverageSkipped + report.unresolved.length + (report.notes?.counts.unresolved ?? 0)
    : 0;
  const changeSheets = (sheets: string[] | null) => {
    setSelectedSheets(sheets);
    previewSeq.current += 1;
    detectSeq.current += 1;
    setPreviewBusy(false);
    setDetectBusy(false);
    setPreview(null);
    setPreviewErr(null);
    setNoteTargets({});
    setPatchErr(null);
    setReport(null);
    setColumnMap(null);
    setColumnConfidence(null);
    setPeriodCompatibility([]);
    setColumnPrompt(null);
    setDimensionalSheets([]);
    setDetectErr(null);
    setTemplateSettings(null);
    setUnitScaleWarnings([]);
    if (file && sheets?.length !== 0) {
      void runDetect(file, sheets);
      if (fillNotes && notesCount !== 0) void runPreview(file, true, sheets);
    }
  };

  const selectTemplate = (nextFile: File) => {
    if (!nextFile.name.toLowerCase().endsWith(".xlsx") || nextFile.size === 0 || nextFile.size > 25 * 1024 * 1024) {
      setPatchErr("Choose a non-empty .xlsx template exported from mTool, up to 25 MB.");
      return;
    }
    previewSeq.current += 1;
    detectSeq.current += 1;
    setPreviewBusy(false);
    setDetectBusy(false);
    setFile(nextFile);
    setReport(null);
    setPatchErr(null);
    setDownloadErr(null);
    setDownloaded(false);
    setFillQueued(false);
    setColumnMap(null);
    setDimensionalSheets([]);
    setColumnConfidence(null);
    setPeriodCompatibility([]);
    setDetectErr(null);
    setTemplateSettings(null);
    setUnitScaleWarnings([]);
    setColumnPrompt(null);
    setPreview(null);
    setPreviewErr(null);
    setNoteTargets({});
    if (selectedSheets?.length !== 0) void runDetect(nextFile);
    if (selectedSheets?.length !== 0 && notesCount !== 0 && fillNotes) {
      void runPreview(nextFile, true);
    }
  };

  if (!open) return null;

  return (
    <div
      style={styles.overlay}
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
      role="dialog"
      aria-modal="true"
      aria-label="Prepare mTool draft"
    >
      <div ref={dialogRef} tabIndex={-1} style={styles.modal}>
        <div style={styles.headerRow}>
          <h2 style={styles.heading}>{report ? "Draft prepared" : "Prepare mTool draft"}</h2>
          {/* Corner close — Esc + scrim-click already close, but a visible ✕
              is the discoverable affordance (E6). */}
          <button
            type="button"
            aria-label="Close"
            data-tooltip="Close"
            onClick={onClose}
            style={styles.closeX}
          >
            ✕
          </button>
        </div>
        <div style={{ overflowY: "auto", minHeight: 0, flex: "1 1 auto" }}>
        {!file && <p style={styles.sub}>Upload an empty .xlsx template exported from mTool.</p>}

        <fieldset disabled={busy || downloading || fillQueued} style={{ border: 0, padding: 0, margin: 0, minWidth: 0 }}>
        {!file && (
          <FileDropzone
            accept=".xlsx"
            disabled={busy || downloading}
            label="Drop your mTool template here or choose a file"
            buttonLabel="Choose template"
            inputLabel="mTool template file"
            testId="mtool-template-dropzone"
            onFile={selectTemplate}
          />
        )}

        {file && (
          <div style={styles.selectedFile}>
            <div style={{ minWidth: 0 }}>
              <div style={{ fontWeight: pwc.weight.semibold, overflowWrap: "anywhere" }}>{file.name}</div>
            </div>
            <button type="button" className={uiClass.btnSecondary}
              style={{ ...ui.buttonSecondary, flexShrink: 0 }}
              onClick={() => replacementInputRef.current?.click()}>
              Change file
            </button>
            <input
              ref={replacementInputRef}
              type="file"
              accept=".xlsx"
              aria-label="mTool template file"
              style={{ display: "none" }}
              onChange={(event) => {
                const replacement = event.target.files?.[0];
                event.target.value = "";
                if (!replacement) return;
                selectTemplate(replacement);
              }}
            />
          </div>
        )}

        {file && meta && !report && (
          <p aria-label="Fill summary" style={{ margin: `${pwc.space.md}px 0`, color: pwc.grey700, fontSize: 14 }}>
            Available from this run: <strong style={{ color: pwc.grey900 }}>{meta.counts.writes} figures</strong>
            {notesCount !== null && <> · <strong style={{ color: pwc.grey900 }}>{notesCount} notes</strong></>}
          </p>
        )}

        {file && !report && (
          <section aria-label="Detected template settings" style={{ borderTop: `1px solid ${pwc.grey200}`, paddingTop: pwc.space.md, marginBottom: pwc.space.md }}>
            <div style={{ fontWeight: pwc.weight.semibold, marginBottom: pwc.space.sm }}>Detected template</div>
            {detectBusy && <div role="status" style={styles.statLine}>Checking template settings…</div>}
            {!detectBusy && templateSettings && <>
              <div style={{ ...styles.statLine, display: "grid", gridTemplateColumns: "minmax(105px, 130px) minmax(0, 1fr)", gap: `${pwc.space.xs}px ${pwc.space.md}px` }}>
                <span style={{ color: pwc.grey700 }}>Standard</span><span>{templateSettings.filing_standard?.toUpperCase() ?? "Not identified"}</span>
                <span style={{ color: pwc.grey700 }}>Filing level</span><span>{templateSettings.filing_level ? templateSettings.filing_level[0].toUpperCase() + templateSettings.filing_level.slice(1) : "Not identified"}</span>
                <span style={{ color: pwc.grey700 }}>Statements</span><span>{detectedStatementLabels(templateSettings.sheets).join("; ") || "No statement format identified"}</span>
                <span style={{ color: pwc.grey700 }}>Amount scale</span><span>{detectedScaleLabel(templateSettings.declared_unit_scales)}</span>
              </div>
              {!templateSettings.filing_family_match && <div role="alert" style={{ ...ui.alertError, marginTop: pwc.space.sm }}>
                This template does not match the run&apos;s filing standard, company/group level, or statements. Choose a matching template.
              </div>}
              {unitScaleWarnings.length > 0 && <div role="alert" style={{ ...ui.alertWarning, marginTop: pwc.space.sm }}>
                Amount scale differs: template {detectedScaleLabel([unitScaleWarnings[0].template_declares])}, run {detectedScaleLabel([unitScaleWarnings[0].run_denomination])}. Check the amounts before filing.
              </div>}
            </>}
            {!detectBusy && detectErr && <div role="alert" style={ui.alertError}>Couldn&apos;t check this template: {detectErr}</div>}
          </section>
        )}

        {patchErr && (
          <div role="alert" style={ui.alertError}>Fill failed: {patchErr}</div>
        )}
        {busy && <div role="status" style={ui.alertInfo}>Preparing your template…</div>}
        {readinessErr && <p role="status" style={styles.statLine}>Run checks are unavailable: {readinessErr} You can still fill the template; checks are repeated when you fill.</p>}
        {loadErr && (
          <div style={ui.alertError}>Could not load fill data: {loadErr}</div>
        )}

        <div hidden={!!report} style={{ display: report ? undefined : "flex", flexDirection: "column" }}>
        {file && <details style={{ marginBottom: pwc.space.md }}>
          <summary style={{ cursor: "pointer", fontSize: 14, fontWeight: pwc.weight.medium, color: pwc.grey900 }}>
            Customize
          </summary>
        {availableSheets.length > 0 && (
          <MtoolSheetSelection sheets={availableSheets} selected={selectedSheets} onChange={changeSheets} />
        )}
        {notesCount !== null && notesCount > 0 && (
          <label style={{ ...styles.statLine, display: "flex", alignItems: "center", gap: 6, marginBottom: 4 }}>
            <input
              type="checkbox"
              checked={fillNotes}
              onChange={(e) => {
                setFillNotes(e.target.checked);
                previewSeq.current += 1;
                setPreviewBusy(false);
                setPreview(null); // plan no longer reflects the toggles
                setPreviewErr(null);
                setNoteTargets({});
                if (e.target.checked && file && selectedSheets?.length !== 0) {
                  void runPreview(undefined, true);
                }
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
                previewSeq.current += 1;
                setPreviewBusy(false);
                setPreview(null); // create-toggle changes the plan
                setPreviewErr(null);
                setNoteTargets({});
                if (file && selectedSheets?.length !== 0) {
                  void runPreview(undefined, true, selectedSheets, e.target.checked);
                }
              }}
              aria-label="Add missing note spots"
              style={{ marginTop: 2 }}
            />
            <span>
              Add missing note spots
              <span style={{ display: "block", color: pwc.grey700, fontSize: 12 }}>
                Creates a destination only when the template has an exact matching note label.
              </span>
            </span>
          </label>
        )}

        {reviewGroups.length > 0 && (
          <div aria-label="Run review reminders" style={{ ...ui.supportingText, borderTop: `1px solid ${pwc.grey200}`, paddingTop: pwc.space.sm }}>
            {reviewGroups.reduce((sum, group) => sum + group.items.length, 0)} saved-run {reviewGroups.reduce((sum, group) => sum + group.items.length, 0) === 1 ? "item needs" : "items need"} review.
          </div>
        )}


        </details>}

        {fillNotes && (
          <div style={{ marginBottom: pwc.space.md }}>
            {previewBusy && (
              <div role="status" style={{ color: pwc.grey500, fontSize: 12 }}>Checking where notes can be placed…</div>
            )}
            {previewErr && (
              <div style={{ ...ui.alertError, marginTop: pwc.space.sm }}>
                <div>Couldn&apos;t check note placement. You can still fill the figures.</div>
                <button type="button" onClick={() => void runPreview()} disabled={busy} style={{ ...ui.buttonGhost, marginTop: pwc.space.sm }} className={uiClass.btnGhost}>Try note check again</button>
              </div>
            )}
            {preview && (preview.unresolved.length > 0 || preview.errors.length > 0) && (
              <details
                open={preview.errors.length > 0}
                style={{
                  borderTop: `1px solid ${pwc.grey200}`,
                  paddingTop: pwc.space.md,
                  fontSize: 12,
                }}
                aria-label="Notes preview"
              >
                <summary style={{ color: pwc.grey900, fontWeight: pwc.weight.medium, cursor: "pointer" }}>
                  {preview.unresolved.length > 0
                    ? `${preview.unresolved.length} ${preview.unresolved.length === 1 ? "note has" : "notes have"} no certain destination`
                    : `${preview.errors.length} ${preview.errors.length === 1 ? "note issue" : "note issues"} to review`}
                  {preview.unresolved.length > 0 && preview.errors.length > 0 && ` · ${preview.errors.length} ${preview.errors.length === 1 ? "issue" : "issues"}`}
                </summary>
                <p style={{ color: pwc.grey700, margin: `${pwc.space.xs}px 0 ${pwc.space.sm}px` }}>
                  {preview.unresolved.length > 0
                    ? "Uncertain notes will be left for completion in mTool unless you choose a destination below."
                    : "Review these note issues before filling."}
                </p>

                {preview.errors.length > 0 && (
                  <div style={{ ...ui.alertError, marginTop: pwc.space.sm }}>
                    <div style={{ fontWeight: pwc.weight.medium }}>
                      {preview.errors.length} problem(s) would stop the notes from landing:
                    </div>
                    <ul style={{ margin: "4px 0 0", paddingLeft: 18 }}>
                      {preview.errors.slice(0, 4).map((e, i) => (
                        <li key={i}>{notesErrorMessage(e)}</li>
                      ))}
                      {preview.errors.length > 4 && <li>… and {preview.errors.length - 4} more</li>}
                    </ul>
                  </div>
                )}

                {/* Notes that need a human call — each with a plain-language
                    reason and, where the tool found options, a picker. This is
                    the notes twin of the numeric column-layout confirm step. */}
                <PlanSection
                  title="Notes to finish in mTool"
                  count={preview.unresolved.length}
                  defaultOpen
                  hint="Place these yourself, or leave them for manual completion in mTool."
                >
                  {preview.unresolved.map((u, i) => {
                    const idx = u.index ?? -1;
                    const chosen = idx >= 0 ? noteTargets[idx] : undefined;
                    const suggestion = u.reason === "strict_near_miss" ? suggestionTarget(u) : null;
                    return (
                      <div key={i} style={styles.noteCard}>
                        <div style={{ fontWeight: pwc.weight.medium, color: pwc.grey900 }}>{u.label}</div>
                        {u.source_sheet && noteReasonUsesSheetScope(u.reason) && (
                          <div style={{ color: pwc.grey500, marginTop: 2 }}>
                            Only checked in: {u.source_sheet}
                          </div>
                        )}
                        <div style={{ color: pwc.grey700, margin: "2px 0 4px" }}>{unresolvedReasonText(u)}</div>
                        {(u.reason === "ambiguous" || u.reason === "identity_mismatch") && (u.candidates?.length ?? 0) > 0 && idx >= 0 && (
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
                        onClick={() => void runPreview()}
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
                  title="New note spots"
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
                  title="Existing note spots"
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
              </details>
            )}
          </div>
        )}

        {columnPrompt && (
          <div
            style={{
              borderTop: `1px solid ${pwc.grey200}`,
              paddingTop: pwc.space.md,
              marginTop: pwc.space.sm,
              fontSize: 12,
              color: pwc.grey700,
            }}
          >
            {columnPrompt}
          </div>
        )}

        {columnMap && (
          <div
            style={{
              borderTop: columnPrompt ? "none" : `1px solid ${pwc.grey200}`,
              paddingTop: columnPrompt ? pwc.space.sm : pwc.space.md,
              marginTop: pwc.space.md,
              fontSize: 12,
            }}
            aria-label="Column layout editor"
          >
            <div style={{ fontWeight: pwc.weight.medium, marginBottom: 2 }}>
              {columnConfidence === "high"
                ? "Period columns detected"
                : "Confirm period columns"}
            </div>
            <div style={{ color: pwc.grey700, marginBottom: pwc.space.sm }}>
              {columnConfidence === "high"
                ? "Only change these if the year labels in your template show a different layout."
                : "Enter the row-label column and the figure columns shown by their letters in Excel (for example D, E, F)."}
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

        </div>
        </fieldset>
        {report && (
          <div role="status"
            style={{
              display: "block",
              marginTop: pwc.space.md,
            }}
          >
            {report.status !== "ok" && (
              <p style={{ ...ui.bodyText, margin: `${pwc.space.xs}px 0 0` }}>
                The draft contains every safely matched value. Finish the items below in mTool before filing.
              </p>
            )}
            <div style={styles.summaryGrid}>
              <div><div style={styles.summaryValue}>{report.counts.written}</div><div style={{ ...styles.statLine, color: pwc.grey700 }}>figures filled</div></div>
              {report.notes && <div><div style={styles.summaryValue}>{report.notes.counts.written}</div><div style={{ ...styles.statLine, color: pwc.grey700 }}>notes filled</div></div>}
              {notFilledCount > 0 && <div><div style={styles.summaryValue}>{notFilledCount}</div><div style={{ ...styles.statLine, color: pwc.grey700 }}>not filled</div></div>}
            </div>
            {Boolean(report.sheet_selection?.excluded_sheets.length) && (
              <p style={{ ...ui.supportingText, margin: `0 0 ${pwc.space.sm}px`, overflowWrap: "anywhere" }}>
                Partial workbook: {report.sheet_selection!.excluded_sheets.map(friendlyMtoolSheetName).join(", ")} not included. {report.sheet_selection!.excluded_figures} figures {report.sheet_selection!.excluded_notes === null ? "and any notes" : `and ${report.sheet_selection!.excluded_notes} ${report.sheet_selection!.excluded_notes === 1 ? "note" : "notes"}`} were left unchanged.
              </p>
            )}
            <p style={{ ...ui.supportingText, margin: `0 0 ${pwc.space.md}px` }}>After downloading, run Validate &amp; Generate in mTool.</p>
            {(report.unit_scale_warnings?.length ?? 0) > 0 && (
              <div role="alert" style={{ ...ui.alertWarning, marginBottom: pwc.space.md, fontSize: 12 }}>
                This template says its figures are in {report.unit_scale_warnings![0].template_declares}, but this run&apos;s figures are in {report.unit_scale_warnings![0].run_denomination}. Check the amounts before filing.
              </div>
            )}
            <details style={{ fontSize: 12, marginBottom: pwc.space.md }}>
              <summary style={{ cursor: "pointer", fontWeight: pwc.weight.medium }}>
                {notFilledCount > 0 ? `View ${notFilledCount} unfinished items and fill receipt` : "View fill receipt"}
              </summary>
            {Boolean(report.filing_coverage?.operator_resolutions?.length) && (
              <details style={{ marginBottom: pwc.space.sm, fontSize: 12 }}>
                <summary style={{ cursor: "pointer" }}>Confirmed filing destinations ({report.filing_coverage!.operator_resolutions!.length})</summary>
                <ul style={{ margin: "4px 0 0", paddingLeft: 18, color: pwc.grey700 }}>
                  {report.filing_coverage!.operator_resolutions!.map((item, index) => (
                    <li key={`${item.cell}:${index}`} style={{ overflowWrap: "anywhere" }}>
                      {item.label} · {item.period} · {item.entity_scope}: {item.cell}
                      {Object.values(item.dimensions ?? {}).length > 0 && ` · ${Object.values(item.dimensions ?? {}).join(", ")}`}
                    </li>
                  ))}
                </ul>
              </details>
            )}
            {((report.counts.reconciled_formula ?? 0) > 0 || (report.unit_class_unknown?.length ?? 0) > 0) && (
              <details style={{ marginBottom: pwc.space.sm, fontSize: 12 }}>
                <summary style={{ cursor: "pointer" }}>Other details</summary>
                {(report.counts.reconciled_formula ?? 0) > 0 && (
                  <p>{report.counts.reconciled_formula} calculated values agree with the reviewed figures. The template formulas were preserved.</p>
                )}
                {(report.unit_class_unknown?.length ?? 0) > 0 && (
                  <p style={{ color: pwc.grey700 }}>
                    {report.unit_class_unknown!.length} value(s) could not be classified by unit and were written unchanged.
                  </p>
                )}
              </details>
            )}
            {report.notes && <RowDetail title="Notes errors" defaultOpen rows={(report.notes.errors ?? []).map(notesErrorMessage)} />}
            {report.notes && <RowDetail title="Notes that need checking" defaultOpen rows={(report.notes.mismatches ?? []).map((e) => `${e.label ?? e.key ?? "Note"}: ${e.detail ?? (e.found === false ? "The saved note is missing or empty. Check it in mTool." : "The saved note differs from the source. Check it in mTool.")}`)} />}
            {report.notes && <RowDetail title="Notes not filled" rows={(report.notes.unresolved ?? []).map((u) => `${u.label ?? "Note"}: ${u.detail ?? "Complete this note in mTool."}`)} />}
            {/* FULL row detail, not counts (Step 11A). The old header-borne
                report capped these at 20 rows and the UI showed only totals,
                so "which rows didn't land?" was unanswerable. */}
            <RowDetail
              title="Couldn't be placed (not written)"
              rows={[
                ...report.unresolved.map(
                  (u) => `${u.sheet} · ${u.label ?? "(no label)"}${u.detail ? ` — ${u.detail}` : ""}`,
                ),
                ...(report.filing_coverage?.unresolved_writes ?? []).map(
                  (u) => `${u.sheet ?? "Template"} · ${u.label ?? "Figure"}${u.detail ? ` — ${u.detail}` : ""}`,
                ),
                ...(report.filing_coverage?.ambiguous_writes ?? []).map(
                  (u) => `${u.sheet ?? "Template"} · ${u.label ?? "Figure"}${u.detail ? ` — ${u.detail}` : ""}`,
                ),
              ]}
            />
            <RowDetail
              title="Skipped — the cell holds a formula"
              rows={report.skipped_formula.map(
                (s) => `${s.sheet}${s.cell ? `!${s.cell}` : ""} · ${s.label ?? ""}`,
              )}
            />
            <RowDetail
              title="Values that differ from the reviewed figures"
              defaultOpen
              rows={report.mismatches.map(
                (m) => `${m.cell}: expected ${m.expected}, found ${m.found ?? "(empty)"}`,
              )}
            />
            <RowDetail
              title="More than one row matched"
              rows={(report.ambiguous ?? []).map(
                (a) => `${a.sheet} · ${a.label ?? ""}${a.detail ? ` — ${a.detail}` : ""}`,
              )}
            />
            <RowDetail
              title="Matched a similar (not identical) label"
              rows={(report.fuzzy_matched ?? []).map(
                (f) => `${f.sheet} · ${f.label ?? ""}${f.matched_label ? ` → ${f.matched_label}` : ""}`,
              )}
            />
            <RowDetail
              title="Errors"
              defaultOpen
              rows={(report.errors ?? []).map((e) => e.detail ?? e.error ?? "error")}
            />
            {report.notes && (report.notes.styling_disabled ||
              (report.notes.counts.formatting_compacted ?? 0) > 0 ||
              (report.notes.counts.formatting_reduced ?? 0) > 0 ||
              (report.notes.counts.formatting_dropped ?? 0) > 0 ||
              (report.notes.counts.source_styling_dropped ?? 0) > 0 ||
              (report.notes.counts.white_grid_dropped ?? 0) > 0) && (
              <details style={{ marginTop: 6, fontSize: 12 }}>
                <summary style={{ cursor: "pointer" }}>Note formatting details</summary>
                {report.notes.styling_disabled && (
                  <p style={{ color: pwc.grey700 }}>
                    Written without styling — you chose the diagnostic “No styling” option,
                    so plain-looking notes are expected.
                  </p>
                )}
                {!report.notes.styling_disabled &&
                  ((report.notes.counts.formatting_compacted ?? 0) > 0 ||
                    (report.notes.counts.formatting_reduced ?? 0) > 0 ||
                    (report.notes.counts.formatting_dropped ?? 0) > 0 ||
                    (report.notes.counts.source_styling_dropped ?? 0) > 0 ||
                    (report.notes.counts.white_grid_dropped ?? 0) > 0) && (
                    <p style={{ color: pwc.grey700 }}>
                      {[
                        (report.notes.counts.formatting_compacted ?? 0) > 0 &&
                          `${report.notes.counts.formatting_compacted} large note(s) used slimmer styling (looks the same)`,
                        (report.notes.counts.formatting_reduced ?? 0) > 0 &&
                          `${report.notes.counts.formatting_reduced} note(s) lost minor styling to fit`,
                        (report.notes.counts.formatting_dropped ?? 0) > 0 &&
                          `${report.notes.counts.formatting_dropped} note(s) written without styling (too large — consider splitting the note)`,
                        (report.notes.counts.source_styling_dropped ?? 0) > 0 &&
                          `${report.notes.counts.source_styling_dropped} note(s) were too large to keep the Word document's own styling — filed with standard styling instead`,
                        (report.notes.counts.white_grid_dropped ?? 0) > 0 &&
                          `${report.notes.counts.white_grid_dropped} note(s) may show mTool's default grey gridlines (the white-line painting was dropped to fit)`,
                      ]
                        .filter(Boolean)
                        .join(" · ")}
                    </p>
                  )}
              </details>
            )}
            </details>
          </div>
        )}

        {downloadErr && (
          <div style={{ ...ui.alertError, marginTop: pwc.space.sm }}>
            Download failed: {downloadErr}
          </div>
        )}
        {downloaded && (
          <div style={{ ...styles.statLine, color: pwc.grey700, marginTop: pwc.space.sm }}>
            Downloaded. Open it in mTool and run Validate &amp; Generate.
          </div>
        )}

        </div>
        <div style={{ ...styles.actions, flexShrink: 0, paddingTop: pwc.space.md, borderTop: `1px solid ${pwc.grey200}` }}>
          {!report && <button type="button" onClick={onClose} className={uiClass.btnGhost} style={ui.buttonGhost}>Cancel</button>}
          <button
            type="button"
            ref={fillButtonRef}
            aria-label={report ? "Change options" : "Fill"}
            onClick={report ? () => setReport(null) : requestSubmit}
            disabled={!file || busy || downloading || fillQueued || selectedSheets?.length === 0 || templateSettings?.filing_family_match === false}
            className={report ? uiClass.btnSecondary : uiClass.btnPrimary}
            style={report ? ui.buttonSecondary : ui.buttonPrimary}
          >
            {busy || fillQueued ? "Preparing…" : report ? "Change options" : "Fill template"}
          </button>
          {report && (
            <button
              type="button"
              onClick={download}
              disabled={downloading || busy}
              className={uiClass.btnPrimary}
              style={ui.buttonPrimary}
              title={
                report.status === "ok"
                  ? "Download the filled template"
                  : "Download with the review items recorded on the fill receipt"
              }
            >
              {downloading ? "Downloading…" : report.status === "ok" ? "Download draft" : "Download draft for review"}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
