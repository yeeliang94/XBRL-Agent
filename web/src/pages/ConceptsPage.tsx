import { useEffect, useState, useCallback, useRef, useMemo } from "react";
import { ApiError, userMessage } from "../lib/errors";
import { pwc } from "../lib/theme";
import { ui, uiClass } from "../lib/uiStyles";
import { STATUS_SYMBOLS } from "../lib/runStatus";
import { ReconciliationQueue } from "../components/ReconciliationQueue";
import { NotesReviewTab } from "../components/NotesReviewTab";
import { NotesCoverageNav } from "../components/NotesCoverageNav";
import type { CoverageNavRow } from "../components/NotesCoverageNav";
import { NeedsAttentionPanel } from "../components/NeedsAttentionPanel";
import { PdfSourcePane } from "../components/PdfSourcePane";
import { fetchNotesCells, sortSheetsBySlot } from "../lib/notesCells";
import {
  templateDisplayName,
  templateSubtitle,
  templateSortKey,
  notesSheetDisplayName,
} from "../lib/sheetLabels";
import { parseEvidencePages } from "../lib/evidencePages";
import { TERMS } from "../lib/vocabulary";
import {
  formatAccounting,
  formatGroupedInput,
  formatGroupedAccounting,
  parseAccountingInput,
} from "../lib/numberFormat";
import type { CrossCheckResult } from "../lib/types";
import { TemplateSettingsPage } from "./TemplateSettingsPage";

// Phase 3.2 — sentinel selector value that swaps the main panel from the
// face-statement tree/grid to the notes editor, so face statements and
// notes are reviewed in one place.
const NOTES_KEY = "__notes__";

// ---------------------------------------------------------------------------
// ConceptsPage — Phase-1 settings UI for the canonical concept model.
//
// Loads /api/runs/{id}/concepts and renders the tree per template.  LEAF
// rows are editable; COMPUTED rows are read-only (the cascade owns them);
// ABSTRACT rows render as section headers.  display_label overrides are
// inline-editable but never exported (PRD §9).
//
// Reconciliation queue is rendered as a side panel.
//
// Styling is inline-only (gotcha #7) — Tailwind didn't load reliably on
// Windows.
// ---------------------------------------------------------------------------

// Accountant-style number formatters now live in lib/numberFormat so the
// numeric Notes review rows can share them without a circular import (this
// page imports NotesReviewTab). Re-exported here so existing imports/tests
// that pull them from ConceptsPage keep working.
export { formatAccounting, formatGroupedInput };

export interface ConceptRow {
  concept_uuid: string;
  parent_uuid: string | null;
  kind: "ABSTRACT" | "LEAF" | "COMPUTED" | "MATRIX_CELL";
  canonical_label: string;
  display_label: string | null;
  render_sheet: string;
  render_row: number;
  render_col: string;
  // Phase 5 — equity-component column on MATRIX_CELL concepts (SOCIE);
  // null on linear concepts. `shape` is the owning template's layout.
  matrix_col?: string | null;
  matrix_col_label?: string | null;
  shape?: string;
  template_id: string;
  value: number | null;
  value_status: string | null;
  children_status: string | null;
  source: string | null;
  evidence: string | null;
  // True for data-entry cells (LEAF / matrix component) the user may edit;
  // false for section headers and formula totals. Set by the backend.
  editable?: boolean;
  // True when this view-row is an alias for another physical location
  // of the same concept (the cross-sheet rollup case: a face-sheet row
  // that shares its concept_uuid with a sub-sheet *Total). Aliases are
  // never directly editable — the workbook's cross-sheet formula owns
  // the value at the alias coord. Backend emits one view-row per alias
  // so the page can mirror the workbook layout.
  is_alias?: boolean;
  // Phase 4 — per-(scope, period) facts for Group runs.
  // Shape: {Company: {CY: 100, PY: 110}, Group: {CY: 200, PY: 220}}.
  // Absent (or single-scope) on Company runs.
  scope_facts?: Record<string, Record<string, number | null>>;
}

// A value the reviewer can't check against the PDF (UX-QA #6): an editable
// leaf that carries an extracted value but cites no source page. These are the
// rows most needing a manual eyeball, so we badge + let them be filtered — not
// treat them like any other row.
export function rowLacksSource(row: ConceptRow): boolean {
  if (!(row.kind === "LEAF" || row.kind === "MATRIX_CELL")) return false;
  if (row.is_alias) return false;
  if (row.value == null) return false;
  // Mirror the PDF pane's evidence→source fallback (selectedEvidencePages): a
  // page can live in EITHER column, so a row lacks a source only if NEITHER
  // yields a page token. `evidence || source` was wrong — a non-empty evidence
  // string with no page (e.g. "see note") would suppress a valid `source`
  // citation like "SOFP p.12" and falsely badge the row.
  if (parseEvidencePages(row.evidence).length > 0) return false;
  return parseEvidencePages(row.source).length === 0;
}

export interface ConceptsPageProps {
  // Null when the Concepts top-nav tab is opened without a run selected —
  // the page then shows a "pick a run" empty state instead of fetching.
  runId: number | null;
  // Gold-standard eval (v16): when source==='benchmark' the SAME grid renders
  // a benchmark's gold facts (/api/benchmarks/{id}/concepts) and edits PATCH
  // /api/benchmarks/{id}/facts instead of the run-fact endpoints. A minimal
  // prop, NOT a component-library extraction (scope discipline): the run-only
  // chrome (PDF pane, conflicts, notes, recheck, download) is suppressed and a
  // compact gold editor is rendered. `runId` is null in this mode.
  source?: "run" | "benchmark";
  benchmarkId?: number | null;
  // The run's stored cross-checks, passed by the run report so the outcome
  // strip can show "Checks passing X/Y" on load (before any manual re-run).
  // Optional — the standalone template view has none. A manual re-run's fresh
  // results take precedence over this baseline.
  initialCrossChecks?: CrossCheckResult[];
  // Re-extract-notes handler, threaded to the embedded notes editor so its
  // "Re-extract notes" button actually launches a rerun (it used to no-op in
  // this path once the Notes-tab link-out was removed). Optional — absent in
  // the standalone template view, where the button falls back to inert.
  onRegenerateNotes?: (runId: number) => void;
}

type Period = "CY" | "PY";
type RowFilter = "review" | "attention" | "extracted" | "edited" | "calculated" | "no_source" | "blank" | "all";
const ROW_FILTERS: Array<{ value: RowFilter; label: string }> = [
  { value: "review", label: "Review relevant" },
  { value: "attention", label: "Needs attention" },
  { value: "extracted", label: "Extracted" },
  { value: "edited", label: "Edited" },
  { value: "calculated", label: "Calculated" },
  { value: "no_source", label: "No source" },
  { value: "blank", label: "Blank" },
  { value: "all", label: "All" },
];

interface WorkspacePreferences {
  searchQuery?: string;
  rowFilter?: RowFilter;
  activeTemplate?: string | null;
  activeSheet?: string | null;
  selectedConceptUuid?: string | null;
  menuWidth?: number;
  pdfWidth?: number;
  menuCollapsed?: boolean;
  pdfCollapsed?: boolean;
}

function readWorkspacePreferences(runId: number | null): WorkspacePreferences {
  if (runId == null || typeof window === "undefined" || import.meta.env.MODE === "test") return {};
  try {
    return JSON.parse(window.sessionStorage.getItem(`xbrl-review:${runId}`) ?? "{}") as WorkspacePreferences;
  } catch {
    return {};
  }
}

function valueEditKey(uuid: string, period: Period): string {
  return `${uuid}:${period}`;
}

function periodValue(
  row: ConceptRow,
  scope: "Company" | "Group",
  period: Period
): number | null {
  const scoped = row.scope_facts?.[scope];
  if (scoped && Object.prototype.hasOwnProperty.call(scoped, period)) {
    return scoped[period] ?? null;
  }
  return period === "CY" ? row.value ?? null : null;
}

function isMandatoryConcept(row: ConceptRow): boolean {
  return row.canonical_label.trim().startsWith("*");
}

function isBlankValue(value: number | null | undefined): boolean {
  return value == null;
}

function displayValueStatus(status: string | null): string {
  if (!status) return "";
  if (["missing", "pending_input", "not_found"].includes(status)) return "";
  return status;
}

// "cascade" is the internal provenance tag the recompute stamps on COMPUTED
// totals (concept_model/cascade.py). It's redundant with the "Calculated"
// state badge and means nothing to a reviewer, so it's hidden from display.
// The stored value is load-bearing (cascade.py branches on source == "cascade"),
// so it is suppressed only at render time — never removed from the data.
function displaySource(source: string | null | undefined): string {
  if (!source) return "";
  return source.trim().toLowerCase() === "cascade" ? "" : source;
}

function displayConceptSource(row: ConceptRow): string {
  const source = displaySource(row.source);
  if (source.trim().toLowerCase() !== "manual edit") return source;
  const pages = parseEvidencePages(row.evidence);
  if (pages.length === 0) return "Manual edit";
  return `Manual edit · original source page${pages.length === 1 ? "" : "s"} ${pages.join(", ")}`;
}

function treeColumns(showPeriods: boolean): string {
  return showPeriods
    ? "minmax(260px, 1fr) minmax(130px, 160px) minmax(130px, 160px) 120px minmax(120px, 180px)"
    : "minmax(260px, 1fr) minmax(150px, 190px) 120px minmax(120px, 180px)";
}

