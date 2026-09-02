import React, { useEffect, useRef, useState } from "react";
import { userMessage } from "../lib/errors";
import { pwc } from "../lib/theme";
import { ui, uiClass } from "../lib/uiStyles";
import { denominationLabel } from "../lib/vocabulary";
import { FileDropzone } from "./FileDropzone";

/**
 * mTool fill modal (docs/PLAN-mtool-fill-pipeline.md Phase 4, Steps 9/11/11A).
 *
 * Launched from the run-detail action row (a button, NOT a tab — gotcha #7).
 * There is no exposure flag (2026-08-05 replay decision) — the filing safety
 * is the preflight gate and the report-before-file acknowledgement below.
 *
 * Four steps in one dialog:
 *   1. Is this run ready to file? GET /mtool-fill/preflight. Blocking is the
 *      default; an override needs the operator to write down why.
 *   2. Show the fill coverage the run will produce (GET /mtool-fill).
 *   3. Upload the empty mTool template; POST /mtool-fill/patch returns the
 *      COMPLETE report — no file yet.
 *   4. Only then, download the workbook from the artifact URL.
 *
 * Step 3/4 being separate is the fix for Step 11's unmet criterion: the old
 * flow fired the download first and parsed a capped report afterwards, so the
 * operator held the file before they could see whether it was clean.
 */

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
  reason?: string; // ambiguous | strict_near_miss | no_match | no_slot | no_payload_row
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
  // Step 11A: the report arrives first, the file is fetched separately.
  artifact_id?: string;
  download_url?: string;
  filename?: string;
  receipt_id?: number | null;
  template_known?: boolean;
  filing_coverage?: {
    status: string;
    requested: number;
    mapped: number;
    unmapped: number;
    ambiguous: number;
    coverage_percent: number;
  };
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
  noteCard: {
    borderTop: `1px solid ${pwc.grey200}`,
    padding: `${pwc.space.sm}px 0`,
    fontSize: 12,
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
 * "blocked" — the server enforces the gate for real (409 on the patch), so a
 * UI that invented a blocker would only ever block the honest case. */
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

function reviewedExceptionLabel(code: string): string {
  if (code === "MFRS_ISSUED_CAPITAL_WRAPPER_OMITTED") {
    return "Issued capital template uses the approved SSM row alignment";
  }
  if (code === "MFRS_RELATED_PARTY_WRAPPER_OMITTED") {
    return "Related party template uses the approved SSM row alignment";
  }
  if (code === "PRESENTATION_TITLE_WITHOUT_TAXONOMY_SLOT") {
    return "Statement title is display-only";
  }
  if (code === "SOCIE_SECTION_HEADER_WITHOUT_TAXONOMY_SLOT") {
    return "Equity section heading is display-only";
  }
  return "Reviewed display-only template difference";
}

/** One category of problem rows from the fill report, listed in FULL.
 *
 * Nothing is capped here. The report used to ride an HTTP header limited to 20
 * rows and 6 KB, and the UI rendered only counts — so "which rows didn't
 * land?" was a question the operator could not answer from the screen. */
function RowDetail({ title, rows }: { title: string; rows: string[] }) {
  if (rows.length === 0) return null;
  return (
    <details style={{ marginTop: 6, fontSize: 12 }} open={rows.length <= 8}>
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
  return reason === "ambiguous" || reason === "strict_near_miss" ||
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
  const [meta, setMeta] = useState<FillMeta | null>(null);
  const [notesCount, setNotesCount] = useState<number | null>(null);
  const [fillNotes, setFillNotes] = useState(true);
  // Default ON: a template freshly exported from mTool has no note spots
  // provisioned, so leaving this off silently placed zero notes (run 75) and
  // read as a broken fill rather than a missing opt-in. The safe posture is
  // still served by the automatic preview, which shows what would be created
  // before anything is written.
  const [createMissingNotes, setCreateMissingNotes] = useState(true);
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
  const [dimensionalSheets, setDimensionalSheets] = useState<string[]>([]);
  const [columnConfidence, setColumnConfidence] = useState<string | null>(null);
  const [detectBusy, setDetectBusy] = useState(false);
  const [detectErr, setDetectErr] = useState<string | null>(null);
  // Filing-readiness gate (Step 8A). `preflightAck` is the operator's written
  // reason for overriding it — recorded on the fill receipt, so it is a
  // decision on the record rather than a checkbox nobody can trace.
  const [preflight, setPreflight] = useState<Preflight | null>(null);
  const [preflightAck, setPreflightAck] = useState("");
  // Step 11A: the workbook waits behind its own request until the operator has
  // seen the report (and, when degraded, said so).
  const [degradedAck, setDegradedAck] = useState(false);
  const [downloadErr, setDownloadErr] = useState<string | null>(null);
  const [downloaded, setDownloaded] = useState(false);
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
    setDetectErr(null);
    setColumnPrompt(null);
    setPreview(null);
    setPreviewErr(null);
    setNoteTargets({});
    setPreflight(null);
    setPreflightAck("");
    setDegradedAck(false);
    setDownloadErr(null);
    setDownloaded(false);
    // Is this run's data settled enough to file? Asked up front so a blocked
    // run says so before the operator hunts for their template.
    fetch(`/api/runs/${runId}/mtool-fill/preflight`)
      .then((r) => (r.ok ? r.json() : null))
      .then((body) => setPreflight(normalisePreflight(body)))
      .catch(() => setPreflight(null));
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
    setDegradedAck(false);
    setDownloaded(false);
    setDownloadErr(null);
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
      if (preflightAck.trim()) form.append("acknowledge_preflight", preflightAck.trim());
      const resp = await fetch(`/api/runs/${runId}/mtool-fill/patch`, {
        method: "POST",
        body: form,
      });
      if (!resp.ok) {
        const body = await resp.json().catch(() => ({}));
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
          const coverage = detail.filing_coverage as {
            unresolved_writes?: { detail?: string }[];
          };
          const reasons = (coverage.unresolved_writes ?? [])
            .map((item) => item.detail)
            .filter((item): item is string => Boolean(item));
          throw new Error(
            reasons.length > 0
              ? reasons.join(" ")
              : typeof detail.error === "string"
                ? detail.error
                : "Some filing values could not be mapped safely to this template.",
          );
        }
        throw new Error(
          typeof detail === "string" ? detail : detail ? JSON.stringify(detail) : `HTTP ${resp.status}`
        );
      }
      // The response is the REPORT, not the file. The workbook waits behind
      // its own request until the operator has seen this (Step 11A).
      setReport((await resp.json()) as ReportSummary);
    } catch (e) {
      setPatchErr(userMessage(e));
    } finally {
      setBusy(false);
    }
  };

  // Step two of the split: fetch the workbook the fill produced. A degraded
  // fill only releases it once the operator has acknowledged the report.
  const download = async () => {
    if (!report?.download_url) return;
    // Belt to the disabled button's braces — and the server refuses too, so a
    // degraded workbook can't leave without an acknowledgement on the record.
    if (report.status !== "ok" && !degradedAck) return;
    setDownloadErr(null);
    try {
      const url =
        report.status === "ok"
          ? report.download_url
          : `${report.download_url}?acknowledge_degraded=${encodeURIComponent(
              "operator confirmed after reading the report",
            )}`;
      const resp = await fetch(url);
      if (!resp.ok) {
        const body = await resp.json().catch(() => ({}));
        throw new Error(
          typeof body?.detail === "string" ? body.detail : `HTTP ${resp.status}`,
        );
      }
      const blob = await resp.blob();
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
      setDownloadErr(userMessage(e));
    }
  };

  // Dry-run diagnostic: what would fill / get created / stay unresolved, and
  // how many fn_* slots the uploaded template exposes. Writes nothing. Sends
  // the operator's placement decisions so a re-check reflects them.
  const runPreview = async (selectedFile?: File) => {
    const targetFile = selectedFile ?? file;
    if (!targetFile) return;
    setPreviewBusy(true);
    setPreviewErr(null);
    setPreview(null);
    try {
      const form = new FormData();
      form.append("template", targetFile);
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
      setDetectErr(userMessage(e));
    } finally {
      if (!stale()) setDetectBusy(false);
    }
  };

  // The run isn't ready to file and nobody has said why they're going ahead.
  const blockedUnacknowledged =
    preflight != null && !preflight.ok && preflightAck.trim().length === 0;

  const c = meta?.counts;
  const excludedParts = c ? [
    c.excluded_matrix_socie > 0 && `${c.excluded_matrix_socie} category/matrix`,
    c.excluded_not_disclosed > 0 && `${c.excluded_not_disclosed} not disclosed`,
    c.excluded_out_of_scope > 0 && `${c.excluded_out_of_scope} out of scope`,
    c.excluded_no_value > 0 && `${c.excluded_no_value} without a value`,
  ].filter((item): item is string => Boolean(item)) : [];
  const totalExcluded = c
    ? c.excluded_matrix_socie + c.excluded_not_disclosed +
      c.excluded_out_of_scope + c.excluded_no_value
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
            data-tooltip="Close"
            onClick={onClose}
            style={styles.closeX}
          >
            ✕
          </button>
        </div>
        <p style={styles.sub}>
          Choose the empty Excel template exported from mTool. We&apos;ll place this
          run&apos;s figures and notes, then return one file for Validate &amp; Generate.
        </p>

        {loadErr && (
          <div style={ui.alertError}>Could not load fill data: {loadErr}</div>
        )}

        {/* Filing-readiness gate. Blocking is the default; overriding means
            writing down why, and that reason goes on the permanent record. */}
        {preflight && !preflight.ok && (
          <section style={{ ...ui.alertWarning, marginBottom: pwc.space.md }} aria-label="Not ready to file">
            <div style={{ fontWeight: pwc.weight.medium }}>
              This run isn&apos;t ready to file yet
            </div>
            <ul style={{ margin: "6px 0 0", paddingLeft: 18, fontSize: 12 }}>
              {preflight.blockers.map((b) => (
                <li key={b.code} style={{ marginBottom: 4 }}>
                  {b.message}
                  {b.examples.length > 0 && (
                    <ul style={{ margin: "2px 0 0", paddingLeft: 16, color: pwc.grey700 }}>
                      {b.examples.map((e, i) => (
                        <li key={i}>{e}</li>
                      ))}
                    </ul>
                  )}
                </li>
              ))}
            </ul>
            <label style={{ display: "block", marginTop: pwc.space.sm, fontSize: 12 }}>
              To continue, record why these checks do not prevent this filing.
              <input
                aria-label="Reason for filing anyway"
                value={preflightAck}
                onChange={(e) => setPreflightAck(e.target.value)}
                placeholder="Reason for continuing"
                style={{ display: "block", width: "100%", marginTop: 4, fontSize: 12 }}
              />
            </label>
          </section>
        )}

        {preflight && preflight.warnings.length > 0 && (
          <details style={{ marginBottom: pwc.space.sm, fontSize: 12, color: pwc.grey700 }}>
            <summary style={{ cursor: "pointer" }}>Other run details ({preflight.warnings.length})</summary>
            {preflight.warnings.map((w) => <div key={w.code}>{w.message}</div>)}
          </details>
        )}

        {preflight?.field_semantics && (
          <section
            aria-label="Filing field coverage"
            style={{
              border: `1px solid ${pwc.grey200}`,
              borderRadius: pwc.radius.md,
              padding: pwc.space.md,
              marginBottom: pwc.space.lg,
              background: pwc.grey50,
            }}
          >
            <strong style={{ fontSize: 13, color: pwc.grey900 }}>
              Field mapping is {preflight.field_semantics.readiness === "ready" ? "ready" : "incomplete"}
            </strong>
            <details style={{ marginTop: 6, fontSize: 12 }}>
              <summary style={{ cursor: "pointer", color: pwc.grey700 }}>Mapping details</summary>
              <div style={{ ...styles.statLine, color: pwc.grey700, marginTop: 6 }}>
                <strong>{preflight.field_semantics.counts.writable_fields}</strong> writable fields
                {preflight.field_semantics.counts.unresolved_fields > 0
                  ? ` · ${preflight.field_semantics.counts.unresolved_fields} unmapped`
                  : " · no fields are missing"}
                {preflight.field_semantics.counts.quarantined_values > 0
                  ? ` · ${preflight.field_semantics.counts.quarantined_values} stored value(s) need review`
                  : ""}
              </div>
              {preflight.field_semantics.reviewed_exceptions.length > 0 && (
                <div style={{ marginTop: 6 }}>
                  <div style={{ color: pwc.grey700 }}>
                    Reviewed template exceptions ({preflight.field_semantics.reviewed_exceptions.length})
                  </div>
                  <ul style={{ margin: "4px 0 0", paddingLeft: 18, color: pwc.grey700 }}>
                    {preflight.field_semantics.reviewed_exceptions.map((item) => (
                      <li key={item.exception_code}>
                        {reviewedExceptionLabel(item.exception_code)} ({item.count})
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </details>
          </section>
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
                {`${totalExcluded} ${totalExcluded === 1 ? "value" : "values"} excluded from this filing: ${excludedParts.join(", ")}.`}
              </div>
            )}
            {c!.conflict_writes > 0 && (
              <div style={{ ...styles.statLine, color: pwc.orange700 }}>
                {`${c!.conflict_writes} ${c!.conflict_writes === 1 ? "value" : "values"} still in conflict will be written — resolve ${c!.conflict_writes === 1 ? "it" : "them"} in Review values first.`}
              </div>
            )}
            {notesCount !== null && (
              <div style={styles.statLine}>
                <strong>{notesCount}</strong> written note(s) will be filled in
              </div>
            )}
          </div>
        )}

        <details style={{ marginBottom: pwc.space.md }}>
          <summary style={{ cursor: "pointer", fontSize: 13, color: pwc.grey700 }}>
            Optional settings
          </summary>
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
                On by default — a template exported straight from mTool usually has
                no spots yet. Untick it to fill only the spots that already exist,
                and run “Check notes” to see the plan before writing.
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
        </details>

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
              setDimensionalSheets([]);
              setColumnConfidence(null);
              setDetectErr(null);
              setDetectBusy(false);
              setColumnPrompt(null);
              setPreview(null); // a different template ⇒ a different plan
              setPreviewErr(null);
              setNoteTargets({}); // decisions were made against the old template
              runDetect(f); // confirm the column layout up front
              if (notesCount !== null && notesCount > 0 && fillNotes) {
                void runPreview(f);
              }
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
        {dimensionalSheets.length > 0 && !detectBusy && !detectErr && (
          <div
            style={{
              border: `1px solid ${pwc.grey200}`,
              borderRadius: pwc.radius.md,
              padding: `${pwc.space.sm}px ${pwc.space.md}px`,
              marginBottom: pwc.space.md,
              color: pwc.grey700,
              fontSize: 12,
            }}
          >
            <strong style={{ color: pwc.grey900 }}>
              {dimensionalSheets.some((sheet) => sheet.toUpperCase().includes("SOCIE"))
                ? "SOCIE matrix recognised"
                : "Category-based sheets recognised"}
            </strong>
            <div style={{ marginTop: 2 }}>
              These columns are matched by taxonomy member, such as equity component
              or share class. There are no current-year or prior-year columns to choose.
            </div>
          </div>
        )}

        {notesCount !== null && notesCount > 0 && fillNotes && (
          <div style={{ marginBottom: pwc.space.md }}>
            {previewBusy && (
              <div style={{ color: pwc.grey500, fontSize: 12 }}>Checking note placement…</div>
            )}
            {previewErr && (
              <div style={{ ...ui.alertError, marginTop: pwc.space.sm }}>
                Couldn&apos;t check note placement: {previewErr}
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
                <div style={{ color: pwc.grey900, fontWeight: pwc.weight.medium }}>
                  {preview.will_fill_existing.length + preview.will_create.length} of {preview.notes_in_run} notes
                  will be placed automatically
                </div>
                {preview.unresolved.length > 0 && (
                  <div style={{ color: pwc.grey700, marginTop: 4 }}>
                    {preview.unresolved.length} {preview.unresolved.length === 1 ? "note has" : "notes have"} no
                    certain destination. You can leave {preview.unresolved.length === 1 ? "it" : "them"} for
                    manual completion in mTool or optionally choose a destination below.
                  </div>
                )}

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
                  title="Needs your decision"
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
              </div>
            )}
          </div>
        )}

        {patchErr && (
          <div style={ui.alertError}>Fill failed: {patchErr}</div>
        )}
        {columnPrompt && (
          <div
            style={{
              border: `1px solid ${pwc.grey200}`,
              borderRadius: pwc.radius.md,
              padding: `${pwc.space.sm}px ${pwc.space.md}px`,
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
            {report.filing_coverage && (
              <div style={{ fontSize: 12, marginTop: 4, color: pwc.grey700 }}>
                Filing coverage: {report.filing_coverage.mapped}/{report.filing_coverage.requested}
                {" "}values mapped ({report.filing_coverage.coverage_percent}%).
              </div>
            )}
            {/* FULL row detail, not counts (Step 11A). The old header-borne
                report capped these at 20 rows and the UI showed only totals,
                so "which rows didn't land?" was unanswerable. */}
            <RowDetail
              title="Couldn't be placed (not written)"
              rows={report.unresolved.map(
                (u) => `${u.sheet} · ${u.label ?? "(no label)"}${u.detail ? ` — ${u.detail}` : ""}`,
              )}
            />
            <RowDetail
              title="Skipped — the cell holds a formula"
              rows={report.skipped_formula.map(
                (s) => `${s.sheet}${s.cell ? `!${s.cell}` : ""} · ${s.label ?? ""}`,
              )}
            />
            <RowDetail
              title="Written but read back differently"
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
              rows={(report.errors ?? []).map((e) => e.detail ?? e.error ?? "error")}
            />
            {(report.unit_scale_warnings?.length ?? 0) > 0 && (
              <div style={{ ...ui.alertWarning, marginTop: 6, fontSize: 12 }}>
                This template says its figures are in{" "}
                {report.unit_scale_warnings![0].template_declares}, but this run&apos;s
                figures are in {report.unit_scale_warnings![0].run_denomination}. Check
                the amounts before you file — a mismatch here is off by a factor of a
                thousand.
              </div>
            )}
            {(report.unit_class_unknown?.length ?? 0) > 0 && (
              <div style={{ color: pwc.grey700, marginTop: 6, fontSize: 12 }}>
                {report.unit_class_unknown!.length} row(s) aren&apos;t in the SSM unit
                index, so we couldn&apos;t tell whether they hold money, share counts or
                a ratio. They were written exactly as stored.
              </div>
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
                    (report.notes.counts.formatting_dropped ?? 0) > 0 ||
                    (report.notes.counts.source_styling_dropped ?? 0) > 0 ||
                    (report.notes.counts.white_grid_dropped ?? 0) > 0) && (
                    <div style={{ color: pwc.grey700 }}>
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
                    </div>
                  )}
              </div>
            )}
          </div>
        )}

        {/* Step 11A: the workbook is a SECOND action, taken once the report
            above has been read. A degraded fill needs it said out loud. */}
        {report && report.status !== "ok" && (
          <label
            style={{ ...styles.statLine, display: "flex", alignItems: "flex-start", gap: 6, marginTop: pwc.space.sm }}
          >
            <input
              type="checkbox"
              checked={degradedAck}
              onChange={(e) => setDegradedAck(e.target.checked)}
              aria-label="I have read the problems above"
              style={{ marginTop: 2 }}
            />
            <span>
              I&apos;ve read the problems above and still want the file
              <span style={{ display: "block", color: pwc.grey700, fontSize: 12 }}>
                Kept with the filing record.
              </span>
            </span>
          </label>
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

        <div style={styles.actions}>
          <button type="button" onClick={onClose} className={uiClass.btnGhost} style={ui.buttonGhost}>
            Close
          </button>
          <button
            type="button"
            onClick={submit}
            disabled={!file || busy || blockedUnacknowledged}
            className={report ? uiClass.btnSecondary : uiClass.btnPrimary}
            style={report ? ui.buttonSecondary : ui.buttonPrimary}
            title={
              blockedUnacknowledged
                ? "Resolve the points above, or say why you're going ahead"
                : undefined
            }
          >
            {busy ? "Filling…" : report ? "Fill again" : "Fill"}
          </button>
          {report && (
            <button
              type="button"
              onClick={download}
              disabled={report.status !== "ok" && !degradedAck}
              className={uiClass.btnPrimary}
              style={ui.buttonPrimary}
              title={
                report.status === "ok"
                  ? "Download the filled template"
                  : "Confirm you've read the problems above first"
              }
            >
              Download filled template
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