export function ConceptsPage({
  runId,
  source = "run",
  benchmarkId = null,
  initialCrossChecks,
  onRegenerateNotes,
}: ConceptsPageProps) {
  const initialWorkspace = useRef<WorkspacePreferences>(readWorkspacePreferences(runId));
  // Gold-standard eval (v16): in benchmark mode we read/write gold facts; the
  // run-only effects (edited_count, conflicts, recheck) all short-circuit on
  // `runId == null`, which is exactly the state in benchmark mode, so they stay
  // inert without extra guards. `effectiveId` drives the one shared load.
  const isBenchmark = source === "benchmark";
  const effectiveId = isBenchmark ? benchmarkId : runId;
  const [concepts, setConcepts] = useState<ConceptRow[]>([]);
  // Reporting periods (e.g. "FY2021" / "FY2020") from the run's scout, used to
  // label the CY / PY column headers with their years (D5). Null when scout
  // didn't capture them — the headers stay plain "CY" / "PY".
  const [reportingCy, setReportingCy] = useState<string | null>(null);
  const [reportingPy, setReportingPy] = useState<string | null>(null);
  const [activeTemplate, setActiveTemplate] = useState<string | null>(initialWorkspace.current.activeTemplate ?? null);
  const [loadError, setLoadError] = useState<string | null>(null);
  // Phase 2 (step 2.10): cross-template search.  We keep the search
  // index in the page rather than re-querying so multi-statement
  // navigation stays snappy on slow networks.
  const [searchQuery, setSearchQuery] = useState(initialWorkspace.current.searchQuery ?? "");
  // Exception-led default: reviewers start with values that carry evidence,
  // edits, mandatory relevance, or an issue instead of hundreds of blank
  // taxonomy rows. "All" remains one click away for expert inspection.
  const [rowFilter, setRowFilter] = useState<RowFilter>(initialWorkspace.current.rowFilter ?? "review");
  const [attentionIndex, setAttentionIndex] = useState(0);
  // Phase 4 step 4.12 — Group runs toggle between Company / Group
  // value columns.  Defaults to Company; the toggle is rendered only
  // when at least one concept carries facts in both scopes.
  const [activeScope, setActiveScope] = useState<"Company" | "Group">(
    "Company"
  );
  // Phase 2.1 — per-cell save status for the editable value column.
  // Keyed by concept_uuid so each row shows its own Saving/Saved/Failed
  // badge (mirrors the notes editor's per-cell status pattern).
  const [editStatus, setEditStatus] = useState<
    Record<string, "saving" | "saved" | "error">
  >({});
  // Bumped after every successful value edit so the reconciliation queue
  // re-fetches — a leaf edit can open or clear a partial-state conflict
  // via the cascade. Phase 2.2.
  const [conflictReloadKey, setConflictReloadKey] = useState(0);
  // Phase 2.3 — how many values the user has edited since the run ended.
  // Surfaced as a banner so the user knows overrides exist before they
  // trigger a re-run (which would clobber them). Refreshed after each edit.
  const [editedCount, setEditedCount] = useState(0);
  // Phase 4.3 — on-demand cross-check re-run summary (against current facts).
  const [recheck, setRecheck] = useState<
    { running: boolean; summary: string | null }
  >({ running: false, summary: null });
  // Full per-check results from the last re-run, so a failed cross-statement
  // check (e.g. SOFP no longer balances after an edit) is actionable — not just
  // an aggregate count. Cross-checks validate ACROSS statements; the
  // reconciliation queue validates WITHIN a statement (parent vs children), so
  // they intentionally don't share a surface.
  const [crossChecks, setCrossChecks] = useState<CrossCheckResult[]>([]);
  // Sub-sheet filter (M3 nested nav): when set, the tree shows only this
  // render_sheet within the active template. null = all sheets of the template.
  const [activeSheet, setActiveSheet] = useState<string | null>(initialWorkspace.current.activeSheet ?? null);
  // Notes sub-tabs: the notes sheet names present for this run (in MBRS slot
  // order), and which one the reviewer has selected. The names let the
  // SheetNavigator expand Notes into per-sheet sub-tabs (mirroring the face
  // statements); `activeNotesSheet` is forwarded to NotesReviewTab so it
  // expands + scrolls to that section. Empty list = no notes → Notes stays a
  // single entry. Notes only apply to runs (not the benchmark gold editor).
  const [notesSheets, setNotesSheets] = useState<string[]>([]);
  const [activeNotesSheet, setActiveNotesSheet] = useState<string | null>(null);
  // Source-PDF pages for the notes cell the reviewer last focused. A face
  // concept drives the PDF pane from its evidence string; a notes cell has no
  // concept row, so NotesReviewTab reports the focused cell's `source_pages`
  // up here instead (review-workspace Phase 1). Cleared on navigation below so
  // a stale note's pages don't linger when switching sheets.
  const [notesPdfPages, setNotesPdfPages] = useState<number[]>([]);
  // Whether a notes cell is currently selected — tracked SEPARATELY from
  // notesPdfPages because a selected cell can legitimately have no recorded
  // pages. Inferring selection from pages.length made a page-less cell look
  // like "nothing selected" (run-168 peer-review finding).
  const [notesCellSelected, setNotesCellSelected] = useState(false);
  // Cell the notes checklist last asked the editor to jump to. `key` bumps per
  // click so re-selecting the same note re-scrolls (review-workspace Phase 2).
  const [notesFocusCell, setNotesFocusCell] = useState<{
    sheet: string;
    row: number;
    key: number;
  } | null>(null);
  // Notes-placed count for the outcome strip, reported up by the checklist nav
  // (which already fetches coverage) so we don't fetch it twice.
  const [notesCoverage, setNotesCoverage] = useState<{
    placed: number;
    total: number;
  } | null>(null);
  // Unresolved notes gaps for the Needs-attention queue, reported up by the
  // checklist nav (same coverage fetch).
  const [coverageGaps, setCoverageGaps] = useState<CoverageNavRow[]>([]);
  // Whether the coverage nav has anything to show. We ALWAYS mount the nav (so
  // coverage is fetched even when notes_cells is empty — e.g. inventory
  // unavailable) but only reveal the panel chrome once it reports content.
  const [coverageHasContent, setCoverageHasContent] = useState(false);
  // 3-column workspace layout: the Menu and Source PDF columns are both
  // resizable (drag handle) and hideable (collapse to a thin rail). The
  // Results column flexes to fill the rest.
  const [menuWidth, setMenuWidth] = useState(initialWorkspace.current.menuWidth ?? 280);
  // Wider default so the source PDF is actually readable at rest (UX-QA #7f) —
  // still user-resizable/collapsible for reviewers who want more table room.
  const [pdfWidth, setPdfWidth] = useState(initialWorkspace.current.pdfWidth ?? 520);
  const [menuCollapsed, setMenuCollapsed] = useState(initialWorkspace.current.menuCollapsed ?? false);
  const [pdfCollapsed, setPdfCollapsed] = useState(initialWorkspace.current.pdfCollapsed ?? false);
  // Whether the row carrying the CURRENT selection may scroll itself into
  // view. True only for intentional jumps (row click, reconciliation
  // conflict, cross-check / coverage focus). The initial auto-selection that
  // merely feeds the evidence pane must NOT move the page — on the
  // deep-linked run page it scrolled the document ~2300px down on mount
  // (design-consistency live-QA follow-up).
  const scrollSelectionRef = useRef(false);
  const [selectedConceptUuid, setSelectedConceptUuid] = useState<string | null>(
    initialWorkspace.current.selectedConceptUuid ?? null
  );

  // Preserve review context when the user briefly visits another run tab and
  // returns. Session scope avoids leaking preferences across browsers/users;
  // no review or acknowledgement state is persisted.
  useEffect(() => {
    if (runId == null || import.meta.env.MODE === "test") return;
    const prefs: WorkspacePreferences = {
      searchQuery,
      rowFilter,
      activeTemplate,
      activeSheet,
      selectedConceptUuid,
      menuWidth,
      pdfWidth,
      menuCollapsed,
      pdfCollapsed,
    };
    try {
      window.sessionStorage.setItem(`xbrl-review:${runId}`, JSON.stringify(prefs));
    } catch {
      // Storage can be unavailable in locked-down browsers; review remains
      // fully usable in-memory, so persistence failure is intentionally quiet.
    }
  }, [runId, searchQuery, rowFilter, activeTemplate, activeSheet, selectedConceptUuid, menuWidth, pdfWidth, menuCollapsed, pdfCollapsed]);
  // Abort an in-flight recheck on unmount AND on runId change — otherwise a
  // slow /recheck from run A can land its results onto run B (this component
  // re-renders rather than remounts when runId changes; the other fetches on
  // this page already key their cleanup on runId, so recheck must too). Also
  // clear the now-stale recheck summary + results when the run switches.
  const recheckAbort = useRef<AbortController | null>(null);
  useEffect(() => {
    return () => {
      recheckAbort.current?.abort();
      setRecheck({ running: false, summary: null });
      setCrossChecks([]);
    };
  }, [runId]);

  // Initial load.  Peer-review #11: abort the in-flight request on
  // unmount / runId change so a slow response can't land on a stale
  // component or clobber a newer run's data.
  useEffect(() => {
    if (effectiveId == null) return;
    const controller = new AbortController();
    // Eval (v16): benchmark mode reads gold facts from the benchmark concepts
    // endpoint, which returns the same view-row shape so the grid is unchanged.
    const url = isBenchmark
      ? `/api/benchmarks/${effectiveId}/concepts`
      : `/api/runs/${effectiveId}/concepts`;
    fetch(url, { signal: controller.signal })
      .then((r) => {
        if (!r.ok) throw ApiError.fromResponse(r.status, null);
        return r.json();
      })
      .then((data) => {
        setConcepts(data.concepts || []);
        setReportingCy(data.reporting_period_cy ?? null);
        setReportingPy(data.reporting_period_py ?? null);
        const loaded = (data.concepts || []) as ConceptRow[];
        const firstTemplate = loaded[0]?.template_id || null;
        const preferred = initialWorkspace.current.activeTemplate;
        setActiveTemplate(
          preferred && loaded.some((row) => row.template_id === preferred)
            ? preferred
            : firstTemplate,
        );
      })
      .catch((err) => {
        // AbortError is expected on cleanup — don't surface it.
        if (err?.name === "AbortError") return;
        setLoadError(userMessage(err));
      });
    return () => {
      controller.abort();
    };
  }, [effectiveId, isBenchmark]);

  // Load the run's notes sheet names so the SheetNavigator can show Notes
  // sub-tabs. Run-only (the gold editor has no notes). A run with no notes
  // (404 / empty) leaves the list empty, so Notes stays a single entry.
  useEffect(() => {
    if (runId == null || isBenchmark) return;
    let cancelled = false;
    fetchNotesCells(runId)
      .then((resp) => {
        if (cancelled) return;
        setNotesSheets(sortSheetsBySlot(resp.sheets).map((s) => s.sheet));
      })
      .catch(() => {
        if (!cancelled) setNotesSheets([]);
      });
    return () => {
      cancelled = true;
    };
  }, [runId, isBenchmark]);

  // Refresh the edited-values count on load and after every successful edit
  // (conflictReloadKey is bumped on the same path).
  useEffect(() => {
    if (runId == null) return;
    const controller = new AbortController();
    fetch(`/api/runs/${runId}/facts/edited_count`, { signal: controller.signal })
      .then((r) => (r.ok ? r.json() : { count: 0 }))
      .then((d) => setEditedCount(d.count || 0))
      .catch((err) => {
        if (err?.name !== "AbortError") setEditedCount(0);
      });
    return () => controller.abort();
  }, [runId, conflictReloadKey]);

  // M3 — open-conflict counts per template, for the navigator's badges. We
  // fetch the same /conflicts endpoint the reconciliation queue uses (keyed on
  // conflictReloadKey so it refreshes after an edit) and roll them up by the
  // owning concept's template. A separate lightweight fetch keeps the queue's
  // own resolve/dismiss state untouched.
  const [conflictCounts, setConflictCounts] = useState<Record<string, number>>(
    {}
  );
  useEffect(() => {
    if (runId == null || concepts.length === 0) return;
    const controller = new AbortController();
    fetch(`/api/runs/${runId}/conflicts`, { signal: controller.signal })
      .then((r) => (r.ok ? r.json() : { conflicts: [] }))
      .then((data) => {
        const templateByUuid = new Map(
          concepts.map((c) => [c.concept_uuid, c.template_id])
        );
        const counts: Record<string, number> = {};
        for (const cf of (data.conflicts || []).filter(
          (c: { status: string }) => c.status === "open"
        )) {
          const tid = templateByUuid.get(cf.concept_uuid);
          if (tid) counts[tid] = (counts[tid] || 0) + 1;
        }
        setConflictCounts(counts);
      })
      .catch((err) => {
        if (err?.name !== "AbortError") setConflictCounts({});
      });
    return () => controller.abort();
  }, [runId, conflictReloadKey, concepts]);

  // Phase 2.1 — write a user value edit for one concept in the active
  // (scope, period), then fold the response back into local state: the
  // edited value AND every recomputed ancestor subtotal the cascade
  // returned, so the tree updates in place without a refetch.
  //
  // `keepalive` is set on the unmount-flush path so a pending edit still
  // reaches the server when the user navigates away mid-debounce (mirrors
  // the notes editor). On that path we skip the state update — the
  // component is going away.
  const onEditValue = useCallback(
    async (
      concept_uuid: string,
      value: number | null,
      opts?: { keepalive?: boolean; period?: Period; entity_scope?: "Company" | "Group" }
    ) => {
      if (effectiveId == null) return;
      const keepalive = opts?.keepalive === true;
      const period = opts?.period ?? "CY";
      // Resolve the scope from the edit options, captured when the edit was
      // made — NOT the live `activeScope`. A debounced or unmount-keepalive
      // flush can fire after the user has toggled scope, so reading the
      // current closure value would PATCH the figure under the wrong scope.
      const entity_scope = opts?.entity_scope ?? activeScope;
      const editKey = valueEditKey(concept_uuid, period);
      if (!keepalive) {
        setEditStatus((s) => ({ ...s, [editKey]: "saving" }));
      }
      try {
        // Eval (v16): benchmark gold edits go to the benchmark facts endpoint
        // (composite key in the body); run edits keep the per-concept URL. Gold
        // has no cascade, so the benchmark response carries no `recomputed`.
        const resp = isBenchmark
          ? await fetch(`/api/benchmarks/${effectiveId}/facts`, {
              method: "PATCH",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ concept_uuid, value, period, entity_scope }),
              keepalive,
            })
          : await fetch(`/api/runs/${effectiveId}/facts/${concept_uuid}`, {
              method: "PATCH",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ value, period, entity_scope }),
              keepalive,
            });
        if (keepalive) return;
        if (!resp.ok) {
          setEditStatus((s) => ({ ...s, [editKey]: "error" }));
          return;
        }
        const data = await resp.json();
        // The edited cell plus every recomputed ancestor → a flat
        // {uuid: value} map we apply across the concept list.
        const updates = new Map<string, number | null>();
        updates.set(concept_uuid, value);
        for (const a of data.recomputed || []) {
          updates.set(a.concept_uuid, a.value ?? null);
        }
        setConcepts((prev) =>
          prev.map((c) => {
            if (!updates.has(c.concept_uuid)) return c;
            const v = updates.get(c.concept_uuid) ?? null;
            // Keep both the top-level value (Company linear runs) and the
            // scope_facts entry (Group runs) in sync so a scope/period
            // toggle after an edit still shows the new figure.
            const next = {
              ...c,
              value: entity_scope === "Company" && period === "CY" ? v : c.value,
            };
            if (c.scope_facts || period === "PY" || entity_scope !== "Company") {
              next.scope_facts = {
                ...c.scope_facts,
                [entity_scope]: {
                  ...(c.scope_facts?.[entity_scope] || {}),
                  [period]: v,
                },
              };
            }
            return next;
          })
        );
        setEditStatus((s) => ({ ...s, [editKey]: "saved" }));
        // A leaf edit may open or clear a partial-state conflict in the
        // cascade — refresh the reconciliation queue. (No-op for benchmark
        // gold, which has no cascade/conflicts.)
        setConflictReloadKey((k) => k + 1);
      } catch (err) {
        if (keepalive) return;
        setEditStatus((s) => ({ ...s, [editKey]: "error" }));
      }
    },
    [effectiveId, isBenchmark, activeScope]
  );

  // Phase 4.3 — re-run cross-checks against the current (edited) facts and
  // summarise the pass/fail counts so the user can validate without a full
  // pipeline re-run.
  const onRecheck = useCallback(async () => {
    if (runId == null) return;
    recheckAbort.current?.abort();
    const controller = new AbortController();
    recheckAbort.current = controller;
    setRecheck({ running: true, summary: null });
    try {
      const resp = await fetch(`/api/runs/${runId}/recheck`, {
        signal: controller.signal,
      });
      if (!resp.ok) {
        setRecheck({ running: false, summary: "Re-check unavailable" });
        return;
      }
      const data = await resp.json();
      const results: CrossCheckResult[] = data.results || [];
      // Backend statuses are "passed" / "failed" / "warning" /
      // "not_applicable" / "pending" (cross_checks.framework). Match those
      // exactly — "pass"/"fail" would always count 0.
      const passed = results.filter((r) => r.status === "passed").length;
      const failed = results.filter((r) => r.status === "failed").length;
      const warnings = results.filter((r) => r.status === "warning").length;
      setCrossChecks(results);
      setRecheck({
        running: false,
        summary: `${passed} passed · ${failed} failed · ${warnings} warnings`,
      });
    } catch (err) {
      if ((err as { name?: string })?.name === "AbortError") return;
      setRecheck({ running: false, summary: "Re-check failed" });
    }
  }, [runId]);

  // Review Workspace M2 — select a concept from outside the grid (e.g. a
  // reconciliation conflict). The conflict only knows the concept_uuid, so we
  // look up its template here and switch to it: otherwise the active-template
  // filter would hide the row and the selection effect would reset it. Any
  // active search is cleared for the same reason (search overrides the
  // template view).
  const handleSelectConcept = useCallback(
    (conceptUuid: string) => {
      const target = concepts.find((c) => c.concept_uuid === conceptUuid);
      if (!target) return;
      setActiveTemplate(target.template_id);
      // Show the whole template (clear any sub-sheet filter) so the target row
      // is guaranteed visible regardless of which sub-sheet it lives on.
      setActiveSheet(null);
      setSearchQuery("");
      // An intentional jump (conflict / cross-check / coverage focus) SHOULD
      // bring the row into view.
      scrollSelectionRef.current = true;
      setSelectedConceptUuid(conceptUuid);
    },
    [concepts]
  );

  // Cross-check click-through: a failing check carries a (target_sheet,
  // target_row) anchor (currently only sofp_balance). Resolve it to the owning
  // concept and select it, reusing handleSelectConcept's template/sheet switch.
  const handleSelectTarget = useCallback(
    (sheet: string, row: number) => {
      const target = concepts.find(
        (c) => c.render_sheet === sheet && c.render_row === row
      );
      if (target) handleSelectConcept(target.concept_uuid);
    },
    [concepts, handleSelectConcept]
  );

  // Distinct templates for the navigator, in financial-statement reading
  // order (SOFP → SOPL → SOCI → SOCIE → SOCF) — the backend's incidental
  // order surfaced alphabetically, putting cash flows first (run-168 design
  // critique). Array.sort is stable, so unrecognised templates keep their
  // backend order at the end.
  const templates: string[] = [];
  for (const c of concepts) {
    if (!templates.includes(c.template_id)) templates.push(c.template_id);
  }
  templates.sort((a, b) => templateSortKey(a) - templateSortKey(b));

  // Per-template ordered render_sheets — the navigator expands a face
  // statement (one template, several sub-sheets) into nested entries so the
  // reviewer can jump straight to a sub-sheet instead of scrolling one flat
  // tree. Single-sheet templates have no children and behave as before.
  const sheetsByTemplate = useMemo(() => {
    const map: Record<string, string[]> = {};
    for (const c of concepts) {
      const sheets = (map[c.template_id] ||= []);
      if (!sheets.includes(c.render_sheet)) sheets.push(c.render_sheet);
    }
    return map;
  }, [concepts]);

  // Cross-check state is needed both by the outcome strip and the row filter,
  // so derive it before constructing the visible table rows.
  const effectiveChecks = useMemo(
    () => (crossChecks.length > 0 ? crossChecks : initialCrossChecks ?? []),
    [crossChecks, initialCrossChecks],
  );
  const checksPassing = effectiveChecks.filter((c) => c.status === "passed").length;
  const checksGraded = effectiveChecks.filter(
    (c) => c.status === "passed" || c.status === "failed",
  ).length;
  const advisoryCount = effectiveChecks.filter((c) => c.status === "warning").length;
  const failingChecks = useMemo(
    () => effectiveChecks.filter(
      (c) => c.status === "failed" || c.status === "warning",
    ),
    [effectiveChecks],
  );
  const actionableChecks = useMemo(
    () => failingChecks.filter((c) => c.target_sheet && c.target_row != null),
    [failingChecks],
  );

  // Search overrides the template filter (matches happen across all
  // templates so a user can hop between statements via the result
  // list).  Empty query falls back to the active-template view.
  const notesActive = activeTemplate === NOTES_KEY;

  // Detect Group runs by the presence of ANY concept with Group-side
  // facts.  Phase-1 Company runs have no Group entry; the toggle stays
  // hidden.
  const isGroupRun = concepts.some(
    (c) => c.scope_facts && c.scope_facts.Group !== undefined
  );

  // Show the period toggle when any concept carries a PY fact in any
  // scope — i.e. the run actually extracted prior-year figures.
  const hasPyFacts = concepts.some(
    (c) =>
      c.scope_facts &&
      Object.values(c.scope_facts).some((periods) => periods?.PY !== undefined)
  );

  const { filtered, noSourceCount } = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    const baseRows = q
      ? concepts.filter((c) => {
          const canon = c.canonical_label.toLowerCase();
          const disp = (c.display_label || "").toLowerCase();
          return canon.includes(q) || disp.includes(q);
        })
      : activeTemplate
        ? concepts.filter(
            (c) =>
              c.template_id === activeTemplate &&
              (activeSheet == null || c.render_sheet === activeSheet),
          )
        : concepts;

    const rowHasValue = (row: ConceptRow) => {
      if (row.value != null) return true;
      return Object.values(row.scope_facts ?? {}).some(
        (periods) => periods?.CY != null || periods?.PY != null,
      );
    };
    const rowWasEdited = (row: ConceptRow) =>
      row.source?.trim().toLowerCase() === "manual edit" ||
      editStatus[valueEditKey(row.concept_uuid, "CY")] === "saved" ||
      editStatus[valueEditKey(row.concept_uuid, "PY")] === "saved";
    const rowHasIssue = (row: ConceptRow) =>
      actionableChecks.some(
        (check) =>
          check.target_sheet === row.render_sheet &&
          check.target_row === row.render_row,
      );
    const matchesRowFilter = (row: ConceptRow) => {
      if (rowFilter === "all") return true;
      if (row.kind === "ABSTRACT") return false;
      if (rowFilter === "attention") return rowHasIssue(row) || rowLacksSource(row);
      if (rowFilter === "extracted") return row.kind !== "COMPUTED" && rowHasValue(row);
      if (rowFilter === "edited") return rowWasEdited(row);
      if (rowFilter === "calculated") return row.kind === "COMPUTED" && rowHasValue(row);
      if (rowFilter === "no_source") return rowLacksSource(row);
      if (rowFilter === "blank") return Boolean(row.editable) && !rowHasValue(row);
      return rowHasValue(row) || rowWasEdited(row) || rowHasIssue(row) || rowLacksSource(row) || isMandatoryConcept(row);
    };

    const directMatches = baseRows.filter(matchesRowFilter);
    const visibleUuids = new Set(directMatches.map((row) => row.concept_uuid));
    const byUuid = new Map(concepts.map((row) => [row.concept_uuid, row]));
    for (const row of directMatches) {
      let parent = row.parent_uuid;
      while (parent) {
        visibleUuids.add(parent);
        parent = byUuid.get(parent)?.parent_uuid ?? null;
      }
    }
    return {
      filtered: baseRows.filter((row) => visibleUuids.has(row.concept_uuid)),
      noSourceCount: baseRows.filter(rowLacksSource).length,
    };
  }, [concepts, searchQuery, activeTemplate, activeSheet, rowFilter, editStatus, actionableChecks]);

  useEffect(() => {
    setAttentionIndex((current) =>
      actionableChecks.length === 0
        ? 0
        : Math.min(current, actionableChecks.length - 1),
    );
  }, [actionableChecks.length]);

  useEffect(() => {
    if (notesActive) {
      setSelectedConceptUuid(null);
      return;
    }
    if (filtered.length === 0) {
      setSelectedConceptUuid(null);
      return;
    }
    if (
      selectedConceptUuid &&
      filtered.some((r) => r.concept_uuid === selectedConceptUuid)
    ) {
      return;
    }
    const firstDataRow =
      filtered.find((r) => r.kind !== "ABSTRACT") || filtered[0];
    // Automatic fallback selection — keep the page where it is.
    scrollSelectionRef.current = false;
    setSelectedConceptUuid(firstDataRow.concept_uuid);
  }, [notesActive, filtered, selectedConceptUuid]);

  const selectedConcept =
    selectedConceptUuid == null
      ? null
      : filtered.find((r) => r.concept_uuid === selectedConceptUuid) || null;

  // Memoised so the PDF pane isn't handed a fresh array on every unrelated
  // re-render (which would reset its current page + zoom). Keyed on the
  // evidence string itself.
  const selectedEvidencePages = useMemo(() => {
    // Prefer the evidence string; fall back to the Source column when evidence
    // carries no page token, so a citation that lives in `source` (e.g.
    // "SOFP p.12") still drives the PDF pane instead of showing "no source
    // page recorded" (E7).
    const fromEvidence = parseEvidencePages(selectedConcept?.evidence);
    if (fromEvidence.length > 0) return fromEvidence;
    return parseEvidencePages(selectedConcept?.source);
  }, [selectedConcept?.evidence, selectedConcept?.source]);

  // Clear the focused-note + coverage state on a run switch (this component is
  // reused across runs, not remounted). Without resetting notesCoverage /
  // coverageGaps, the previous run's "Notes placed" metric and Needs-attention
  // rows would linger if the next run self-hides the coverage nav. (Not keyed
  // on sheet/template change — the notes checklist sets both the target pages
  // AND the sheet in one go, so an effect keyed on those would wipe the pages
  // it just set.)
  useEffect(() => {
    setNotesPdfPages([]);
    setNotesCellSelected(false);
    setNotesFocusCell(null);
    setNotesCoverage(null);
    setCoverageGaps([]);
    setCoverageHasContent(false);
  }, [runId]);

  // Focusing a notes cell in the editor: follow its recorded pages (possibly
  // none) and mark the selection. An empty page list is a real state —
  // "selected, but no source page recorded" — not "nothing selected".
  const handleNotesCellPages = useCallback((pages: number[]) => {
    setNotesPdfPages(pages);
    setNotesCellSelected(true);
  }, []);

  // The pages the Source PDF pane should show: a focused notes cell's pages
  // when the notes editor is active, otherwise the selected face concept's
  // evidence pages.
  const pdfPages = notesActive ? notesPdfPages : selectedEvidencePages;

  // Notes checklist click → navigate to that note. A placed note opens its
  // sheet and scrolls to the exact cell; a missing note opens the Source PDF at
  // the note's inventory pages so the reviewer can see what wasn't captured.
  // Either way the PDF follows the note's page range.
  const handleCoverageSelect = useCallback((row: CoverageNavRow) => {
    setSearchQuery("");
    setActiveTemplate(NOTES_KEY);
    const pages =
      row.page_lo != null
        ? Array.from(
            { length: Math.max(0, (row.page_hi ?? row.page_lo) - row.page_lo) + 1 },
            (_, i) => (row.page_lo as number) + i,
          )
        : [];
    setNotesPdfPages(pages);
    const placement = row.placements[0];
    // A placed note targets a specific cell (selected); a missing note has
    // no cell to select — the pane just shows the note's inventory pages.
    setNotesCellSelected(!!placement);
    if (placement) {
      setActiveNotesSheet(placement.sheet);
      setNotesFocusCell((c) => ({
        sheet: placement.sheet,
        row: placement.row,
        key: (c?.key ?? 0) + 1,
      }));
    } else {
      // Nowhere placed — show all notes and let the PDF carry the evidence.
      setActiveNotesSheet(null);
      setNotesFocusCell(null);
    }
  }, []);

  // Eval (v16): benchmark gold editor — a compact reuse of the same grid,
  // without the run-only chrome (no PDF pane, conflicts, notes, download). Gold
  // LEAF/MATRIX cells are editable; edits PATCH the benchmark facts endpoint.
  if (isBenchmark) {
    if (benchmarkId == null) {
      return (
        <div data-testid="benchmark-gold-empty" style={{ padding: pwc.space.xl }}>
          <p style={styles.panelMuted}>Select a benchmark to edit its reference values.</p>
        </div>
      );
    }
    return (
      <div
        data-testid="benchmark-gold-editor"
        style={{ display: "flex", flexDirection: "column", gap: pwc.space.lg }}
      >
        {loadError && (
          <div style={styles.errorBanner}>Failed to load reference values: {loadError}</div>
        )}
        <div style={ui.alertInfo} role="note">
          Reference-value edits save automatically when you leave a field. Each edited row shows Saving, Saved, or Save failed.
        </div>
        <section style={styles.toolbar} aria-label="Reference editor controls">
          {templates.length > 1 && (
            <div style={styles.controlGroup}>
              <label htmlFor="gold-template" style={ui.fieldLabel}>Statement</label>
              <select
                id="gold-template"
                data-testid="gold-template-select"
                value={activeTemplate ?? ""}
                onChange={(e) => {
                  setActiveTemplate(e.target.value);
                  setActiveSheet(null);
                  setSearchQuery("");
                }}
                style={ui.select}
              >
                {templates.map((tid) => (
                  <option key={tid} value={tid}>{templateDisplayName(tid)}</option>
                ))}
              </select>
            </div>
          )}
          {isGroupRun && (
            <div style={styles.controlGroup}>
              <span style={ui.fieldLabel}>Entity</span>
              <SegmentedControl
                testId="gold-entity-scope-toggle"
                values={["Company", "Group"] as const}
                activeValue={activeScope}
                onChange={setActiveScope}
                buttonTestId={(scope) => `gold-scope-btn-${scope}`}
              />
            </div>
          )}
          <div style={styles.searchGroup}>
            <label htmlFor="gold-search" style={ui.fieldLabel}>Search</label>
            <input
              id="gold-search"
              data-testid="gold-search"
              type="search"
              placeholder="Search all sheets"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              style={{ ...ui.input, width: "100%" }}
            />
          </div>
        </section>
        {filtered.length > 0 && filtered.every((r) => r.shape === "matrix") ? (
          <ConceptMatrixGrid
            rows={filtered}
            onEditValue={onEditValue}
            editStatus={editStatus}
            selectedUuid={selectedConceptUuid}
            onSelectRow={setSelectedConceptUuid}
            activeScope={activeScope}
            showPeriods={hasPyFacts}
            scrollOnSelectRef={scrollSelectionRef}
          />
        ) : (
          <ConceptTree
            rows={filtered}
            onEditValue={onEditValue}
            editStatus={editStatus}
            selectedUuid={selectedConceptUuid}
            onSelectRow={setSelectedConceptUuid}
            activeScope={activeScope}
            showPeriods={hasPyFacts}
            scrollOnSelectRef={scrollSelectionRef}
          />
        )}
      </div>
    );
  }

  if (runId == null) {
    // No run selected → this surface becomes the global template settings
    // (master-template label editing), separate from per-run value review
    // (Phase 5.1 / 5.3). The "pick a run" guidance moves into the panel.
    return (
      <div data-testid="concepts-page-empty">
        <TemplateSettingsPage />
      </div>
    );
  }

  const totalOpenConflicts = Object.values(conflictCounts).reduce(
    (a, b) => a + b,
    0
  );
  const moveAttention = (delta: number) => {
    if (actionableChecks.length === 0) return;
    const next = (attentionIndex + delta + actionableChecks.length) % actionableChecks.length;
    setAttentionIndex(next);
    const check = actionableChecks[next];
    handleSelectTarget(check.target_sheet as string, check.target_row as number);
  };

  const menuColumn = (
    <div className="review-menu-column" style={{ ...styles.column, flex: `0 0 ${menuWidth}px`, width: menuWidth }}>
      <ColumnHeader
        title={TERMS.documentColumn}
        testId="menu"
        onHide={() => setMenuCollapsed(true)}
      />
      <CollapsiblePanel title="Sheets" testId="panel-sheets">
        <SheetNavigator
          templates={templates}
          sheetsByTemplate={sheetsByTemplate}
          activeTemplate={activeTemplate}
          activeSheet={activeSheet}
          notesKey={NOTES_KEY}
          notesSheets={notesSheets}
          activeNotesSheet={activeNotesSheet}
          conflictCounts={conflictCounts}
          onSelectTemplate={(tid) => {
            // Switching sheets clears any active search so the chosen sheet's
            // rows are actually shown (search overrides the template view), and
            // clears the sub-sheet filter so the whole template is shown.
            setSearchQuery("");
            setActiveTemplate(tid);
            setActiveSheet(null);
            // Selecting the Notes header (or any face template) clears the
            // notes sub-sheet focus so the editor shows all notes again.
            setActiveNotesSheet(null);
          }}
          onSelectSheet={(tid, sheet) => {
            setSearchQuery("");
            setActiveTemplate(tid);
            setActiveSheet(sheet);
          }}
          onSelectNotesSheet={(sheet) => {
            setSearchQuery("");
            setActiveTemplate(NOTES_KEY);
            setActiveNotesSheet(sheet);
            // Manual sub-tab switch: drop the previous note's PDF pages AND
            // its selection so the pane waits for a fresh cell focus rather
            // than showing stale pages.
            setNotesPdfPages([]);
            setNotesCellSelected(false);
          }}
        />
      </CollapsiblePanel>
      {/* Always mounted so /notes-coverage is fetched for EVERY run — a run
          whose notes extraction produced no cells can still have an
          inventory-unavailable state that must surface loudly (gotcha #27).
          The nav self-hides on a face-only / pre-feature run and reports that
          via onVisible, so we keep the panel chrome hidden (display:none, not
          unmounted) until there's content. */}
      <div style={coverageHasContent ? undefined : { display: "none" }}>
        <CollapsiblePanel title="Notes checklist" testId="panel-notes-checklist">
          <NotesCoverageNav
            runId={runId}
            activeSheet={notesActive ? activeNotesSheet : null}
            onSelectNote={handleCoverageSelect}
            onSummary={setNotesCoverage}
            onGaps={setCoverageGaps}
            onVisible={setCoverageHasContent}
          />
        </CollapsiblePanel>
      </div>
      <CollapsiblePanel title={TERMS.needsAttention} testId="panel-attention">
        <NeedsAttentionPanel
          failingChecks={failingChecks}
          onSelectCheck={handleSelectTarget}
          coverageGaps={coverageGaps}
          onSelectNote={handleCoverageSelect}
          openConflicts={totalOpenConflicts}
          reconciliation={
            <ReconciliationQueue
              runId={runId}
              reloadKey={conflictReloadKey}
              onSelectConcept={handleSelectConcept}
              embedded
            />
          }
        />
      </CollapsiblePanel>
    </div>
  );

  const pdfColumn = (
    <div className="review-source-column" style={{ ...styles.column, flex: `0 0 ${pdfWidth}px`, width: pdfWidth }}>
      <ColumnHeader
        title="Source PDF"
        testId="pdf"
        onHide={() => setPdfCollapsed(true)}
      />
      {/* Source-PDF verification: the pane follows the selected concept's
          evidence pages so a reviewer can eyeball the figure against the
          document without leaving the page (M1). `embedded` because the
          column header above already says "Source PDF" and owns Hide. */}
      <PdfSourcePane
        runId={runId}
        pages={pdfPages}
        embedded
        hasSelection={notesActive ? notesCellSelected : selectedConcept != null}
      />
      {/* Field details — the technical metadata (template, cell, source,
          evidence) for the selected value. Collapsed by default so the everyday
          view stays label + figures; opened on demand (review-workspace
          Phase 3). Only meaningful for a face concept, so hidden on notes. */}
      {!notesActive && (
        <CollapsiblePanel
          title="Field details"
          testId="panel-details"
          defaultOpen={false}
        >
          <ConceptEvidenceBody concept={selectedConcept} />
        </CollapsiblePanel>
      )}
    </div>
  );

  return (
    <div data-testid="concepts-page" className="review-workspace" style={styles.shell}>
      {/* Column 1 — Document (statements, notes checklist, needs-attention) */}
      {menuCollapsed ? (
        <CollapsedRail
          label={TERMS.documentColumn}
          testId="menu"
          onExpand={() => setMenuCollapsed(false)}
        />
      ) : (
        <>
          {menuColumn}
          <ResizeHandle
            testId="resize-menu"
            onDelta={(dx) => setMenuWidth((w) => clamp(w + dx, 200, 520))}
          />
        </>
      )}

      {/* Column 2 — Results + concept grid (always visible, flexes to fill).
          Sits directly beside the Source PDF so a value and the document page
          it came from are adjacent — no center-then-far-left eye travel. */}
      <main style={styles.resultsCol}>
        <section style={styles.reviewHeader}>
          <div style={styles.titleRow}>
            <div>
              <h1 style={styles.pageTitle}>{TERMS.reviewWorkspaceTitle}</h1>
            </div>
            <div style={styles.actionRow}>
              {actionableChecks.length > 0 && (
                <div style={styles.issueNav} aria-label="Issue navigation">
                  <button
                    type="button"
                    className={uiClass.btnSecondary}
                    style={{ ...ui.buttonSecondary, ...ui.buttonSm }}
                    onClick={() => moveAttention(-1)}
                    aria-label="Previous issue"
                  >
                    ←
                  </button>
                  <span style={ui.metadata}>{Math.min(attentionIndex + 1, actionableChecks.length)} / {actionableChecks.length}</span>
                  <button
                    type="button"
                    className={uiClass.btnSecondary}
                    style={{ ...ui.buttonSecondary, ...ui.buttonSm }}
                    onClick={() => moveAttention(1)}
                    aria-label="Next issue"
                  >
                    →
                  </button>
                </div>
              )}
              {recheck.summary && (
                <span data-testid="recheck-summary" style={styles.recheckSummary} role="status" aria-live="polite">
                  {recheck.summary}
                </span>
              )}
              <button
                data-testid="recheck-btn"
                className={uiClass.btnSecondary}
                onClick={onRecheck}
                disabled={recheck.running}
                title="Rerun consistency checks using the current saved figures"
                style={{
                  ...ui.buttonSecondary,
                  cursor: recheck.running ? "default" : "pointer",
                  opacity: recheck.running ? 0.7 : 1,
                }}
              >
                {recheck.running ? TERMS.validatingFigures : TERMS.validateFigures}
              </button>
              {/* No Download button here — the run header directly above this
                  workspace already carries the one primary "Download filled
                  Excel" action. Two identical primary CTAs on one screen made
                  users ask whether they differ (run-168 design critique). */}
            </div>
          </div>

          {/* Outcome strip — what a reviewer cares about (are the checks
              passing, did the notes land, what have I changed), not row counts
              (review-workspace Phase 3). Metrics only appear when their source
              exists, so a face-only run drops "Notes placed" and a run with no
              cross-checks drops "Checks passing". */}
          <div style={styles.summaryStrip} aria-label="Review summary">
            {checksGraded > 0 && (
              <ReviewMetric
                label="Checks passing"
                value={`${checksPassing}/${checksGraded}`}
                tone={checksPassing === checksGraded ? "success" : "warning"}
                caption={
                  advisoryCount > 0
                    ? `${advisoryCount} advisory note${advisoryCount === 1 ? "" : "s"} to review (not counted above)`
                    : undefined
                }
              />
            )}
            {notesCoverage && notesCoverage.total > 0 && (
              <ReviewMetric
                label="Notes placed"
                value={`${notesCoverage.placed}/${notesCoverage.total}`}
                tone={
                  notesCoverage.placed === notesCoverage.total
                    ? "success"
                    : "warning"
                }
              />
            )}
            {/* Neutral, not amber: an edit count is information, not a
                problem. Amber is reserved for the checks metric so its signal
                stays meaningful. The explanation lives IN the card (it used
                to be repeated in a separate banner directly below). */}
            <ReviewMetric
              label="Your edits"
              value={String(editedCount)}
              tone="neutral"
              caption={
                editedCount > 0
                  ? `${editedCount} value${editedCount === 1 ? "" : "s"} edited since the run finished — included in the downloaded Excel; re-running an agent overwrites them.`
                  : undefined
              }
              captionTestId="edited-values-banner"
            />
          </div>
        </section>

        {loadError && (
          <div style={styles.errorBanner}>
            Failed to load concepts: {loadError}
          </div>
        )}

        {/* Failing cross-checks now surface in the left-column "Needs
            attention" queue (click-to-cell); the dedicated Cross-checks tab
            keeps the full expected/actual detail. */}

        {/* Both toolbar controls only apply to figure sheets, so on a
            notes sheet the whole card is skipped — rendering the shell
            with its children hidden painted an empty white box between
            the outcome strip and the notes editor (run-168 QA finding). */}
        {!notesActive && (
          <section style={styles.toolbar} aria-label="Review controls">
            {isGroupRun && (
              <div style={styles.controlGroup}>
                <span style={ui.fieldLabel}>Entity</span>
                <SegmentedControl
                  testId="entity-scope-toggle"
                  values={["Company", "Group"] as const}
                  activeValue={activeScope}
                  onChange={setActiveScope}
                  buttonTestId={(scope) => `scope-btn-${scope}`}
                />
              </div>
            )}

            <div style={styles.searchGroup}>
              <label htmlFor="concept-search" style={ui.fieldLabel}>
                Search
              </label>
              <input
                id="concept-search"
                data-testid="concept-search"
                type="search"
                placeholder="Search all sheets"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                style={{ ...ui.input, width: "100%" }}
              />
            </div>
            <div style={styles.rowFilters} role="group" aria-label="Figure rows">
              {ROW_FILTERS.map((option) => (
                <button
                  key={option.value}
                  type="button"
                  data-testid={option.value === "no_source" ? "filter-no-source" : `filter-${option.value}`}
                  aria-pressed={rowFilter === option.value}
                  onClick={() => setRowFilter(option.value)}
                  className={rowFilter === option.value ? uiClass.btnSubtle : uiClass.btnSecondary}
                  style={{
                    ...(rowFilter === option.value ? ui.buttonSubtle : ui.buttonSecondary),
                    ...ui.buttonSm,
                  }}
                >
                  {option.label}{option.value === "no_source" && noSourceCount > 0 ? ` (${noSourceCount})` : ""}
                </button>
              ))}
            </div>
            <span style={styles.srOnly} role="status" aria-live="polite">
              {filtered.length} figure row{filtered.length === 1 ? "" : "s"} shown
            </span>
          </section>
        )}

        {notesActive ? (
          // Notes edit in place, next to the Source PDF — focusing a cell jumps
          // the PDF pane to that note's source pages (review-workspace Phase 1).
          <div data-testid="review-notes-panel">
            <NotesReviewTab
              runId={runId}
              focusSheet={activeNotesSheet}
              focusCell={notesFocusCell}
              onActiveCellPages={handleNotesCellPages}
              onRegenerate={onRegenerateNotes}
            />
          </div>
        ) : filtered.length > 0 && filtered.every((r) => r.shape === "matrix") ? (
          // Only render the matrix grid when EVERY visible row is matrix.
          // A cross-template search can match SOCIE + linear concepts at
          // once; `.some` would shove the linear rows into the grid with
          // no cells. `.every` keeps a mixed result list on the linear
          // tree (matrix rows simply show blank values there).
          <ConceptMatrixGrid
            rows={filtered}
            onEditValue={onEditValue}
            editStatus={editStatus}
            selectedUuid={selectedConceptUuid}
            onSelectRow={setSelectedConceptUuid}
            activeScope={activeScope}
            showPeriods={hasPyFacts}
            scrollOnSelectRef={scrollSelectionRef}
            cyLabel={reportingCy ? `CY (${reportingCy})` : "CY"}
            pyLabel={reportingPy ? `PY (${reportingPy})` : "PY"}
          />
        ) : (
          <ConceptTree
            rows={filtered}
            onEditValue={onEditValue}
            editStatus={editStatus}
            selectedUuid={selectedConceptUuid}
            onSelectRow={setSelectedConceptUuid}
            activeScope={activeScope}
            showPeriods={hasPyFacts}
            scrollOnSelectRef={scrollSelectionRef}
            cyLabel={reportingCy ? `CY (${reportingCy})` : "CY"}
            pyLabel={reportingPy ? `PY (${reportingPy})` : "PY"}
          />
        )}
      </main>

      {/* Column 3 — Source PDF, docked on the right so the value grid and its
          source page sit side by side. The resize handle is on the PDF's LEFT
          edge now, so a rightward drag shrinks it — delta sign is flipped
          relative to the Menu handle on the far left. */}
      {pdfCollapsed ? (
        <CollapsedRail
          label="Source PDF"
          testId="pdf"
          onExpand={() => setPdfCollapsed(false)}
        />
      ) : (
        <>
          <ResizeHandle
            testId="resize-pdf"
            onDelta={(dx) => setPdfWidth((w) => clamp(w - dx, 260, 720))}
          />
          {pdfColumn}
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Layout primitives for the 3-column workspace — a generic collapsible panel,
// per-column hide header + collapsed rail, and a drag-to-resize handle. Inline
// styles only (gotcha #7).
// ---------------------------------------------------------------------------

function clamp(v: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, v));
}

function CollapsiblePanel({
  title,
  testId,
  defaultOpen = true,
  children,
}: {
  title: string;
  testId?: string;
  defaultOpen?: boolean;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <section data-testid={testId} style={styles.panelCard}>
      <button
        type="button"
        data-testid={testId ? `${testId}-toggle` : undefined}
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        style={styles.panelHeader}
      >
        <span style={styles.panelHeaderTitle}>{title}</span>
        <span
          style={{
            ...styles.panelChevron,
            transform: open ? "none" : "rotate(-90deg)",
          }}
        >
          ▾
        </span>
      </button>
      {open && <div style={styles.panelBody}>{children}</div>}
    </section>
  );
}

function ColumnHeader({
  title,
  testId,
  onHide,
}: {
  title: string;
  testId: string;
  onHide: () => void;
}) {
  return (
    <div style={styles.columnHeader}>
      <span style={styles.columnHeaderTitle}>{title}</span>
      <button
        type="button"
        data-testid={`col-hide-${testId}`}
        onClick={onHide}
        style={styles.columnHideBtn}
        title={`Hide ${title} panel`}
        aria-label={`Hide ${title} panel`}
      >
        « Hide
      </button>
    </div>
  );
}

function CollapsedRail({
  label,
  testId,
  onExpand,
}: {
  label: string;
  testId: string;
  onExpand: () => void;
}) {
  // A thin vertical button is easy to miss, so the rail carries an explicit
  // expand chevron at top + bottom and lifts to the accent colour on hover so
  // it clearly reads as "click to reveal" rather than a passive divider.
  const [hover, setHover] = useState(false);
  return (
    <button
      type="button"
      data-testid={`col-show-${testId}`}
      onClick={onExpand}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        ...styles.collapsedRail,
        background: hover ? pwc.orange50 : pwc.white,
        borderColor: hover ? pwc.orange400 : pwc.grey200,
        color: hover ? pwc.orange700 : pwc.grey700,
      }}
      title={`Show ${label} panel`}
      aria-label={`Show ${label} panel`}
    >
      <span aria-hidden="true" style={styles.collapsedRailChevron}>
        »
      </span>
      <span style={styles.collapsedRailLabel}>{label}</span>
      <span aria-hidden="true" style={styles.collapsedRailChevron}>
        »
      </span>
    </button>
  );
}

function ResizeHandle({
  testId,
  onDelta,
}: {
  testId: string;
  onDelta: (dx: number) => void;
}) {
  const [active, setActive] = useState(false);
  // Hold the live drag listeners so an unmount mid-drag can tear them down —
  // otherwise the window listeners (and the body user-select lock) leak if the
  // component disappears between mousedown and mouseup.
  const cleanupRef = useRef<(() => void) | null>(null);
  useEffect(() => () => cleanupRef.current?.(), []);

  const onMouseDown = (e: React.MouseEvent) => {
    e.preventDefault();
    let lastX = e.clientX;
    setActive(true);
    const move = (ev: MouseEvent) => {
      const dx = ev.clientX - lastX;
      lastX = ev.clientX;
      onDelta(dx);
    };
    const teardown = () => {
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", up);
      document.body.style.userSelect = "";
      cleanupRef.current = null;
    };
    const up = () => {
      teardown();
      setActive(false);
    };
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
    document.body.style.userSelect = "none";
    // The unmount effect calls this; it skips setActive (the component is gone).
    cleanupRef.current = teardown;
  };
  return (
    <div
      className="review-resize-handle"
      role="separator"
      aria-orientation="vertical"
      data-testid={testId}
      onMouseDown={onMouseDown}
      style={{
        ...styles.resizeHandle,
        background: active ? pwc.orange400 : pwc.grey200,
      }}
    />
  );
}

// ---------------------------------------------------------------------------
// ConceptTree — flat list with indent-by-parent-chain depth for clarity.
// Recursive nesting would buy nothing here; the rows are display-order
// already.
// ---------------------------------------------------------------------------

export type EditValueFn = (
  uuid: string,
  value: number | null,
  opts?: { keepalive?: boolean; period?: Period; entity_scope?: "Company" | "Group" }
) => Promise<void>;

function ConceptTree({
  rows,
  onEditValue,
  editStatus,
  selectedUuid,
  onSelectRow,
  activeScope,
  showPeriods,
  scrollOnSelectRef,
  cyLabel = "CY",
  pyLabel = "PY",
}: {
  rows: ConceptRow[];
  onEditValue: EditValueFn;
  editStatus: Record<string, "saving" | "saved" | "error">;
  selectedUuid: string | null;
  onSelectRow: (uuid: string) => void;
  activeScope: "Company" | "Group";
  showPeriods: boolean;
  /** May the selected row scroll itself into view? False for the initial
   *  auto-selection (see the owner ref in ConceptsPage). */
  scrollOnSelectRef?: React.MutableRefObject<boolean>;
  // Year-labelled column headers ("CY (FY2021)"); default to plain codes.
  cyLabel?: string;
  pyLabel?: string;
}) {
  const depthByUuid = new Map<string, number>();
  for (const r of rows) {
    if (r.parent_uuid && depthByUuid.has(r.parent_uuid)) {
      depthByUuid.set(r.concept_uuid, depthByUuid.get(r.parent_uuid)! + 1);
    } else {
      depthByUuid.set(r.concept_uuid, 0);
    }
  }
  // Collapse consecutive ABSTRACT headers that repeat the same label — the
  // taxonomy nests "Statement of cash flows" three deep, which rendered as
  // three identical section bands in a row (E7). A data row (or a differently
  // labelled header) breaks the run, so nothing real is hidden.
  const visibleRows: ConceptRow[] = [];
  let prevAbstractLabel: string | null = null;
  for (const r of rows) {
    const label = (r.display_label || r.canonical_label || "").trim();
    if (r.kind === "ABSTRACT") {
      if (prevAbstractLabel !== null && prevAbstractLabel === label) continue;
      prevAbstractLabel = label;
    } else {
      prevAbstractLabel = null;
    }
    visibleRows.push(r);
  }
  return (
    <div
      role="tree"
      style={styles.tableShell}
    >
      <div
        role="row"
        style={{ ...styles.treeHeaderRow, gridTemplateColumns: treeColumns(showPeriods) }}
      >
        {/* "Line item" (accountant vocabulary), not the internal "Concept"
            codename — plain-language rule (CLAUDE.md "talk like a product
            person"). Numeric column headers right-align over their figures. */}
        <div style={styles.headerCell}>Line item</div>
        <div style={styles.headerCellNumeric}>{showPeriods ? cyLabel : "Value"}</div>
        {showPeriods && <div style={styles.headerCellNumeric}>{pyLabel}</div>}
        <div style={styles.headerCell}>State</div>
        <div style={styles.headerCell}>Source</div>
      </div>
      {visibleRows.map((r) => (
        <ConceptRowView
          // Composite key: alias rows share concept_uuid with their
          // primary, so a uuid-only key would collide and React would
          // render one view-row instead of two. (sheet, row, col)
          // disambiguates without relying on array index.
          key={`${r.concept_uuid}@${r.render_sheet}:${r.render_row}:${r.render_col}`}
          row={r}
          depth={depthByUuid.get(r.concept_uuid) || 0}
          onEditValue={onEditValue}
          editStatus={editStatus}
          selected={selectedUuid === r.concept_uuid}
          onSelectRow={onSelectRow}
          activeScope={activeScope}
          showPeriods={showPeriods}
          scrollOnSelectRef={scrollOnSelectRef}
        />
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// ConceptMatrixGrid — SOCIE view.  Concept identity is (movement-row,
// equity-component-column), so we render a 2-D grid: one row per movement
// label, one column per `matrix_col`.  ABSTRACT rows (block sub-headers)
// span the full width as section dividers.  Data-entry component cells are
// editable (Phase 2.1 / peer-review F1); formula totals stay read-only.
// ---------------------------------------------------------------------------

type MatrixCell = {
  uuid: string;
  values: Record<Period, number | null>;
  editable: boolean;
  mandatory: boolean;
};

function excelColumnIndex(col: string): number {
  let index = 0;
  for (const char of col.toUpperCase()) {
    const code = char.charCodeAt(0);
    if (code < 65 || code > 90) return Number.MAX_SAFE_INTEGER;
    index = index * 26 + (code - 64);
  }
  return index;
}

function ConceptMatrixGrid({
  rows,
  onEditValue,
  editStatus,
  selectedUuid,
  onSelectRow,
  activeScope,
  showPeriods,
  scrollOnSelectRef,
  cyLabel = "CY",
  pyLabel = "PY",
}: {
  rows: ConceptRow[];
  onEditValue: EditValueFn;
  editStatus: Record<string, "saving" | "saved" | "error">;
  selectedUuid: string | null;
  onSelectRow: (uuid: string) => void;
  activeScope: "Company" | "Group";
  showPeriods: boolean;
  /** May the selected row scroll itself into view? False for the initial
   *  auto-selection (see the owner ref in ConceptsPage). */
  scrollOnSelectRef?: React.MutableRefObject<boolean>;
  // Year-labelled period headers ("CY (FY2021)"); default to plain codes.
  cyLabel?: string;
  pyLabel?: string;
}) {
  // Distinct component columns, in spreadsheet order (B, C, …).
  const cols: string[] = [];
  const colLabels = new Map<string, string>();
  for (const r of rows) {
    if (r.matrix_col && !cols.includes(r.matrix_col)) cols.push(r.matrix_col);
    if (r.matrix_col && r.matrix_col_label) {
      colLabels.set(r.matrix_col, r.matrix_col_label);
    }
  }
  cols.sort((a, b) => excelColumnIndex(a) - excelColumnIndex(b));

  // Movement rows, in render order. Each cell keeps its concept_uuid +
  // editable flag so an edit can be routed through the facts PATCH endpoint.
  type GridRow = {
    render_row: number;
    label: string;
    isAbstract: boolean;
    cells: Map<string, MatrixCell>;
  };
  const byRow = new Map<number, GridRow>();
  const order: number[] = [];
  for (const r of rows) {
    let g = byRow.get(r.render_row);
    if (!g) {
      g = {
        render_row: r.render_row,
        label: r.display_label || r.canonical_label,
        isAbstract: r.kind === "ABSTRACT",
        cells: new Map(),
      };
      byRow.set(r.render_row, g);
      order.push(r.render_row);
    }
    if (r.matrix_col) {
      g.cells.set(r.matrix_col, {
        uuid: r.concept_uuid,
        values: {
          CY: periodValue(r, activeScope, "CY"),
          PY: periodValue(r, activeScope, "PY"),
        },
        editable: r.editable === true,
        // In SOCIE, leading "*" marks the movement row, not every component
        // intersection. Highlighting every blank component as mandatory turns
        // the matrix into a wall of orange even when blanks are legitimate.
        mandatory: false,
      });
    }
  }

  // M2 — scroll the row holding the selected cell into view when selection is
  // driven from outside the grid. We key element refs by render_row and locate
  // the owning row by scanning cells for the selected uuid.
  const rowRefs = useRef(new Map<number, HTMLDivElement | null>());
  useEffect(() => {
    if (!selectedUuid) return;
    // Automatic fallback selections must not move the page (live-QA fix);
    // only intentional jumps scroll.
    if (scrollOnSelectRef && !scrollOnSelectRef.current) return;
    for (const [rn, g] of byRow) {
      for (const cell of g.cells.values()) {
        if (cell.uuid === selectedUuid) {
          rowRefs.current.get(rn)?.scrollIntoView?.({ block: "nearest" });
          return;
        }
      }
    }
  }, [selectedUuid, byRow, scrollOnSelectRef]);

  // Wider columns so an input fits without clipping accountant figures.
  const visiblePeriods: Period[] = showPeriods ? ["CY", "PY"] : ["CY"];
  const valueColumns = cols.flatMap((c) =>
    visiblePeriods.map((period) => ({ col: c, period }))
  );
  const periodColWidth = 136;
  const gridCols = `minmax(240px, 300px) repeat(${valueColumns.length}, ${periodColWidth}px)`;

  return (
    <div
      data-testid="concept-matrix-grid"
      role="table"
      style={styles.matrixShell}
    >
      <div
        role="rowgroup"
        style={{
          display: "grid",
          gridTemplateColumns: gridCols,
          background: pwc.grey100,
          fontWeight: 600,
          fontSize: 12,
          borderBottom: `1px solid ${pwc.grey200}`,
        }}
      >
        <div
          style={{
            ...styles.matrixHeaderMovement,
            gridRow: showPeriods ? "1 / span 2" : undefined,
          }}
        >
          Movement
        </div>
        {cols.map((col, idx) => {
          const label = colLabels.get(col) || col;
          const start = 2 + idx * visiblePeriods.length;
          return (
            <div
              key={col}
              style={{
                ...styles.matrixComponentHeader,
                gridColumn: showPeriods
                  ? `${start} / span ${visiblePeriods.length}`
                  : undefined,
              }}
              title={`Column ${col}: ${label}`}
            >
              {label}
            </div>
          );
        })}
        {showPeriods && valueColumns.map(({ col, period }) => (
          <div
            key={`${col}-${period}`}
            style={styles.matrixPeriodHeader}
            title={`${col} ${period}`}
          >
            {period === "CY" ? cyLabel : pyLabel}
          </div>
        ))}
      </div>
      {order.map((rn) => {
        const g = byRow.get(rn)!;
        if (g.isAbstract) {
          return (
            <div
              key={rn}
              role="row"
              style={{
                padding: `${pwc.space.sm}px ${pwc.space.md}px`,
                background: pwc.grey50,
                fontFamily: pwc.fontBody,
                fontSize: 13,
                fontWeight: 600,
                borderBottom: `1px solid ${pwc.grey100}`,
              }}
            >
              {g.label}
            </div>
          );
        }
        return (
          <div
            key={rn}
            ref={(el) => rowRefs.current.set(rn, el)}
            role="row"
            style={{
              display: "grid",
              gridTemplateColumns: gridCols,
              borderBottom: `1px solid ${pwc.grey100}`,
              fontFamily: pwc.fontBody,
              fontSize: 13,
              alignItems: "center",
            }}
          >
            <div style={styles.matrixMovementCell}>
              {g.label}
            </div>
            {valueColumns.map(({ col, period }) => {
              const cell = g.cells.get(col);
              const selected = cell?.uuid === selectedUuid;
              const highlightEmpty =
                cell?.mandatory === true && isBlankValue(cell.values[period]);
              const testId = showPeriods
                ? `matrix-cell-${rn}-${col}-${period}`
                : `matrix-cell-${rn}-${col}`;
              return (
                <div
                  key={`${col}-${period}`}
                  data-testid={testId}
                  onClick={() => cell && onSelectRow(cell.uuid)}
                  style={{
                    padding: `${pwc.space.xs}px ${pwc.space.sm}px`,
                    textAlign: "right",
                    minWidth: 0,
                    background: selected ? pwc.orange50 : "transparent",
                    boxShadow: selected
                      ? `inset 0 -2px 0 ${pwc.orange400}`
                      : undefined,
                    cursor: cell ? "pointer" : "default",
                  }}
                >
                  {cell && cell.editable ? (
                    <EditableValueCell
                      uuid={cell.uuid}
                      value={cell.values[period]}
                      onEditValue={onEditValue}
                      status={editStatus[valueEditKey(cell.uuid, period)]}
                      period={showPeriods ? period : undefined}
                      scope={activeScope}
                      highlight={highlightEmpty}
                      compact
                    />
                  ) : (
                    <ReadOnlyValue
                      value={cell?.values[period]}
                      highlight={highlightEmpty}
                    />
                  )}
                </div>
              );
            })}
          </div>
        );
      })}
    </div>
  );
}

function ConceptRowView({
  row,
  depth,
  onEditValue,
  editStatus,
  selected,
  onSelectRow,
  activeScope,
  showPeriods,
  scrollOnSelectRef,
}: {
  row: ConceptRow;
  depth: number;
  onEditValue: EditValueFn;
  editStatus: Record<string, "saving" | "saved" | "error">;
  selected: boolean;
  onSelectRow: (uuid: string) => void;
  activeScope: "Company" | "Group";
  showPeriods: boolean;
  /** May the selected row scroll itself into view? False for the initial
   *  auto-selection (see the owner ref in ConceptsPage). */
  scrollOnSelectRef?: React.MutableRefObject<boolean>;
}) {
  // M2 — when selection is driven from outside the grid (a reconciliation
  // conflict), bring the row into view. `scrollIntoView` is guarded with `?.`
  // because jsdom doesn't implement it (the test env would otherwise throw).
  // The initial AUTO-selection must not move the page (live-QA fix) — only
  // intentional jumps carry scrollOnSelectRef.current === true.
  const rowRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!selected) return;
    if (scrollOnSelectRef && !scrollOnSelectRef.current) return;
    rowRef.current?.scrollIntoView?.({ block: "nearest" });
  }, [selected, scrollOnSelectRef]);

  // Phase 5.3 — labels are READ-ONLY in the per-run review; renaming lives
  // on the global Template settings page so there's one coherent place to
  // edit labels and one (here) to edit values. The label still shows the
  // user's global display_label override when set.
  const label = row.display_label || row.canonical_label;
  const isAbstract = row.kind === "ABSTRACT";
  const isComputed = row.kind === "COMPUTED";
  const isMandatory = isMandatoryConcept(row);
  const isAlias = row.is_alias === true;
  // Phase 2.1 — only genuine LEAF rows are editable. COMPUTED totals are
  // owned by the cascade; ABSTRACT rows are section headers (gotcha #17).
  // Alias view-rows are NEVER editable (the workbook formula owns the
  // value at the alias coord); the backend already drops `editable` on
  // them, this is defence-in-depth in case the backend ever forgets.
  const isEditable = row.kind === "LEAF" && !isAlias;
  const cyValue = periodValue(row, activeScope, "CY");
  const pyValue = periodValue(row, activeScope, "PY");
  const cyIncompleteMandatory = isMandatory && isBlankValue(cyValue);
  const pyIncompleteMandatory = isMandatory && isBlankValue(pyValue);
  const cyStatus = editStatus[valueEditKey(row.concept_uuid, "CY")];
  const pyStatus = editStatus[valueEditKey(row.concept_uuid, "PY")];
  const statusLabel =
    cyStatus === "saving" || pyStatus === "saving"
      ? "Saving"
      : cyStatus === "saved" || pyStatus === "saved"
      ? "Saved"
      : cyStatus === "error" || pyStatus === "error"
      ? "Save failed"
      : isComputed
      ? cyValue != null || (showPeriods && pyValue != null)
        ? "Calculated"
        : ""
      : displayValueStatus(row.value_status);

  return (
    <div
      ref={rowRef}
      data-testid={`concept-row-${row.concept_uuid}`}
      data-kind={row.kind}
      // Section headers aren't selectable — they carry no value, so clicking
      // one used to select a row the PDF pane / details panel could do
      // nothing with. Data rows keep the click-to-select behaviour.
      onClick={isAbstract ? undefined : () => onSelectRow(row.concept_uuid)}
      style={{
        display: "grid",
        gridTemplateColumns: isAbstract
          ? "minmax(0, 1fr)"
          : treeColumns(showPeriods),
        gap: pwc.space.lg,
        minWidth: 760,
        // Section headers are visually distinct from data rows: tighter,
        // smaller, semibold caption on a grey band — so a run of same-named
        // taxonomy headers ("Statement of cash flows" nested three deep)
        // reads as structure, not as three broken data rows (run-168
        // design critique).
        padding: isAbstract
          ? `${pwc.space.sm}px ${pwc.space.xl}px`
          : `${pwc.space.lg}px ${pwc.space.xl}px`,
        background: isAbstract
          ? pwc.grey100
          : selected
          ? pwc.orange50
          : pwc.white,
        borderBottom: `1px solid ${pwc.grey100}`,
        fontFamily: pwc.fontBody,
        fontSize: isAbstract ? 12 : 15,
        fontWeight: isAbstract ? pwc.weight.semibold : pwc.weight.regular,
        letterSpacing: isAbstract ? 0.4 : undefined,
        textTransform: isAbstract ? ("uppercase" as const) : undefined,
        color: isAbstract ? pwc.grey700 : isComputed ? pwc.grey700 : pwc.grey900,
        cursor: isAbstract ? "default" : "pointer",
        alignItems: "center",
      }}
    >
      <div
        title={
          isAlias
            ? `canonical: ${row.canonical_label} — linked to value from another sheet`
            : `canonical: ${row.canonical_label}`
        }
        style={{
          paddingLeft: depth * 14,
          display: "flex",
          flexDirection: "column",
          gap: pwc.space.xs,
          lineHeight: 1.55,
        }}
      >
        <span
          data-testid={`label-${row.concept_uuid}`}
          style={
            isAlias
              ? { fontStyle: "italic", color: pwc.grey700 }
              : undefined
          }
        >
          {label}
          {isAlias && (
            <span
              data-testid={`alias-marker-${row.concept_uuid}`}
              style={{
                marginLeft: pwc.space.sm,
                fontSize: 12,
                fontStyle: "italic",
                color: pwc.grey700,
                fontWeight: pwc.weight.regular,
              }}
            >
              (linked)
            </span>
          )}
        </span>
        {/* Explain the orange highlight instead of leaving a bare empty
            input: a "*" (mandatory) row with no extracted value tells the
            reviewer WHY it's flagged and what to do (run-168 design
            critique — "highlighting should carry a reason"). */}
        {isEditable &&
          (cyIncompleteMandatory || (showPeriods && pyIncompleteMandatory)) && (
            <span
              data-testid={`required-chip-${row.concept_uuid}`}
              style={styles.requiredChip}
              title="This line is mandatory in the filing template but no value was extracted. Enter one, or leave it blank if the statement genuinely doesn't disclose it."
            >
              Required — no value extracted
            </span>
          )}
        {/* No-source badge (UX-QA #6): this value can't be checked against the
            PDF because no source page was recorded. Flag it for extra scrutiny
            rather than letting it look like any other verified row. */}
        {rowLacksSource(row) && (
          <span
            data-testid={`no-source-chip-${row.concept_uuid}`}
            style={styles.noSourceChip}
            title="No source page was recorded for this value, so it can't be checked against the PDF automatically. Verify it manually before filing."
          >
            No source page — verify manually
          </span>
        )}
      </div>
      {!isAbstract && (
        <>
          {/* value column — editable for LEAF rows, read-only otherwise */}
          <div style={styles.valueCell}>
            {isComputed ? (
              <ReadOnlyValue
                value={cyValue}
                highlight={cyIncompleteMandatory}
                testId={
                  showPeriods
                    ? `readonly-value-${row.concept_uuid}-CY`
                    : `readonly-value-${row.concept_uuid}`
                }
              />
            ) : isEditable ? (
              <EditableValueCell
                uuid={row.concept_uuid}
                value={cyValue}
                onEditValue={onEditValue}
                status={cyStatus}
                period={showPeriods ? "CY" : undefined}
                scope={activeScope}
                highlight={cyIncompleteMandatory}
              />
            ) : (
              <ReadOnlyValue
                value={cyValue}
                highlight={cyIncompleteMandatory}
                testId={
                  showPeriods
                    ? `readonly-value-${row.concept_uuid}-CY`
                    : `readonly-value-${row.concept_uuid}`
                }
              />
            )}
          </div>
          {showPeriods && (
            <div style={styles.valueCell}>
              {isComputed ? (
                <ReadOnlyValue
                  value={pyValue}
                  highlight={pyIncompleteMandatory}
                  testId={`readonly-value-${row.concept_uuid}-PY`}
                />
              ) : isEditable ? (
                <EditableValueCell
                  uuid={row.concept_uuid}
                  value={pyValue}
                  onEditValue={onEditValue}
                  status={pyStatus}
                  period="PY"
                  scope={activeScope}
                  highlight={pyIncompleteMandatory}
                />
              ) : (
                <ReadOnlyValue
                  value={pyValue}
                  highlight={pyIncompleteMandatory}
                  testId={`readonly-value-${row.concept_uuid}-PY`}
                />
              )}
            </div>
          )}
          <div style={styles.stateCell}>
            {statusLabel ? (
              <StatusBadge
                label={statusLabel}
                tone={
                  cyStatus === "error" || pyStatus === "error"
                    ? "error"
                    : isComputed
                    ? "neutral"
                    : row.value_status === "user_override" ||
                      cyStatus === "saved" ||
                      pyStatus === "saved"
                    ? "accent"
                    : "neutral"
                }
              />
            ) : null}
          </div>
          <div style={styles.sourceCell}>{displayConceptSource(row)}</div>
        </>
      )}
    </div>
  );
}

function ReadOnlyValue({
  value,
  highlight = false,
  testId,
}: {
  value: number | null | undefined;
  highlight?: boolean;
  testId?: string;
}) {
  if (value == null) {
    return (
      <span
        aria-hidden="true"
        data-testid={testId}
        style={
          highlight ? styles.mandatoryEmptyValueBox : styles.emptyValueBox
        }
      />
    );
  }
  // Render inside a box matching the editable input's footprint so computed
  // totals line up with the data-entry fields above them (a faint grey fill +
  // no caret signals "read-only, owned by the cascade").
  return (
    <span data-testid={testId} style={styles.readonlyValueBox}>
      {formatAccounting(value)}
    </span>
  );
}

function ReviewMetric({
  label,
  value,
  tone = "neutral",
  caption,
  captionTestId,
}: {
  label: string;
  value: string;
  tone?: "neutral" | "accent" | "warning" | "success";
  /** Optional one-line explanation rendered inside the card — used by the
   *  edits metric so the same fact isn't told twice (card + banner). */
  caption?: string;
  captionTestId?: string;
}) {
  // Status is carried by a left rule + the label's dark text on a NEUTRAL
  // surface — matching the app's alerts (ui.alertWarning) and error banner,
  // not a full coloured fill. `rule` null keeps the plain all-round border for
  // the neutral tone. (Design language: status colour is an accent, never a
  // brand-competing background wash.)
  const palette =
    tone === "accent"
      ? { rule: pwc.orange500, label: pwc.orange700 }
      : tone === "warning"
      ? { rule: pwc.warning, label: pwc.warningText }
      : tone === "success"
      ? { rule: pwc.success, label: pwc.successText }
      : { rule: null as string | null, label: pwc.grey700 };
  return (
    <div
      style={{
        ...styles.metric,
        ...(palette.rule ? { borderLeft: `3px solid ${palette.rule}` } : {}),
      }}
    >
      <span style={styles.metricValue}>{value}</span>
      <span style={{ ...styles.metricLabel, color: palette.label }}>{label}</span>
      {caption && (
        <span data-testid={captionTestId} style={styles.metricCaption}>
          {caption}
        </span>
      )}
    </div>
  );
}

function SegmentedControl<T extends string>({
  testId,
  values,
  activeValue,
  onChange,
  buttonTestId,
}: {
  testId: string;
  values: readonly T[];
  activeValue: T;
  onChange: (value: T) => void;
  buttonTestId: (value: T) => string;
}) {
  return (
    <div data-testid={testId} role="tablist" style={styles.segmented}>
      {values.map((value) => {
        const active = value === activeValue;
        return (
          <button
            key={value}
            type="button"
            role="tab"
            aria-selected={active}
            data-testid={buttonTestId(value)}
            onClick={() => onChange(value)}
            style={{
              ...styles.segmentedButton,
              background: active ? pwc.grey900 : pwc.white,
              color: active ? pwc.white : pwc.grey800,
            }}
          >
            {value}
          </button>
        );
      })}
    </div>
  );
}

function StatusBadge({
  label,
  tone,
}: {
  label: string;
  tone: "neutral" | "accent" | "error";
}) {
  // Monochrome value state (design-system Status / Financial rule F3):
  // neutral symbol + explicit text — never a coloured pill. error → action
  // required (!), accent (edited/saved) → ✓, neutral (computed etc.) → ◇.
  const symbol =
    tone === "error"
      ? STATUS_SYMBOLS.attention
      : tone === "accent"
      ? STATUS_SYMBOLS.success
      : STATUS_SYMBOLS.derived;
  return (
    <span style={{ ...ui.status, fontSize: 12 }}>
      <span aria-hidden="true" style={{ ...ui.statusSymbol, fontSize: 12 }}>{symbol}</span>
      {label.replace(/_/g, " ")}
    </span>
  );
}

// ---------------------------------------------------------------------------
// SheetNavigator — M3. An always-visible left rail listing each template (and
// the Notes editor) so reviewers switch sheets in one click instead of hunting
// through a dropdown. Each item carries an open-conflict count badge so the
// reviewer can triage where to look first.
// ---------------------------------------------------------------------------

function SheetNavigator({
  templates,
  sheetsByTemplate,
  activeTemplate,
  activeSheet,
  notesKey,
  notesSheets,
  activeNotesSheet,
  conflictCounts,
  onSelectTemplate,
  onSelectSheet,
  onSelectNotesSheet,
}: {
  templates: string[];
  sheetsByTemplate: Record<string, string[]>;
  activeTemplate: string | null;
  activeSheet: string | null;
  notesKey: string;
  notesSheets: string[];
  activeNotesSheet: string | null;
  conflictCounts: Record<string, number>;
  onSelectTemplate: (templateId: string) => void;
  onSelectSheet: (templateId: string, sheet: string) => void;
  onSelectNotesSheet: (sheet: string) => void;
}) {
  const notesActive = activeTemplate === notesKey;
  // Mirror the face-statement pattern: when Notes is the active surface and the
  // run has notes sheets, expand them as sub-tabs so the reviewer jumps to one
  // note directly instead of scrolling the single stacked editor.
  const showNotesSubSheets = notesActive && notesSheets.length > 0;
  return (
    <nav
      data-testid="sheet-navigator"
      aria-label="Sheets"
      style={styles.sideNav}
    >
      {templates.map((tid) => {
        const active = tid === activeTemplate;
        const count = conflictCounts[tid] || 0;
        const sheets = sheetsByTemplate[tid] || [];
        // A statement workbook is split across several sub-sheets (face +
        // breakdowns). Expand them as nested entries under the active
        // template so the reviewer can jump to one sub-sheet directly. Only
        // worth showing when there's more than one sheet.
        const showSubSheets = active && sheets.length > 1;
        return (
          <div key={tid}>
            <button
              type="button"
              data-testid={`sheet-nav-${tid}`}
              aria-current={active && activeSheet == null ? "true" : undefined}
              onClick={() => onSelectTemplate(tid)}
              style={{
                ...styles.sideNavItem,
                // Highlight the template header only when it represents the
                // current view (all sheets); a selected sub-sheet dims it.
                background: active && activeSheet == null ? pwc.orange50 : pwc.white,
                color: active && activeSheet == null ? pwc.orange700 : pwc.grey800,
                borderColor: active ? pwc.orange400 : pwc.grey200,
                fontWeight: active && activeSheet == null ? 600 : 500,
              }}
            >
              <span style={{ ...styles.sideNavLabel, ...styles.sideNavLabelStack }} title={tid}>
                {templateDisplayName(tid)}
                {/* Plain-English gloss under the MBRS acronym — "SOFP" alone
                    assumes the reader speaks taxonomy shorthand. */}
                {templateSubtitle(tid) && (
                  <span style={styles.sideNavSubtitle}>{templateSubtitle(tid)}</span>
                )}
              </span>
              {count > 0 && (
                <span
                  data-testid={`sheet-nav-count-${tid}`}
                  style={styles.sideNavBadge}
                  title={`${count} open conflict${count === 1 ? "" : "s"}`}
                >
                  {count}
                </span>
              )}
            </button>
            {showSubSheets && (
              <div style={styles.sideNavSubGroup}>
                {sheets.map((sheet) => {
                  const sheetActive = active && activeSheet === sheet;
                  return (
                    <button
                      key={sheet}
                      type="button"
                      data-testid={`sheet-nav-sheet-${tid}-${sheet}`}
                      aria-current={sheetActive ? "true" : undefined}
                      onClick={() => onSelectSheet(tid, sheet)}
                      style={{
                        ...styles.sideNavSubItem,
                        background: sheetActive ? pwc.orange50 : pwc.white,
                        color: sheetActive ? pwc.orange700 : pwc.grey700,
                        borderColor: sheetActive ? pwc.orange400 : pwc.grey200,
                        fontWeight: sheetActive ? 600 : 400,
                      }}
                    >
                      <span style={styles.sideNavLabel}>{sheet}</span>
                    </button>
                  );
                })}
              </div>
            )}
          </div>
        );
      })}
      <div>
        <button
          type="button"
          data-testid={`sheet-nav-${notesKey}`}
          aria-current={notesActive && activeNotesSheet == null ? "true" : undefined}
          onClick={() => onSelectTemplate(notesKey)}
          style={{
            ...styles.sideNavItem,
            // Highlight the Notes header only when showing all notes; a
            // selected sub-tab dims it (mirrors the face-template behaviour).
            background: notesActive && activeNotesSheet == null ? pwc.orange50 : pwc.white,
            color: notesActive && activeNotesSheet == null ? pwc.orange700 : pwc.grey800,
            borderColor: notesActive ? pwc.orange400 : pwc.grey200,
            fontWeight: notesActive && activeNotesSheet == null ? 600 : 500,
          }}
        >
          <span style={styles.sideNavLabel}>Notes</span>
        </button>
        {showNotesSubSheets && (
          <div style={styles.sideNavSubGroup}>
            {notesSheets.map((sheet) => {
              const sheetActive = notesActive && activeNotesSheet === sheet;
              return (
                <button
                  key={sheet}
                  type="button"
                  data-testid={`sheet-nav-notes-${sheet}`}
                  aria-current={sheetActive ? "true" : undefined}
                  onClick={() => onSelectNotesSheet(sheet)}
                  style={{
                    ...styles.sideNavSubItem,
                    background: sheetActive ? pwc.orange50 : pwc.white,
                    color: sheetActive ? pwc.orange700 : pwc.grey700,
                    borderColor: sheetActive ? pwc.orange400 : pwc.grey200,
                    fontWeight: sheetActive ? 600 : 400,
                  }}
                >
                  <span style={styles.sideNavLabel} title={sheet}>
                    {notesSheetDisplayName(sheet)}
                  </span>
                </button>
              );
            })}
          </div>
        )}
      </div>
    </nav>
  );
}

function ConceptEvidenceBody({
  concept,
}: {
  concept: ConceptRow | null;
}) {
  return (
    <>
      {concept == null ? (
        <p style={styles.panelMuted}>Select a value row to view source context.</p>
      ) : (
        <div style={styles.evidenceStack}>
          <div>
            <div style={styles.evidenceLabel}>Line item</div>
            <div style={styles.evidenceText}>
              {concept.display_label || concept.canonical_label}
            </div>
          </div>
          <div style={styles.evidenceGrid}>
            <div>
              <div style={styles.evidenceLabel}>Template</div>
              <div style={styles.evidenceText}>{concept.template_id}</div>
            </div>
            <div>
              <div style={styles.evidenceLabel}>Cell</div>
              <div style={styles.evidenceText}>
                {concept.render_sheet}!{concept.render_col}
                {concept.render_row}
              </div>
            </div>
          </div>
          <div>
            <div style={styles.evidenceLabel}>Source</div>
            <div style={styles.evidenceText}>
              {displayConceptSource(concept) || "No source recorded"}
            </div>
          </div>
          <div>
            <div style={styles.evidenceLabel}>Evidence</div>
            <div style={styles.evidenceText}>
              {concept.evidence || "No evidence snippet recorded for this field."}
            </div>
          </div>
        </div>
      )}
    </>
  );
}

// ---------------------------------------------------------------------------
// EditableValueCell — a number input for a LEAF value with a debounced save.
// Saving / failed states are visible because they require attention; saved
// state is intentionally quiet so dense matrices don't collect status text.
// Mirrors the notes editor's save timing: debounce while typing, flush on
// blur, and flush any pending edit with `keepalive` on unmount so navigating
// away mid-edit never loses a save.
// ---------------------------------------------------------------------------

const SAVE_DEBOUNCE_MS = 800;

function EditableValueCell({
  uuid,
  value,
  onEditValue,
  status,
  period,
  scope,
  highlight = false,
  compact = false,
}: {
  uuid: string;
  value: number | null;
  onEditValue: EditValueFn;
  status?: "saving" | "saved" | "error";
  period?: Period;
  scope: "Company" | "Group";
  highlight?: boolean;
  compact?: boolean;
}) {
  const [draft, setDraft] = useState(value == null ? "" : String(value));
  const [focused, setFocused] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  // The latest unsaved value, kept in a ref so the unmount cleanup can flush
  // it without re-subscribing the effect on every keystroke.
  const pending = useRef<number | null | undefined>(undefined);
  // The scope the pending edit was made under — captured so an unmount flush
  // PATCHes under the right scope even if the user has since toggled.
  const pendingScope = useRef<"Company" | "Group">(scope);
  const onEditRef = useRef(onEditValue);
  onEditRef.current = onEditValue;

  // When the upstream value changes (scope/period toggle, or a cascade
  // recompute landed on this row) and the user isn't mid-edit, resync the
  // visible draft so we never show a stale figure.
  useEffect(() => {
    if (!focused) setDraft(value == null ? "" : String(value));
  }, [value, focused]);

  // Flush any pending edit on unmount (navigation / tab switch).
  useEffect(() => {
    return () => {
      if (timer.current) clearTimeout(timer.current);
      if (pending.current !== undefined) {
        void onEditRef.current(uuid, pending.current, {
          keepalive: true,
          period,
          entity_scope: pendingScope.current,
        });
      }
    };
  }, [uuid, period]);

  // Parse the field: empty → clear (null); a finite number → that number;
  // anything else is rejected (don't save garbage — Phase 4.1 hardens this).
  function parse(raw: string): number | null | undefined {
    // Empty clears; otherwise read thousands separators AND accounting
    // parentheses ("(1,234)" → -1234) so the at-rest accounting display
    // round-trips even if it reaches parse without being re-typed.
    if (raw.trim() === "") return null;
    const n = parseAccountingInput(raw);
    return Number.isFinite(n) ? n : undefined;
  }

  function schedule(raw: string) {
    const parsed = parse(raw);
    if (parsed === undefined) return; // invalid — wait for a valid value
    pending.current = parsed;
    pendingScope.current = scope;
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      timer.current = null;
      pending.current = undefined;
      void onEditRef.current(uuid, parsed, { period, entity_scope: scope });
    }, SAVE_DEBOUNCE_MS);
  }

  function flush(raw: string) {
    const parsed = parse(raw);
    if (parsed === undefined) return;
    if (timer.current) {
      clearTimeout(timer.current);
      timer.current = null;
    }
    pending.current = undefined;
    void onEditRef.current(uuid, parsed, { period, entity_scope: scope });
  }

  const badge =
    status === "saving"
      ? "Saving…"
      : status === "error"
      ? "Save failed"
      : "";

  const inputTestId = period
    ? `value-input-${uuid}-${period}`
    : `value-input-${uuid}`;
  const statusTestId = period
    ? `value-status-${uuid}-${period}`
    : `value-status-${uuid}`;
  const highlightEmpty = highlight && draft.trim() === "";
  // Accounting-grouped ("(20,667)" for negatives) when at rest so an editable
  // leaf matches an adjacent COMPUTED total; raw digits while focused so
  // typing, cursor position, and the round-trip aren't disturbed (issue 4 /
  // C5). parse() reads the parens back to a negative on save.
  const displayValue = focused ? draft : formatGroupedAccounting(draft);

  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: compact ? "flex-end" : "center",
        flexDirection: compact ? "column" : "row",
        gap: compact ? 2 : pwc.space.sm,
        width: "100%",
        maxWidth: "100%",
      }}
    >
      {badge && !compact && (
        <span
          data-testid={statusTestId}
          style={{
            fontSize: 12,
            color: status === "error" ? pwc.error : pwc.grey700,
          }}
        >
          {badge}
        </span>
      )}
      <input
        data-testid={inputTestId}
        inputMode="decimal"
        title={status === "saved" ? "Saved" : undefined}
        value={displayValue}
        onChange={(e) => {
          // Keep the raw (comma-free) form in `draft`; the display adds the
          // separators when blurred. Strip any commas the user/browser
          // inserted so parse/save see a clean number.
          const raw = e.target.value.replace(/,/g, "");
          setDraft(raw);
          schedule(raw);
        }}
        onFocus={() => setFocused(true)}
        onBlur={(e) => {
          setFocused(false);
          flush(e.target.value);
        }}
        style={{
          width: "100%",
          boxSizing: "border-box",
          minWidth: 0,
          textAlign: "right",
          padding: `${pwc.space.sm}px ${pwc.space.md}px`,
          border: `1px solid ${
            status === "error"
              ? pwc.error
              : highlightEmpty
              ? pwc.orange400
              : pwc.grey300
          }`,
          borderRadius: pwc.radius.md,
          fontFamily: pwc.fontBody,
          fontVariantNumeric: "tabular-nums",
          fontSize: 14,
          background: highlightEmpty ? pwc.orange50 : pwc.white,
        }}
      />
      {badge && compact && (
        <span
          data-testid={statusTestId}
          style={{
            fontSize: 11,
            lineHeight: 1,
            color: status === "error" ? pwc.error : pwc.grey500,
          }}
        >
          {badge}
        </span>
      )}
    </span>
  );
}

const styles = {
  // 3-column workspace shell. No flex-wrap: columns keep their row so the
  // resize handles stay between them; the Results column flexes to fill.
  shell: {
    display: "flex",
    alignItems: "flex-start",
    gap: pwc.space.md,
    fontFamily: pwc.fontBody,
  } as React.CSSProperties,
  column: {
    display: "flex",
    flexDirection: "column" as const,
    gap: pwc.space.lg,
    minWidth: 0,
    position: "sticky" as const,
    top: pwc.space.lg,
    alignSelf: "flex-start",
    maxHeight: "calc(100vh - 32px)",
    overflowY: "auto" as const,
  } as React.CSSProperties,
  resultsCol: {
    flex: "1 1 460px",
    minWidth: 0,
    display: "flex",
    flexDirection: "column" as const,
  } as React.CSSProperties,
  columnHeader: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    padding: `0 ${pwc.space.xs}px`,
  } as React.CSSProperties,
  columnHeaderTitle: {
    fontFamily: pwc.fontHeading,
    fontSize: 11,
    fontWeight: 600,
    color: pwc.grey500,
    textTransform: "uppercase" as const,
    letterSpacing: 0,
  } as React.CSSProperties,
  columnHideBtn: {
    border: "none",
    background: "transparent",
    color: pwc.grey500,
    fontSize: 11,
    fontWeight: 600,
    cursor: "pointer",
    padding: `2px ${pwc.space.xs}px`,
  } as React.CSSProperties,
  collapsedRail: {
    flex: "0 0 40px",
    alignSelf: "stretch",
    minHeight: 240,
    border: `1px solid ${pwc.grey200}`,
    borderRadius: pwc.radius.md,
    cursor: "pointer",
    display: "flex",
    flexDirection: "column" as const,
    alignItems: "center",
    justifyContent: "space-between",
    gap: pwc.space.md,
    padding: `${pwc.space.md}px 0`,
    position: "sticky" as const,
    top: pwc.space.lg,
    transition: "background 0.12s, border-color 0.12s, color 0.12s",
  } as React.CSSProperties,
  collapsedRailChevron: {
    fontSize: 14,
    lineHeight: 1,
    fontWeight: 600,
  } as React.CSSProperties,
  collapsedRailLabel: {
    writingMode: "vertical-rl" as const,
    transform: "rotate(180deg)",
    fontFamily: pwc.fontHeading,
    fontSize: 11,
    fontWeight: 600,
    letterSpacing: 0,
  } as React.CSSProperties,
  resizeHandle: {
    flex: "0 0 6px",
    alignSelf: "stretch",
    minHeight: 240,
    borderRadius: 3,
    cursor: "col-resize",
    position: "sticky" as const,
    top: pwc.space.lg,
  } as React.CSSProperties,
  panelCard: {
    ...ui.card,
    overflow: "hidden",
    // The parent menu column is a bounded flex column (maxHeight ~100vh). Without
    // this, panels inherit flex-shrink:1 and get squashed BELOW their content
    // height; combined with overflow:hidden that silently clips the tail of the
    // sheet list (Notes + expanded sub-sheets) with no scrollbar. flexShrink:0
    // keeps each panel at its natural height so the column's own overflowY:auto
    // scrolls the whole rail and every entry stays reachable.
    flexShrink: 0,
  } as React.CSSProperties,
  panelHeader: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    width: "100%",
    border: "none",
    background: pwc.grey50,
    borderBottom: `1px solid ${pwc.grey100}`,
    cursor: "pointer",
    padding: `${pwc.space.md}px ${pwc.space.lg}px`,
    textAlign: "left" as const,
  } as React.CSSProperties,
  panelHeaderTitle: {
    fontFamily: pwc.fontHeading,
    fontSize: 11,
    fontWeight: 600,
    color: pwc.grey500,
    textTransform: "uppercase" as const,
    letterSpacing: 0,
  } as React.CSSProperties,
  panelChevron: {
    color: pwc.grey500,
    fontSize: 12,
    transition: "transform 0.15s",
  } as React.CSSProperties,
  panelBody: {
    padding: pwc.space.lg,
  } as React.CSSProperties,
  reviewHeader: {
    ...ui.card,
    padding: pwc.space.xl,
    marginBottom: pwc.space.xl,
  } as React.CSSProperties,
  titleRow: {
    display: "flex",
    alignItems: "flex-start",
    justifyContent: "space-between",
    gap: pwc.space.xl,
    flexWrap: "wrap",
  } as React.CSSProperties,
  pageTitle: {
    fontFamily: pwc.fontHeading,
    color: pwc.grey900,
    fontSize: 24,
    fontWeight: pwc.weight.medium,
    lineHeight: 1.25,
    margin: 0,
  } as React.CSSProperties,
  actionRow: {
    display: "flex",
    alignItems: "center",
    justifyContent: "flex-end",
    gap: pwc.space.md,
    flexWrap: "wrap",
  } as React.CSSProperties,
  issueNav: {
    display: "flex",
    alignItems: "center",
    gap: pwc.space.sm,
  } as React.CSSProperties,
  recheckSummary: {
    fontSize: 12,
    color: pwc.grey700,
    whiteSpace: "nowrap",
  } as React.CSSProperties,
  summaryStrip: {
    display: "grid",
    gridTemplateColumns: "repeat(auto-fit, minmax(120px, 1fr))",
    gap: pwc.space.lg,
    marginTop: pwc.space.xl,
  } as React.CSSProperties,
  metric: {
    background: pwc.grey50,
    border: `1px solid ${pwc.grey200}`,
    borderRadius: pwc.radius.lg,
    padding: `${pwc.space.md}px ${pwc.space.lg}px`,
    display: "flex",
    flexDirection: "column" as const,
    gap: 2,
  } as React.CSSProperties,
  metricValue: {
    fontFamily: pwc.fontBody,
    fontVariantNumeric: "tabular-nums",
    fontSize: 20,
    fontWeight: pwc.weight.regular,
    color: pwc.grey900,
  } as React.CSSProperties,
  metricLabel: {
    fontFamily: pwc.fontHeading,
    fontSize: 13,
    fontWeight: 500,
    letterSpacing: 0,
  } as React.CSSProperties,
  metricCaption: {
    fontSize: 12,
    lineHeight: 1.45,
    color: pwc.grey700,
    marginTop: 2,
  } as React.CSSProperties,
  errorBanner: {
    marginBottom: pwc.space.md,
    padding: `${pwc.space.sm}px ${pwc.space.md}px`,
    background: pwc.white,
    border: `1px solid ${pwc.grey200}`,
    borderLeft: `3px solid ${pwc.error}`,
    borderRadius: pwc.radius.sm,
    color: pwc.grey800,
    fontSize: 13,
    lineHeight: 1.5,
  } as React.CSSProperties,
  crossChecksWrap: {
    marginBottom: pwc.space.lg,
  } as React.CSSProperties,
  toolbar: {
    ...ui.card,
    padding: pwc.space.xl,
    marginBottom: pwc.space.xl,
    display: "flex",
    flexWrap: "wrap",
    gap: pwc.space.md,
    alignItems: "end",
    position: "sticky" as const,
    top: 0,
    zIndex: 5,
  } as React.CSSProperties,
  controlGroup: {
    display: "flex",
    flexDirection: "column" as const,
    gap: pwc.space.xs,
  } as React.CSSProperties,
  searchGroup: {
    flex: "1 1 280px",
    minWidth: 260,
    display: "flex",
    flexDirection: "column" as const,
    gap: pwc.space.xs,
  } as React.CSSProperties,
  rowFilters: {
    display: "flex",
    alignItems: "center",
    flexWrap: "wrap" as const,
    gap: pwc.space.xs,
    width: "100%",
  } as React.CSSProperties,
  srOnly: {
    position: "absolute" as const,
    width: 1,
    height: 1,
    padding: 0,
    margin: -1,
    overflow: "hidden",
    clip: "rect(0, 0, 0, 0)",
    whiteSpace: "nowrap" as const,
    border: 0,
  } as React.CSSProperties,
  segmented: {
    display: "inline-flex",
    border: `1px solid ${pwc.grey300}`,
    borderRadius: pwc.radius.lg,
    overflow: "hidden",
    background: pwc.white,
    minHeight: 44,
  } as React.CSSProperties,
  segmentedButton: {
    padding: `${pwc.space.md}px ${pwc.space.lg}px`,
    border: "none",
    borderRight: `1px solid ${pwc.grey200}`,
    cursor: "pointer",
    fontFamily: pwc.fontHeading,
    fontSize: 14,
    fontWeight: pwc.weight.medium,
    minWidth: 54,
  } as React.CSSProperties,
  sideNav: {
    display: "flex",
    flexDirection: "column" as const,
    gap: pwc.space.sm,
    minWidth: 0,
    // Bound the sheet list to its own scroll area so the lower entries —
    // notably Notes and its sub-tabs — are always reachable even when the
    // page is embedded in the Values tab (where the column's outer scroll
    // can be clipped by the tab container). Without this the tail of the
    // list got pushed off-screen with no way to scroll to it.
    maxHeight: "min(52vh, 540px)",
    overflowY: "auto" as const,
    paddingRight: pwc.space.xs,
  } as React.CSSProperties,
  sideNavItem: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    gap: pwc.space.sm,
    width: "100%",
    textAlign: "left" as const,
    padding: `${pwc.space.md}px ${pwc.space.lg}px`,
    border: `1px solid ${pwc.grey200}`,
    borderRadius: pwc.radius.lg,
    cursor: "pointer",
    fontFamily: pwc.fontBody,
    fontSize: 14,
    fontWeight: 500,
  } as React.CSSProperties,
  sideNavLabel: {
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap" as const,
    minWidth: 0,
  } as React.CSSProperties,
  // Stacks the statement code over its plain-English subtitle.
  sideNavLabelStack: {
    display: "flex",
    flexDirection: "column" as const,
    gap: 1,
  } as React.CSSProperties,
  sideNavSubtitle: {
    fontSize: 11,
    fontWeight: pwc.weight.regular,
    color: pwc.grey500,
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap" as const,
  } as React.CSSProperties,
  sideNavSubGroup: {
    display: "flex",
    flexDirection: "column" as const,
    gap: pwc.space.sm,
    margin: `${pwc.space.sm}px 0 ${pwc.space.sm}px ${pwc.space.md}px`,
    paddingLeft: pwc.space.sm,
    borderLeft: `2px solid ${pwc.grey200}`,
  } as React.CSSProperties,
  sideNavSubItem: {
    display: "flex",
    alignItems: "center",
    width: "100%",
    textAlign: "left" as const,
    padding: `${pwc.space.sm}px ${pwc.space.md}px`,
    border: `1px solid ${pwc.grey200}`,
    borderRadius: pwc.radius.md,
    cursor: "pointer",
    fontFamily: pwc.fontBody,
    fontSize: 13,
  } as React.CSSProperties,
  sideNavBadge: {
    flex: "0 0 auto",
    minWidth: 18,
    textAlign: "center" as const,
    padding: `1px ${pwc.space.xs}px`,
    borderRadius: 9,
    background: pwc.white,
    border: `1px solid ${pwc.error}`,
    color: pwc.grey800,
    fontSize: 11,
    fontWeight: pwc.weight.semibold,
  } as React.CSSProperties,
  panelMuted: {
    margin: 0,
    color: pwc.grey700,
    fontSize: 14,
    lineHeight: 1.5,
  } as React.CSSProperties,
  evidenceStack: {
    display: "flex",
    flexDirection: "column" as const,
    gap: pwc.space.md,
  } as React.CSSProperties,
  evidenceGrid: {
    display: "grid",
    gridTemplateColumns: "1fr",
    gap: pwc.space.md,
  } as React.CSSProperties,
  evidenceLabel: {
    fontFamily: pwc.fontHeading,
    fontSize: 11,
    fontWeight: 500,
    color: pwc.grey500,
    textTransform: "uppercase" as const,
    letterSpacing: 0,
    marginBottom: 2,
  } as React.CSSProperties,
  evidenceText: {
    fontSize: 14,
    color: pwc.grey900,
    lineHeight: 1.45,
    overflowWrap: "anywhere" as const,
  } as React.CSSProperties,
  tableShell: {
    ...ui.card,
    overflowX: "auto",
    overflowY: "hidden",
  } as React.CSSProperties,
  treeHeaderRow: {
    display: "grid",
    gridTemplateColumns: "minmax(260px, 1fr) minmax(150px, 190px) 120px minmax(120px, 180px)",
    gap: pwc.space.md,
    minWidth: 760,
    padding: `${pwc.space.lg}px ${pwc.space.xl}px`,
    background: pwc.grey50,
    color: pwc.grey700,
    borderBottom: `1px solid ${pwc.grey200}`,
    position: "sticky" as const,
    top: 0,
    zIndex: 4,
  } as React.CSSProperties,
  headerCell: {
    fontFamily: pwc.fontHeading,
    fontSize: 14,
    fontWeight: pwc.weight.medium,
  } as React.CSSProperties,
  // Numeric columns (CY / PY / Value) right-align, header included, so
  // magnitudes line up the way accountants read them.
  headerCellNumeric: {
    fontFamily: pwc.fontHeading,
    fontSize: 14,
    fontWeight: pwc.weight.medium,
    textAlign: "right" as const,
  } as React.CSSProperties,
  valueCell: {
    textAlign: "right" as const,
    display: "flex",
    justifyContent: "flex-end",
    alignItems: "center",
    minWidth: 0,
    fontFamily: pwc.fontBody,
    fontVariantNumeric: "tabular-nums",
  } as React.CSSProperties,
  emptyValueBox: {
    display: "inline-block",
    width: "100%",
    boxSizing: "border-box",
    height: 32,
    border: `1px solid ${pwc.grey300}`,
    borderRadius: pwc.radius.md,
    background: pwc.white,
  } as React.CSSProperties,
  mandatoryEmptyValueBox: {
    display: "inline-block",
    width: "100%",
    boxSizing: "border-box",
    height: 32,
    border: `1px solid ${pwc.orange400}`,
    borderRadius: pwc.radius.md,
    background: pwc.orange50,
  } as React.CSSProperties,
  // Computed totals / non-editable values: same footprint as the editable
  // input but with a read-only look (faint fill, muted border, no caret).
  readonlyValueBox: {
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "flex-end",
    width: "100%",
    boxSizing: "border-box",
    minHeight: 32,
    padding: `${pwc.space.xs}px ${pwc.space.sm}px`,
    border: `1px solid ${pwc.grey200}`,
    borderRadius: pwc.radius.md,
    background: pwc.grey50,
    fontFamily: pwc.fontBody,
    fontVariantNumeric: "tabular-nums",
    fontSize: 14,
    color: pwc.grey800,
  } as React.CSSProperties,
  stateCell: {
    display: "flex",
    alignItems: "center",
    minWidth: 0,
  } as React.CSSProperties,
  // Small caption under a mandatory line item whose value is still blank —
  // names the reason the input is highlighted orange.
  requiredChip: {
    fontSize: 12,
    color: pwc.warningText,
    fontWeight: pwc.weight.medium,
  } as React.CSSProperties,
  // No-source badge + its filter toggle (UX-QA #6).
  noSourceChip: {
    fontSize: 12,
    color: pwc.warningText,
    fontWeight: pwc.weight.medium,
  } as React.CSSProperties,
  noSourceFilterLabel: {
    display: "flex",
    alignItems: "center",
    gap: pwc.space.xs,
    marginTop: pwc.space.xs,
    fontFamily: pwc.fontBody,
    fontSize: 12,
    color: pwc.grey700,
    cursor: "pointer",
  } as React.CSSProperties,
  sourceCell: {
    color: pwc.grey700,
    fontSize: 14,
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap" as const,
  } as React.CSSProperties,
  matrixShell: {
    ...ui.card,
    overflowX: "auto",
  } as React.CSSProperties,
  matrixHeaderMovement: {
    padding: `${pwc.space.sm}px ${pwc.space.md}px`,
    display: "flex",
    alignItems: "center",
    position: "sticky" as const,
    left: 0,
    zIndex: 3,
    background: pwc.grey100,
    borderRight: `1px solid ${pwc.grey200}`,
  } as React.CSSProperties,
  matrixComponentHeader: {
    padding: `${pwc.space.sm}px ${pwc.space.md}px`,
    textAlign: "center" as const,
    lineHeight: 1.25,
    whiteSpace: "normal" as const,
    overflowWrap: "anywhere" as const,
    borderLeft: `1px solid ${pwc.grey200}`,
    borderBottom: `1px solid ${pwc.grey200}`,
    minHeight: 42,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
  } as React.CSSProperties,
  matrixPeriodHeader: {
    padding: `${pwc.space.xs}px ${pwc.space.sm}px`,
    textAlign: "right" as const,
    color: pwc.grey500,
    fontFamily: pwc.fontHeading,
    fontSize: 11,
    fontWeight: 600,
    textTransform: "uppercase" as const,
    letterSpacing: 0,
    borderLeft: `1px solid ${pwc.grey200}`,
  } as React.CSSProperties,
  matrixMovementCell: {
    padding: `${pwc.space.sm}px ${pwc.space.md}px`,
    position: "sticky" as const,
    left: 0,
    zIndex: 2,
    background: pwc.white,
    borderRight: `1px solid ${pwc.grey100}`,
    lineHeight: 1.35,
  } as React.CSSProperties,
};
