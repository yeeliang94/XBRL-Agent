import { useEffect, useState, useCallback, useRef, useMemo } from "react";
import { ApiError, userMessage } from "../lib/errors";
import { pwc, tokens } from "../lib/theme";
import { ExpandMore, LeftPanelClose } from "../components/iconGlyphs";
import { ui, uiClass } from "../lib/uiStyles";
import { STATUS_SYMBOLS } from "../lib/runStatus";
import { StatusIcon } from "../components/StatusIcon";
import { NotesReviewTab } from "../components/NotesReviewTab";
import { ResizableDivider } from "../components/ResizableDivider";
import { ReconciliationQueue } from "../components/ReconciliationQueue";
import { PdfSourcePane } from "../components/PdfSourcePane";
import { fetchPdfPageCount } from "../lib/api";
import {
  figureSheetDisplayName,
  templateDisplayName,
  templateSubtitle,
  templateSortKey,
} from "../lib/sheetLabels";
import { parseEvidencePages } from "../lib/evidencePages";
import {
  formatAccounting,
  formatGroupedInput,
  formatGroupedAccounting,
  parseAccountingInput,
} from "../lib/numberFormat";
import type { CrossCheckResult } from "../lib/types";
import { TemplateSettingsPage } from "./TemplateSettingsPage";
import {
  HumanComparisonBar,
  type ComparisonPane,
  type ComparisonTile,
} from "../components/HumanComparisonBar";
import { HumanStatusMarker } from "../components/HumanStatusMarker";
import {
  getHumanComparison,
  humanSlotKey,
  HUMAN_STATUS_LABEL,
  type HumanComparison,
  type HumanFigureSlot,
  type HumanFileRecord,
  type HumanSlotStatus,
} from "../lib/humanFile";

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
  scope_fact_details?: Record<
    string,
    Record<string, {
      value: number | null;
      value_status: string | null;
      children_status: string | null;
      source: string | null;
      evidence: string | null;
    }>
  >;
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

function rowHasOpenableSource(row: ConceptRow): boolean {
  return parseEvidencePages(row.evidence).length > 0 || parseEvidencePages(row.source).length > 0;
}

export interface ConceptsPageProps {
  // Null when the Concepts top-nav tab is opened without a run selected —
  // the page then shows a "pick a run" empty state instead of fetching.
  runId: number | null;
  // The current cross-checks, passed by the run report so the compact
  // attention disclosure can target failing checks. Optional — the standalone
  // template view has none.
  initialCrossChecks?: CrossCheckResult[];
  onPreparationBlocked?: (blocked: boolean) => void;
  /** Open the unified review workspace directly on its persistent Notes
   *  index/editor/source composition. Used by the run-detail Notes route. */
  initialView?: "figures" | "notes";
  /** The human-filled mTool file attached to this run, if any. When set, the
   *  right-hand panel can show the human's values instead of the Source PDF. */
  humanFile?: HumanFileRecord | null;
  onReplaceHumanFile?: () => void;
  onHumanFileRemoved?: () => void;
}

type Period = "CY" | "PY";
type HumanRowFilter = "human_differs" | "human_missed" | "human_ai_only";
type RowFilter = "review" | "attention" | "extracted" | "edited" | "calculated" | "no_source" | "blank" | "all" | HumanRowFilter;
const HUMAN_ROW_FILTERS: Array<{ value: HumanRowFilter; label: string; status: HumanSlotStatus }> = [
  { value: "human_differs", label: "Differs from human", status: "different" },
  { value: "human_missed", label: "Missed by AI", status: "missed" },
  { value: "human_ai_only", label: "AI-only", status: "ai_only" },
];

/** What the figures table needs to show the human columns. */
interface HumanView {
  slots: Map<string, HumanFigureSlot>;
  notComparedTemplates: Set<string>;
}
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
  pdfWidth?: number;
  pdfCollapsed?: boolean;
  activeScope?: "Company" | "Group";
}

function readWorkspacePreferences(runId: number | null): WorkspacePreferences {
  if (runId == null || typeof window === "undefined" || import.meta.env.MODE === "test") return {};
  try {
    return JSON.parse(window.sessionStorage.getItem(`xbrl-review:${runId}`) ?? "{}") as WorkspacePreferences;
  } catch {
    return {};
  }
}

export function resolveInitialWorkspaceTemplate(
  initialView: "figures" | "notes",
  storedTemplate: string | null | undefined,
  loadedTemplates: string[],
): string | null {
  if (initialView === "notes") return NOTES_KEY;
  if (
    storedTemplate &&
    storedTemplate !== NOTES_KEY &&
    loadedTemplates.includes(storedTemplate)
  ) {
    return storedTemplate;
  }
  return loadedTemplates[0] ?? null;
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

function conceptForScope(
  row: ConceptRow,
  scope: "Company" | "Group",
  period: Period = "CY",
): ConceptRow {
  const detail = row.scope_fact_details?.[scope]?.[period];
  if (!detail) return row;
  return {
    ...row,
    value: detail.value,
    value_status: detail.value_status,
    children_status: detail.children_status,
    source: detail.source,
    evidence: detail.evidence,
  };
}

function isMandatoryConcept(row: ConceptRow): boolean {
  return row.canonical_label.trim().startsWith("*");
}

function isBlankValue(value: number | null | undefined): boolean {
  return value == null;
}

// Provenance is retained in the data and explained only in Field details.
// A recorded value is not a verification verdict.
function describeValueOrigin(row: ConceptRow): string {
  if (row.source?.trim().toLowerCase() === "cascade") return "Calculated from component figures";
  if (row.value_status === "user_override" || row.source?.trim().toLowerCase() === "manual edit") return "Manually edited";
  if (row.kind === "COMPUTED" && row.value != null) return "Calculated from component figures";
  switch (row.value_status) {
    case "observed": return "Recorded value; not independently verified";
    case "explicit_zero": return "Zero recorded in the source";
    case "not_disclosed": return "Not disclosed in the source";
    case "conflict": return "Conflicting values — review required";
    case "missing":
    case "pending_input":
    case "not_found": return "No value recorded";
    default: return "Origin not recorded";
  }
}

function displayConceptSource(row: ConceptRow): string {
  const evidencePages = parseEvidencePages(row.evidence);
  const pages = evidencePages.length ? evidencePages : parseEvidencePages(row.source);
  return pages.length ? `Page${pages.length === 1 ? "" : "s"} ${pages.join(", ")}` : "";
}

/** A comparison column header: "AI CY" / "Human CY". The full reporting
 *  period ("CY (year ended …)") stays in the header's tooltip so the paired
 *  columns stay narrow enough to read on one line. */
function comparisonHeader(source: "AI" | "Human", periodLabel: string | null): string {
  if (!periodLabel) return source === "AI" ? "AI value" : "Human value";
  return `${source} ${periodLabel.split(" (")[0]}`;
}

function treeColumns(showPeriods: boolean, human = false): string {
  if (human) {
    return showPeriods
      ? "minmax(130px, 1fr) repeat(4, minmax(88px, 120px))"
      : "minmax(130px, 1fr) repeat(2, minmax(100px, 150px))";
  }
  return showPeriods
    ? "minmax(130px, 1fr) minmax(88px, 120px) minmax(88px, 120px)"
    : "minmax(130px, 1fr) minmax(100px, 150px)";
}

function humanSlotsForRow(
  row: ConceptRow,
  human: HumanView,
  scope: "Company" | "Group",
): HumanFigureSlot[] {
  return (["CY", "PY"] as const)
    .map((period) => human.slots.get(humanSlotKey(row.concept_uuid, period, scope)))
    .filter((slot): slot is HumanFigureSlot => slot != null);
}

export function ConceptsPage({
  runId,
  initialCrossChecks,
  onPreparationBlocked,
  initialView = "figures",
  humanFile = null,
  onReplaceHumanFile,
  onHumanFileRemoved,
}: ConceptsPageProps) {
  const initialWorkspace = useRef<WorkspacePreferences>(readWorkspacePreferences(runId));
  const [concepts, setConcepts] = useState<ConceptRow[]>([]);
  // Reporting periods (e.g. "FY2021" / "FY2020") from the run's scout, used to
  // label the CY / PY column headers with their years (D5). Null when scout
  // didn't capture them — the headers stay plain "CY" / "PY".
  const [reportingCy, setReportingCy] = useState<string | null>(null);
  const [reportingPy, setReportingPy] = useState<string | null>(null);
  const [activeTemplate, setActiveTemplate] = useState<string | null>(
    initialView === "notes"
      ? NOTES_KEY
      : initialWorkspace.current.activeTemplate === NOTES_KEY
        ? null
        : initialWorkspace.current.activeTemplate ?? null,
  );
  const [loadError, setLoadError] = useState<string | null>(null);
  // Phase 2 (step 2.10): cross-template search.  We keep the search
  // index in the page rather than re-querying so multi-statement
  // navigation stays snappy on slow networks.
  const [searchQuery, setSearchQuery] = useState(initialWorkspace.current.searchQuery ?? "");
  // Keep empty alternatives visible for comparison against the mTool worksheet.
  const [rowFilter, setRowFilter] = useState<RowFilter>(initialWorkspace.current.rowFilter ?? "all");
  // Phase 4 step 4.12 — Group runs toggle between Company / Group
  // value columns.  Defaults to Company; the toggle is rendered only
  // when at least one concept carries facts in both scopes.
  const [activeScope, setActiveScope] = useState<"Company" | "Group">(
    initialWorkspace.current.activeScope ?? "Company"
  );
  // Phase 2.1 — per-cell save status for the editable value column.
  // Keyed by concept_uuid so each row shows its own Saving/Saved/Failed
  // badge (mirrors the notes editor's per-cell status pattern).
  const [editStatus, setEditStatus] = useState<
    Record<string, "saving" | "saved" | "error">
  >({});
  // Bumped after every successful value edit so the compact attention count
  // re-fetches — a leaf edit can open or clear a partial-state conflict.
  const [conflictReloadKey, setConflictReloadKey] = useState(0);
  const [editedCount, setEditedCount] = useState(0);
  const [attentionOpen, setAttentionOpen] = useState(false);
  // Sub-sheet filter (M3 nested nav): when set, the tree shows only this
  // render_sheet within the active template. null = all sheets of the template.
  const [activeSheet, setActiveSheet] = useState<string | null>(initialWorkspace.current.activeSheet ?? null);
  // The notes checklist can focus a specific filing sheet/cell in the
  // dedicated Notes workspace. Figures and Notes have separate run tabs, so
  // notes are intentionally not repeated in the figures statement picker.
  const [activeNotesSheet, setActiveNotesSheet] = useState<string | null>(null);
  // Source-PDF pages for the notes cell the reviewer last focused. A face
  // concept drives the PDF pane from its evidence string; a notes cell has no
  // concept row, so NotesReviewTab reports the focused cell's `source_pages`
  // up here instead (review-workspace Phase 1). Cleared on navigation below so
  // a stale note's pages don't linger when switching sheets.
  const [notesPdfPages, setNotesPdfPages] = useState<number[]>([]);
  const [notesPdfSelectionKey, setNotesPdfSelectionKey] = useState(0);
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
  // Wider default so the source PDF is actually readable at rest (UX-QA #7f) —
  // still user-resizable/collapsible for reviewers who want more table room.
  const [pdfWidth, setPdfWidth] = useState(initialWorkspace.current.pdfWidth ?? 520);
  const workspaceRef = useRef<HTMLDivElement>(null);
  const pdfMaxWidthRef = useRef(720);
  const [pdfCollapsed, setPdfCollapsed] = useState(initialWorkspace.current.pdfCollapsed ?? false);
  // A run without a stored PDF folds the source column to its rail so the
  // figures and notes get the width; opening the rail still explains why.
  const [pdfMissing, setPdfMissing] = useState(false);
  const [showMissingPdf, setShowMissingPdf] = useState(false);
  useEffect(() => {
    if (runId == null) return;
    let cancelled = false;
    void fetchPdfPageCount(runId).then((count) => {
      if (!cancelled) setPdfMissing(count === null);
    });
    return () => { cancelled = true; };
  }, [runId]);
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
      pdfWidth,
      pdfCollapsed,
      activeScope,
    };
    try {
      window.sessionStorage.setItem(`xbrl-review:${runId}`, JSON.stringify(prefs));
    } catch {
      // Storage can be unavailable in locked-down browsers; review remains
      // fully usable in-memory, so persistence failure is intentionally quiet.
    }
  }, [runId, searchQuery, rowFilter, activeTemplate, activeSheet, selectedConceptUuid, pdfWidth, pdfCollapsed, activeScope]);
  // Initial load.  Peer-review #11: abort the in-flight request on
  // unmount / runId change so a slow response can't land on a stale
  // component or clobber a newer run's data.
  useEffect(() => {
    if (runId == null) return;
    const controller = new AbortController();
    fetch(`/api/runs/${runId}/concepts`, { signal: controller.signal })
      .then((r) => {
        if (!r.ok) throw ApiError.fromResponse(r.status, null);
        return r.json();
      })
      .then((data) => {
        setConcepts(data.concepts || []);
        setReportingCy(data.reporting_period_cy ?? null);
        setReportingPy(data.reporting_period_py ?? null);
        const loaded = (data.concepts || []) as ConceptRow[];
        const loadedTemplates = Array.from(new Set(loaded.map((row) => row.template_id)));
        const resolvedTemplate = resolveInitialWorkspaceTemplate(
          initialView,
          initialWorkspace.current.activeTemplate,
          loadedTemplates,
        );
        setActiveTemplate(resolvedTemplate);
        // A stored sheet that is not part of the restored statement would
        // hide every row of it.
        const storedSheet = initialWorkspace.current.activeSheet;
        if (storedSheet && !loaded.some((row) =>
          row.template_id === resolvedTemplate && row.render_sheet === storedSheet)) {
          setActiveSheet(null);
        }
      })
      .catch((err) => {
        // AbortError is expected on cleanup — don't surface it.
        if (err?.name === "AbortError") return;
        setLoadError(userMessage(err));
      });
    return () => {
      controller.abort();
    };
  }, [runId, initialView]);

  useEffect(() => {
    if (runId == null) return;
    const controller = new AbortController();
    fetch(`/api/runs/${runId}/facts/edited_count`, { signal: controller.signal })
      .then((response) => (response.ok ? response.json() : { count: 0 }))
      .then((payload) => setEditedCount(payload.count || 0))
      .catch((error) => {
        if (error?.name !== "AbortError") setEditedCount(0);
      });
    return () => controller.abort();
  }, [runId, conflictReloadKey]);

  // Human-file comparison. Recomputed by the server from the run's current
  // values, so it is re-read after every saved edit (conflictReloadKey).
  const [comparison, setComparison] = useState<HumanComparison | null>(null);
  const [comparisonPane, setComparisonPane] = useState<ComparisonPane>("human");
  const humanFileId = humanFile ? `${humanFile.sha256}:${humanFile.uploaded_at}` : null;
  useEffect(() => {
    // A newly attached file opens on the human columns.
    setComparisonPane("human");
  }, [humanFileId]);
  useEffect(() => {
    if (runId == null || humanFileId == null) {
      setComparison(null);
      return;
    }
    let cancelled = false;
    getHumanComparison(runId)
      .then((data) => { if (!cancelled) setComparison(data); })
      .catch(() => { if (!cancelled) setComparison(null); });
    return () => { cancelled = true; };
  }, [runId, humanFileId, conflictReloadKey]);
  const humanActive = comparison != null && comparisonPane === "human";
  const humanView = useMemo<HumanView | null>(() => {
    if (!comparison) return null;
    const slots = new Map<string, HumanFigureSlot>();
    for (const slot of comparison.figures.slots) {
      slots.set(humanSlotKey(slot.concept_uuid, slot.period, slot.entity_scope, slot.dimension_key), slot);
    }
    return {
      slots,
      notComparedTemplates: new Set(
        comparison.file.not_compared
          .filter((n) => n.reason !== "not_in_run")
          .map((n) => n.template_id),
      ),
    };
  }, [comparison]);

  // Open-conflict counts feed the compact attention control. Keep the request
  // keyed on conflictReloadKey so it refreshes after an edit or a manual
  // resolve/dismiss action.
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
        const openConflicts = (data.conflicts || []).filter(
          (c: { status: string }) => c.status === "open"
        );
        for (const cf of openConflicts) {
          const tid = templateByUuid.get(cf.concept_uuid);
          if (tid) counts[tid] = (counts[tid] || 0) + 1;
        }
        setConflictCounts(counts);
      })
      .catch((err) => {
        if (err?.name !== "AbortError") {
          setConflictCounts({});
        }
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
      if (runId == null) return;
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
        const resp = await fetch(`/api/runs/${runId}/facts/${concept_uuid}`, {
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
            const next: ConceptRow = {
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
            if (c.scope_fact_details || period === "PY" || entity_scope !== "Company") {
              const currentDetail = c.scope_fact_details?.[entity_scope]?.[period];
              next.scope_fact_details = {
                ...c.scope_fact_details,
                [entity_scope]: {
                  ...(c.scope_fact_details?.[entity_scope] || {}),
                  [period]: {
                    value: v,
                    value_status: "user_override",
                    children_status: currentDetail?.children_status ?? null,
                    source: "manual edit",
                    evidence: currentDetail?.evidence ?? null,
                  },
                },
              };
            }
            return next;
          })
        );
        setEditStatus((s) => ({ ...s, [editKey]: "saved" }));
        // A leaf edit may open or clear a partial-state conflict in the
        // cascade — refresh the compact attention count.
        setConflictReloadKey((k) => k + 1);
      } catch (err) {
        if (keepalive) return;
        setEditStatus((s) => ({ ...s, [editKey]: "error" }));
      }
    },
    [runId, activeScope]
  );

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
      // Open the target's worksheet so the selected row is visible.
      setActiveSheet(target.render_sheet);
      setRowFilter("all");
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

  // Distinct templates for the compact picker, in financial-statement reading
  // order (SOFP → SOPL → SOCI → SOCIE → SOCF) — the backend's incidental
  // order surfaced alphabetically, putting cash flows first (run-168 design
  // critique). Array.sort is stable, so unrecognised templates keep their
  // backend order at the end.
  const templates: string[] = [];
  for (const c of concepts) {
    if (!templates.includes(c.template_id)) templates.push(c.template_id);
  }
  templates.sort((a, b) => templateSortKey(a) - templateSortKey(b));

  // Per-template ordered render_sheets let the compact picker retain access to
  // statement breakdowns without reintroducing a second sidebar.
  const sheetsByTemplate = useMemo(() => {
    const map: Record<string, string[]> = {};
    for (const c of concepts) {
      const sheets = (map[c.template_id] ||= []);
      if (!sheets.includes(c.render_sheet)) sheets.push(c.render_sheet);
    }
    return map;
  }, [concepts]);

  // Cross-check state drives the focused attention queue and row filter. The
  // repeated summary cards and re-run action live on Overview/Cross-checks,
  // not inside the editing surface.
  const failingChecks = useMemo(
    () => (initialCrossChecks ?? []).filter(
      (c) => c.status === "failed" || c.status === "warning",
    ),
    [initialCrossChecks],
  );
  const actionableChecks = useMemo(
    () => failingChecks.filter((c) => c.target_sheet && c.target_row != null),
    [failingChecks],
  );

  // Search overrides the template filter (matches happen across all
  // templates so a user can hop between statements via the result
  // list).  Empty query falls back to the active-template view.
  const notesActive = activeTemplate === NOTES_KEY;
  // Both comparison views keep their worksheet navigation visible. The user
  // may fold Figures' rail; narrow layouts place it above the table.
  const [railOpen, setRailOpen] = useState<boolean | null>(null);
  const railFolded = humanActive && railOpen === false;

  useEffect(() => {
    const workspace = workspaceRef.current;
    if (!workspace) return;
    const updateLimit = () => {
      const width = workspace.getBoundingClientRect().width;
      const maxWidth = width > 0 ? Math.min(720, width * 0.34) : 720;
      pdfMaxWidthRef.current = maxWidth;
      // Store the visible width so dragging back from the limit responds
      // immediately and restoring the workspace preserves the same size.
      setPdfWidth((w) => clamp(w, Math.min(260, maxWidth), maxWidth));
    };
    updateLimit();
    const observer = typeof ResizeObserver !== "undefined" ? new ResizeObserver(updateLimit) : null;
    observer?.observe(workspace);
    return () => observer?.disconnect();
  }, [notesActive, runId]);

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

  const figureIssueRows = useMemo(() => concepts.filter((row) =>
    row.kind !== "ABSTRACT" && (
      rowLacksSource(conceptForScope(row, activeScope)) || actionableChecks.some((check) =>
        check.target_sheet === row.render_sheet && check.target_row === row.render_row,
      )
    ),
  ), [concepts, activeScope, actionableChecks]);

  const { filtered, noSourceCount, humanFilterCounts } = useMemo(() => {
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
      return periodValue(row, activeScope, "CY") != null || periodValue(row, activeScope, "PY") != null;
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
    const humanFilter = HUMAN_ROW_FILTERS.find((option) => option.value === rowFilter);
    const matchesRowFilter = (row: ConceptRow) => {
      const scopedRow = conceptForScope(row, activeScope);
      if (rowFilter === "all") return true;
      if (humanFilter) {
        // Without a comparison the stored filter falls back to every row.
        if (!humanView) return true;
        return humanSlotsForRow(row, humanView, activeScope)
          .some((slot) => slot.status === humanFilter.status);
      }
      if (row.kind === "ABSTRACT") return false;
      if (rowFilter === "attention") return rowHasIssue(row) || rowLacksSource(scopedRow);
      if (rowFilter === "extracted") return row.kind !== "COMPUTED" && rowHasValue(row);
      if (rowFilter === "edited") return rowWasEdited(scopedRow);
      if (rowFilter === "calculated") return row.kind === "COMPUTED" && rowHasValue(row);
      if (rowFilter === "no_source") return rowLacksSource(scopedRow);
      if (rowFilter === "blank") return Boolean(row.editable) && !rowHasValue(row);
      return rowHasValue(row) || rowWasEdited(scopedRow) || rowHasIssue(row) || rowLacksSource(scopedRow) || isMandatoryConcept(row);
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
    // Fields (not alias rows) on the current worksheet per human status.
    const humanFilterCounts: Partial<Record<HumanRowFilter, number>> = {};
    if (humanView) {
      for (const option of HUMAN_ROW_FILTERS) {
        humanFilterCounts[option.value] = new Set(baseRows
          .filter((row) => humanSlotsForRow(row, humanView, activeScope)
            .some((slot) => slot.status === option.status))
          .map((row) => row.concept_uuid)).size;
      }
    }
    return {
      filtered: baseRows.filter((row) => visibleUuids.has(row.concept_uuid)),
      noSourceCount: baseRows.filter((row) => rowLacksSource(conceptForScope(row, activeScope))).length,
      humanFilterCounts,
    };
  }, [concepts, searchQuery, activeTemplate, activeSheet, rowFilter, editStatus, actionableChecks, activeScope, humanView]);

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
  const selectedScopedConcept = selectedConcept
    ? conceptForScope(selectedConcept, activeScope)
    : null;

  // Memoised so the PDF pane isn't handed a fresh array on every unrelated
  // re-render (which would reset its current page + zoom). Keyed on the
  // evidence string itself.
  const selectedEvidencePages = useMemo(() => {
    // Prefer the evidence string; fall back to the Source column when evidence
    // carries no page token, so a citation that lives in `source` (e.g.
    // "SOFP p.12") still drives the PDF pane instead of showing "no source
    // page recorded" (E7).
    const fromEvidence = parseEvidencePages(selectedScopedConcept?.evidence);
    if (fromEvidence.length > 0) return fromEvidence;
    return parseEvidencePages(selectedScopedConcept?.source);
  }, [selectedScopedConcept?.evidence, selectedScopedConcept?.source]);

  // Clear the focused note on a run switch. Not keyed on sheet/template change:
  // a requested note focus sets the destination and PDF pages together.
  useEffect(() => {
    setNotesPdfPages([]);
    setNotesPdfSelectionKey(0);
    setNotesCellSelected(false);
    setNotesFocusCell(null);
  }, [runId]);

  // Focusing a notes cell in the editor: follow its recorded pages (possibly
  // none) and mark the selection. An empty page list is a real state —
  // "selected, but no source page recorded" — not "nothing selected".
  const handleNotesCellPages = useCallback((pages: number[]) => {
    setNotesPdfPages(pages);
    setNotesPdfSelectionKey((key) => key + 1);
    setNotesCellSelected(true);
  }, []);

  // Audit panels are mounted beside this workspace and use the shared window
  // event to request an exact notes cell. Keep the handler here, where the
  // production editor state lives, so emitting the event is not mistaken for
  // completed navigation.
  useEffect(() => {
    const onFocus = (event: Event) => {
      const detail = (event as CustomEvent).detail;
      if (!detail || typeof detail.sheet !== "string" || typeof detail.row !== "number") return;
      setSearchQuery("");
      setActiveTemplate(NOTES_KEY);
      setActiveNotesSheet(detail.sheet);
      setNotesPdfPages([]);
      setNotesCellSelected(true);
      setNotesFocusCell((current) => ({
        sheet: detail.sheet,
        row: detail.row,
        key: (current?.key ?? 0) + 1,
      }));
    };
    window.addEventListener("notes-coverage-focus", onFocus);
    return () => window.removeEventListener("notes-coverage-focus", onFocus);
  }, [runId]);

  // The pages the Source PDF pane should show: a focused notes cell's pages
  // when the notes editor is active, otherwise the selected face concept's
  // evidence pages.
  const pdfPages = notesActive ? notesPdfPages : selectedEvidencePages;

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

  // Human-file summary: figures follow the Company/Group switch; notes are
  // placement only. Exceptions are listed only when there are any.
  let comparisonTiles: ComparisonTile[] = [];
  let comparisonExcludes: string | null = null;
  const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;
  if (comparison) {
    if (notesActive) {
      const t = comparison.notes.totals;
      comparisonTiles = [
        { label: "placed", value: `${t.both_filled} of ${t.human_filled}` },
        ...(t.human_filled > t.both_filled ? [{ label: "missed by AI", value: String(t.human_filled - t.both_filled) }] : []),
        ...(t.ai_only > 0 ? [{ label: "AI-only", value: String(t.ai_only) }] : []),
      ];
      const unmatchedNotes = comparison.file.unmatched.filter((u) => u.kind === "note").length;
      if (unmatchedNotes > 0) {
        comparisonTiles.push({ label: "not counted", value: String(unmatchedNotes) });
        comparisonExcludes = `Not counted: ${plural(unmatchedNotes, "unmatched note")}`;
      }
    } else {
      const t = comparison.figures.totals[activeScope] ?? { human_filled: 0, both_filled: 0, same_value: 0, ai_only: 0, zero_blank_excluded: 0 };
      comparisonTiles = [
        { label: "match", value: `${t.same_value} of ${t.human_filled}` },
        ...(t.both_filled > t.same_value ? [{ label: "differ", value: String(t.both_filled - t.same_value) }] : []),
        ...(t.human_filled > t.both_filled ? [{ label: "missed by AI", value: String(t.human_filled - t.both_filled) }] : []),
        ...(t.ai_only > 0 ? [{ label: "AI-only", value: String(t.ai_only) }] : []),
      ];
      const n = comparison.figures.excluded.unmatched_rows;
      const exclusions = [
        t.zero_blank_excluded > 0 ? plural(t.zero_blank_excluded, "human zero") : null,
        n > 0 ? plural(n, "unmatched row") : null,
      ].filter(Boolean);
      if (exclusions.length > 0) {
        comparisonTiles.push({ label: "not counted", value: String(t.zero_blank_excluded + n) });
        comparisonExcludes = `Not counted: ${exclusions.join(", ")}`;
      }
    }
  }
  const activeNotCompared = !notesActive && humanActive && activeTemplate
    ? comparison?.file.not_compared.find((n) => n.template_id === activeTemplate && n.reason !== "not_in_run") ?? null
    : null;
  // Only the human rows on the worksheet being read.
  const visibleSheets = new Set(notesActive
    ? (activeNotesSheet ? [activeNotesSheet] : [])
    : activeSheet
      ? [activeSheet]
      : activeTemplate ? sheetsByTemplate[activeTemplate] ?? [] : []);
  const unmatchedFigureRows = humanActive
    ? comparison?.file.unmatched.filter((u) => u.kind === "figure" && visibleSheets.has(u.sheet)) ?? []
    : [];
  const tableHuman = humanActive ? humanView : null;

  const totalOpenConflicts = Object.values(conflictCounts).reduce(
    (a, b) => a + b,
    0
  );
  const attentionCount =
    failingChecks.length + totalOpenConflicts;
  const pdfColumn = (
    <div className="review-source-column" style={{ ...styles.column, ...(notesActive ? { overflow: "hidden" } : {}), flex: `0 1 ${pdfWidth}px`, width: pdfWidth, maxWidth: "34%" }}>
      <ColumnHeader
        title="Source PDF"
        testId="pdf"
        onHide={() => setPdfCollapsed(true)}
        inset
      />
      {/* Source-PDF verification: the pane follows the selected concept's
          evidence pages so a reviewer can eyeball the figure against the
          document without leaving the page (M1). `embedded` because the
          column header above already says "Source PDF" and owns Hide. */}
      <div style={notesActive ? { minHeight: 0, overflowY: "auto", scrollbarWidth: "thin" } : undefined}>
      <PdfSourcePane
        runId={runId}
        pages={pdfPages}
        selectionKey={notesActive ? notesPdfSelectionKey : 0}
        embedded
        hasSelection={notesActive ? notesCellSelected : selectedConcept != null}
      />
      </div>
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
          <ConceptEvidenceBody concept={selectedScopedConcept} />
        </CollapsiblePanel>
      )}
    </div>
  );

  return (
    <div data-testid="concepts-page" style={styles.workspace}>
      {comparison && (
        <HumanComparisonBar
          runId={runId}
          file={comparison.file}
          showDetails={humanActive}
          paneSwitch={
            <SegmentedControl
              testId="comparison-pane-toggle"
              values={["Human file", "Source PDF"] as const}
              activeValue={comparisonPane === "human" ? "Human file" : "Source PDF"}
              onChange={(value) => setComparisonPane(value === "Human file" ? "human" : "pdf")}
              buttonTestId={(value) => `comparison-pane-${value === "Human file" ? "human" : "pdf"}`}
            />
          }
          tiles={comparisonTiles}
          excludesNote={comparisonExcludes}
          onReplace={onReplaceHumanFile}
          onRemoved={onHumanFileRemoved}
        />
      )}
      <div ref={workspaceRef} className="review-workspace" style={styles.shell}>
      {/* Results + concept grid (always visible, flexes to fill).
          Sits directly beside the Source PDF so a value and the document page
          it came from are adjacent. Sheet selection and attention are compact
          controls here instead of a repeated second sidebar. */}
      {!notesActive && railFolded && (
        <CollapsedRail label="Statements" testId="statements" onExpand={() => setRailOpen(true)} />
      )}
      {!notesActive && !railFolded && <aside className="review-template-rail" aria-label="Figure template navigator"
        style={styles.templateRail}>
        {humanActive
          ? <ColumnHeader title="mTool worksheets" testId="statements" onHide={() => setRailOpen(false)} />
          : <div style={styles.columnHeader}><strong style={ui.sectionTitle}>mTool worksheets</strong></div>}
            <div className="review-sheet-picker-group" style={styles.controlGroup}>
              <label htmlFor="review-sheet-picker" style={ui.fieldLabel}>
                Statement
              </label>
              <select
                id="review-sheet-picker"
                data-testid="review-sheet-picker"
                value={`${activeTemplate ?? ""}::${activeSheet ?? ""}`}
                onChange={(event) => {
                  const [templateId, sheet = ""] = event.target.value.split("::");
                  setSearchQuery("");
                  setActiveTemplate(templateId || null);
                  setActiveSheet(sheet || null);
                }}
                style={ui.select}
              >
                {templates.flatMap((templateId) => {
                  const sheets = sheetsByTemplate[templateId] ?? [];
                  const label = templateDisplayName(templateId);
                  const subtitle = templateSubtitle(templateId);
                  return [
                    <option key={`${templateId}:all`} value={`${templateId}::`}>
                      {subtitle
                        ? `${label} · ${sheets.length > 1 ? `All ${subtitle.toLowerCase()} rows` : subtitle}`
                        : label}
                    </option>,
                    ...(sheets.length > 1 ? sheets : []).map((sheet) => (
                      <option key={`${templateId}:${sheet}`} value={`${templateId}::${sheet}`}>
                        {label} · {figureSheetDisplayName(sheet)}
                      </option>
                    )),
                  ];
                })}
              </select>
            </div>
            <div style={styles.searchGroup}>
              <label htmlFor="concept-search" style={styles.visuallyHidden}>
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
        {searchQuery.trim() && <nav aria-label="Matching figure fields" style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          {concepts.filter((row) => `${row.canonical_label} ${row.display_label ?? ""}`.toLowerCase().includes(searchQuery.trim().toLowerCase())).map((row) => (
            <button type="button" key={`${row.concept_uuid}:${row.render_sheet}:${row.render_row}`} style={{ ...ui.buttonGhost, textAlign: "left", whiteSpace: "normal" }}
              onClick={() => handleSelectConcept(row.concept_uuid)}>{row.display_label || row.canonical_label} · {figureSheetDisplayName(row.render_sheet)}</button>
          ))}
        </nav>}
        <nav aria-label="Figure worksheets" style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          {templates.flatMap((templateId) => (sheetsByTemplate[templateId] ?? []).map((sheet) => (
            <button type="button" key={`${templateId}:${sheet}`} aria-current={activeTemplate === templateId && (activeSheet === sheet || activeSheet == null) ? "true" : undefined}
              className={uiClass.btnQuiet}
              style={activeTemplate === templateId && (activeSheet === sheet || activeSheet == null) ? styles.worksheetItemActive : styles.worksheetItem}
              onClick={() => { setSearchQuery(""); setRowFilter("all"); setActiveTemplate(templateId); setActiveSheet(sheet); }}>
              {templateDisplayName(templateId)} · {figureSheetDisplayName(sheet)}
            </button>
          )))}
        </nav>
      </aside>}
      <section aria-label="Review results" style={{ ...styles.resultsCol, ...(!notesActive && !railFolded ? styles.resultsColDivided : {}) }}>
        {loadError && (
          <div style={styles.errorBanner}>
            Failed to load concepts: {loadError}
          </div>
        )}

        {/* Both toolbar controls only apply to figure sheets, so on a
            notes sheet the whole card is skipped — rendering the shell
            with its children hidden painted an empty white box between
            the outcome strip and the notes editor (run-168 QA finding). */}
        {!notesActive && (
          <section style={styles.toolbar} aria-label="Review controls">
            {isGroupRun && (
              <div style={styles.inlineControlGroup}>
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

            <div style={styles.inlineControlGroup}>
              <label htmlFor="concept-row-filter" style={ui.fieldLabel}>
                Rows
              </label>
              <select
                id="concept-row-filter"
                data-testid="row-filter"
                value={comparison || !rowFilter.startsWith("human_") ? rowFilter : "all"}
                onChange={(e) => setRowFilter(e.target.value as RowFilter)}
                style={ui.select}
              >
                {ROW_FILTERS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                    {option.value === "no_source" && noSourceCount > 0
                      ? ` (${noSourceCount})`
                      : ""}
                  </option>
                ))}
                {comparison && HUMAN_ROW_FILTERS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                    {humanFilterCounts[option.value] != null ? ` (${humanFilterCounts[option.value]})` : ""}
                  </option>
                ))}
              </select>
            </div>
            {/* Announced for screen readers; visually the table itself shows the rows. */}
            <span style={styles.visuallyHidden} role="status" aria-live="polite">
              {filtered.length} figure row{filtered.length === 1 ? "" : "s"} shown
            </span>
            {editedCount > 0 && (
              <span data-testid="edited-values-summary" style={styles.editedValuesSummary}>
                {editedCount} saved edit{editedCount === 1 ? "" : "s"} · included in the download;
                re-running extraction overwrites {editedCount === 1 ? "it" : "them"}
              </span>
            )}
            <button type="button" style={{ ...ui.buttonGhost, marginLeft: "auto" }} disabled={figureIssueRows.length === 0}
              onClick={() => {
                const current = figureIssueRows.findIndex((row) => row.concept_uuid === selectedConceptUuid);
                const next = figureIssueRows[(current + 1) % figureIssueRows.length];
                if (next) handleSelectConcept(next.concept_uuid);
              }}>Next issue</button>
            {attentionCount > 0 && (
              <button
                type="button"
                data-testid="review-attention-control"
                style={styles.attentionControl}
                title={`${attentionCount} item${attentionCount === 1 ? "" : "s"} need attention`}
                aria-expanded={attentionOpen}
                onClick={() => setAttentionOpen((open) => !open)}
              >
                <span aria-hidden="true">!</span> {attentionCount}
              </button>
            )}
          </section>
        )}

        {!notesActive && activeTemplate?.includes("socf") && <details style={{ margin: "8px 0", fontSize: 13, color: pwc.grey700 }}>
          <summary>Cash-flow calculation notes</summary>
          <p>Template subtotals can differ from the source presentation when a payment is adjusted before a subtotal and deducted later.
            Check the payment’s source evidence and final cash totals before accepting the difference.</p>
        </details>}

        {!notesActive && attentionOpen && attentionCount > 0 && (
          <section data-testid="review-attention-panel" style={styles.attentionPanel}>
            {failingChecks.length > 0 && (
              <div>
                <h2 style={styles.attentionHeading}>Checks needing attention</h2>
                <ul style={styles.attentionList}>
                  {failingChecks.map((check, index) => {
                    const canSelect = Boolean(check.target_sheet && check.target_row != null);
                    return (
                      <li key={`${check.name}:${index}`}>
                        <button
                          type="button"
                          data-testid={`review-attention-check-${index}`}
                          style={styles.attentionItem}
                          disabled={!canSelect}
                          onClick={() => {
                            if (canSelect) {
                              handleSelectTarget(check.target_sheet as string, check.target_row as number);
                            }
                          }}
                        >
                          {check.message || check.name}
                        </button>
                      </li>
                    );
                  })}
                </ul>
              </div>
            )}
            {totalOpenConflicts > 0 && (
              <div>
                <h2 style={styles.attentionHeading}>Figures to reconcile</h2>
                <ReconciliationQueue
                  runId={runId}
                  reloadKey={conflictReloadKey}
                  onSelectConcept={handleSelectConcept}
                  onConflictResolved={() => setConflictReloadKey((key) => key + 1)}
                  embedded
                />
              </div>
            )}
          </section>
        )}

        {activeNotCompared && (
          <p style={{ ...ui.metadata, margin: `0 0 ${pwc.space.md}px` }} data-testid="human-not-compared">
            {activeNotCompared.reason === "different_variant"
              ? `Not compared · human file uses another layout${activeNotCompared.human_variant ? ` (${activeNotCompared.human_variant})` : ""}`
              : "Not compared · empty in the human file"}
          </p>
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
              onPreparationBlocked={onPreparationBlocked}
              onComparisonChange={() => setConflictReloadKey((key) => key + 1)}
              humanFigures={humanActive ? humanView?.slots : null}
              human={humanActive && comparison ? {
                html: comparison.notes.human_html,
                status: Object.fromEntries(comparison.notes.fields.map((f) => [f.concept_uuid, f.status])),
              } : null}
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
            human={tableHuman}
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
            human={tableHuman}
          />
        )}
        {unmatchedFigureRows.length > 0 && (
          <details data-testid="human-unmatched-rows" style={styles.unmatchedList}>
            <summary style={styles.unmatchedSummary}>
              {unmatchedFigureRows.length} human row{unmatchedFigureRows.length === 1 ? "" : "s"} with no matching field
            </summary>
            <ul style={styles.attentionList}>
              {unmatchedFigureRows.map((u) => (
                <li key={`${u.sheet}:${u.row}`} style={styles.unmatchedItem}>
                  <span>{u.label || `Row ${u.row}`}</span>
                  <span style={styles.panelMuted}>Row {u.row}</span>
                  <span style={{ fontVariantNumeric: "tabular-nums" }}>
                    {Object.values(u.values ?? {}).map((v) => formatAccounting(v)).join(" · ")}
                  </span>
                </li>
              ))}
            </ul>
          </details>
        )}
      </section>

      {/* Column 3 — Source PDF, docked on the right so the value grid and its
          source page sit side by side. The resize handle is on the PDF's LEFT
          edge now, so a rightward drag shrinks it — delta sign is flipped
          relative to the Menu handle on the far left. */}
      {humanActive ? null : pdfCollapsed || (pdfMissing && !showMissingPdf) ? (
        <CollapsedRail
          label="Source PDF"
          testId="pdf"
          onExpand={() => { setPdfCollapsed(false); setShowMissingPdf(true); }}
        />
      ) : (
        <>
          <ResizableDivider
            testId="resize-pdf"
            label="Resize source PDF panel"
            onDelta={(dx) => setPdfWidth((w) => clamp(w - dx, Math.min(260, pdfMaxWidthRef.current), pdfMaxWidthRef.current))}
          />
          {pdfColumn}
        </>
      )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Layout primitives for the review workspace — a generic collapsible panel,
// Source-PDF hide header / collapsed rail, and a drag-to-resize handle.
// ---------------------------------------------------------------------------

function clamp(v: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, v));
}

function CollapsiblePanel({
  title,
  testId,
  defaultOpen = true,
  openWhen = false,
  keepMounted = false,
  children,
}: {
  title: string;
  testId?: string;
  defaultOpen?: boolean;
  /** Open when an important async result arrives after the panel mounted. */
  openWhen?: boolean;
  keepMounted?: boolean;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  useEffect(() => {
    if (openWhen) setOpen(true);
  }, [openWhen]);
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
          className="review-panel-chevron"
          style={{
            ...styles.panelChevron,
            transform: open ? "none" : "rotate(-90deg)",
          }}
        >
          <ExpandMore size={20} />
        </span>
      </button>
      {(open || keepMounted) && (
        <div style={{ ...styles.panelBody, display: open ? undefined : "none" }}>
          {children}
        </div>
      )}
    </section>
  );
}

function ColumnHeader({
  title,
  testId,
  onHide,
  inset = false,
}: {
  title: string;
  testId: string;
  onHide: () => void;
  /** Align the title with a padded pane body (the Source PDF card) so it sits
   *  the same distance from the divider as the other pane titles. */
  inset?: boolean;
}) {
  return (
    <div style={inset ? { ...styles.columnHeader, paddingInline: pwc.space.lg } : styles.columnHeader}>
      <span style={styles.columnHeaderTitle}>{title}</span>
      <button
        type="button"
        data-testid={`col-hide-${testId}`}
        onClick={onHide}
        style={styles.columnHideBtn}
        title={`Hide ${title} panel`}
        aria-label={`Hide ${title} panel`}
      >
        <LeftPanelClose size={20} style={title === "Source PDF" ? { transform: "scaleX(-1)" } : undefined} />
        <span>Hide</span>
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
  return (
    <button
      type="button"
      data-testid={`col-show-${testId}`}
      onClick={onExpand}
      className="review-collapsed-rail"
      style={styles.collapsedRail}
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
  human = null,
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
  /** Human-file values shown as extra columns on the same rows. */
  human?: HumanView | null;
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
      className="concept-tree-shell"
      style={styles.tableShell}
    >
      <div
        role="row"
        className="concept-tree-header"
        data-human={human ? "true" : undefined}
        style={{ ...styles.treeHeaderRow, gridTemplateColumns: treeColumns(showPeriods, human != null), ...(human ? { gap: pwc.space.lg } : null) }}
      >
        {/* "Line item" (accountant vocabulary), not the internal "Concept"
            codename — plain-language rule (CLAUDE.md "talk like a product
            person"). Numeric column headers right-align over their figures. */}
        <div role="columnheader" style={styles.headerCell}>Line item</div>
        {human ? <>
          {/* AI periods first, then the human's: AI CY, AI PY, Human CY, Human PY. */}
          <div role="columnheader" style={styles.headerCellNumeric} title={showPeriods ? cyLabel : undefined}>{comparisonHeader("AI", showPeriods ? cyLabel : null)}</div>
          {showPeriods && <div role="columnheader" style={styles.headerCellNumeric} title={pyLabel}>{comparisonHeader("AI", pyLabel)}</div>}
          <div role="columnheader" className="human-divider" style={styles.headerCellNumeric} title={showPeriods ? cyLabel : undefined}>{comparisonHeader("Human", showPeriods ? cyLabel : null)}</div>
          {showPeriods && <div role="columnheader" style={styles.headerCellNumeric} title={pyLabel}>{comparisonHeader("Human", pyLabel)}</div>}
        </> : <>
          <div role="columnheader" style={styles.headerCellNumeric}>{showPeriods ? cyLabel : "Value"}</div>
          {showPeriods && <div role="columnheader" style={styles.headerCellNumeric}>{pyLabel}</div>}
        </>}

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
          human={human}
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
  human = null,
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
  /** Human-file values: one extra column after each value column. */
  human?: HumanView | null;
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
    templateId: string;
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
        templateId: r.template_id,
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
  // Per component: AI periods first, then the human's (AI CY, AI PY, Human CY, Human PY).
  const valueColumns = cols.flatMap((c) => [
    ...visiblePeriods.map((period) => ({ col: c, period, isHuman: false })),
    ...(human ? visiblePeriods.map((period) => ({ col: c, period, isHuman: true })) : []),
  ]);
  const columnsPerComponent = visiblePeriods.length * (human ? 2 : 1);
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
          fontWeight: 400,
          fontSize: 14,
          borderBottom: `1px solid ${pwc.grey200}`,
        }}
      >
        <div
          style={{
            ...styles.matrixHeaderMovement,
            gridRow: showPeriods || human ? "1 / span 2" : undefined,
          }}
        >
          Movement
        </div>
        {cols.map((col, idx) => {
          const label = colLabels.get(col) || col;
          const start = 2 + idx * columnsPerComponent;
          return (
            <div
              key={col}
              style={{
                ...styles.matrixComponentHeader,
                gridColumn: columnsPerComponent > 1
                  ? `${start} / span ${columnsPerComponent}`
                  : undefined,
              }}
              title={`Column ${col}: ${label}`}
            >
              {label}
            </div>
          );
        })}
        {(showPeriods || human) && valueColumns.map(({ col, period, isHuman }) => (
          <div
            key={`${col}-${period}${isHuman ? "-human" : ""}`}
            style={{
              ...styles.matrixPeriodHeader,
              ...(human ? { borderLeft: "none" } : null),
              ...(isHuman && period === visiblePeriods[0] ? styles.matrixHumanDivider : null),
            }}
            title={`${col} ${period}`}
          >
            {human
              ? comparisonHeader(isHuman ? "Human" : "AI", showPeriods ? (period === "CY" ? cyLabel : pyLabel) : null)
              : showPeriods ? (period === "CY" ? cyLabel : pyLabel) : "Value"}
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
                padding: `${pwc.space.xs}px ${pwc.space.md}px`,
                background: pwc.grey50,
                fontFamily: pwc.fontBody,
                fontSize: 14,
                fontWeight: 500,
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
              fontSize: 14,
              alignItems: "center",
            }}
          >
            <div style={styles.matrixMovementCell}>
              {g.label}
            </div>
            {valueColumns.map(({ col, period, isHuman }) => {
              const cell = g.cells.get(col);
              if (isHuman && human) {
                return (
                  <div key={`${col}-${period}-human`} style={{
                    padding: `${pwc.space.xs}px ${pwc.space.sm}px`,
                    minWidth: 0,
                    // One divider before each component's human columns.
                    ...(period === visiblePeriods[0] ? styles.matrixHumanDivider : null),
                  }}>
                    <HumanValueCell
                      slot={cell ? human.slots.get(humanSlotKey(cell.uuid, period, activeScope)) : undefined}
                      notCompared={human.notComparedTemplates.has(g.templateId)}
                      testId={`matrix-human-${rn}-${col}-${period}`}
                    />
                  </div>
                );
              }
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
                    background: selected ? pwc.grey50 : "transparent",
                    cursor: cell ? "pointer" : "default",
                  }}
                >
                  {cell && cell.editable ? (
                    <EditableValueCell
                      uuid={cell.uuid}
                      ariaLabel={`${g.label}, ${colLabels.get(col) || col}, ${activeScope}, ${period === "CY" ? "current" : "prior"} period`}
                      value={cell.values[period]}
                      onEditValue={onEditValue}
                      status={editStatus[valueEditKey(cell.uuid, period)]}
                      period={showPeriods ? period : undefined}
                      scope={activeScope}
                      highlight={highlightEmpty}
                      compact
                    />
                  ) : cell ? (
                    <ReadOnlyValue value={cell.values[period]} />
                  ) : null}
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
  human = null,
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
  human?: HumanView | null;
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
  const cyScopedRow = conceptForScope(row, activeScope, "CY");
  const cyValue = periodValue(row, activeScope, "CY");
  const pyValue = periodValue(row, activeScope, "PY");
  const cyIncompleteMandatory = isMandatory && isBlankValue(cyValue);
  const pyIncompleteMandatory = isMandatory && isBlankValue(pyValue);
  const cyStatus = editStatus[valueEditKey(row.concept_uuid, "CY")];
  const pyStatus = editStatus[valueEditKey(row.concept_uuid, "PY")];
  const hasConflict = cyScopedRow.value_status === "conflict" ||
    (showPeriods && conceptForScope(row, activeScope, "PY").value_status === "conflict");

  return (
    <div
      ref={rowRef}
      className="concept-tree-row"
      data-testid={`concept-row-${row.concept_uuid}`}
      data-kind={row.kind}
      // Section headers aren't selectable — they carry no value, so clicking
      // one used to select a row the PDF pane / details panel could do
      // nothing with. Data rows keep the click-to-select behaviour.
      tabIndex={isAbstract ? undefined : 0}
      role={isAbstract ? undefined : "treeitem"}
      aria-selected={isAbstract ? undefined : selected}
      data-human={human ? "true" : undefined}
      onKeyDown={(event) => {
        if (event.target === event.currentTarget && !isAbstract && (event.key === "Enter" || event.key === " ")) {
          event.preventDefault(); onSelectRow(row.concept_uuid);
        }
      }}
      onClick={isAbstract ? undefined : () => onSelectRow(row.concept_uuid)}
      style={{
        display: "grid",
        gridTemplateColumns: isAbstract
          ? "minmax(0, 1fr)"
          : treeColumns(showPeriods, human != null),
        gap: pwc.space.lg,
        minWidth: 0,
        // Section headers are visually distinct from data rows: tighter,
        // smaller, semibold caption on a grey band — so a run of same-named
        // taxonomy headers ("Statement of cash flows" nested three deep)
        // reads as structure, not as three broken data rows (run-168
        // design critique).
        padding: `${pwc.space.xs}px ${pwc.space.xl}px`,
        background: isAbstract
          ? pwc.grey100
          : selected
          ? pwc.grey50
          : pwc.white,
        borderBottom: `1px solid ${pwc.grey100}`,
        fontFamily: pwc.fontBody,
        fontSize: 14,
        fontWeight: isAbstract ? pwc.weight.semibold : pwc.weight.regular,
        letterSpacing: isAbstract ? 0.4 : undefined,
        textTransform: isAbstract ? ("uppercase" as const) : undefined,
        color: isAbstract ? pwc.grey700 : pwc.grey900,
        cursor: isAbstract ? "default" : "pointer",
        alignItems: "center",
      }}
    >
      <div
        className="concept-tree-label"
        title={
          isAlias
            ? `canonical: ${row.canonical_label} — linked to value from another sheet`
            : `canonical: ${row.canonical_label}`
        }
        style={{
          paddingLeft: depth * 14,
          minWidth: 0,
          overflowWrap: "anywhere",
          display: "flex",
          flexDirection: "column",
          gap: 2,
          lineHeight: 1.4,
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
                fontSize: 14,
                fontStyle: "italic",
                color: pwc.grey700,
                fontWeight: pwc.weight.regular,
              }}
            >
              (linked)
            </span>
          )}
          {selected && rowHasOpenableSource(cyScopedRow) && (
            <button
              type="button"
              className="concept-source-jump"
              style={{ ...styles.sourceJump, marginLeft: pwc.space.sm }}
              aria-label={`Open source for ${label}`}
              onClick={(event) => {
                event.stopPropagation();
                onSelectRow(row.concept_uuid);
              }}
            >
              {displayConceptSource(cyScopedRow)}
            </button>
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
        {rowLacksSource(cyScopedRow) && (
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
                testId={
                  showPeriods
                    ? `readonly-value-${row.concept_uuid}-CY`
                    : `readonly-value-${row.concept_uuid}`
                }
              />
            ) : isEditable ? (
              <EditableValueCell
                uuid={row.concept_uuid}
                ariaLabel={`${label}, ${activeScope}, current period`}
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
                  testId={`readonly-value-${row.concept_uuid}-PY`}
                />
              ) : isEditable ? (
                <EditableValueCell
                  uuid={row.concept_uuid}
                  ariaLabel={`${label}, ${activeScope}, prior period`}
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
                  testId={`readonly-value-${row.concept_uuid}-PY`}
                />
              )}
            </div>
          )}
          {human && (
            <div className="human-divider">
              <HumanValueCell
                slot={human.slots.get(humanSlotKey(row.concept_uuid, "CY", activeScope))}
                notCompared={human.notComparedTemplates.has(row.template_id)}
                testId={`human-value-${row.concept_uuid}-CY`}
              />
            </div>
          )}
          {human && showPeriods && (
            <HumanValueCell
              slot={human.slots.get(humanSlotKey(row.concept_uuid, "PY", activeScope))}
              notCompared={human.notComparedTemplates.has(row.template_id)}
              testId={`human-value-${row.concept_uuid}-PY`}
            />
          )}
          {hasConflict && <div role="alert" style={{ ...styles.stateCell, gridColumn: "1 / -1" }}>
            <StatusBadge label="Conflicting values" tone="error" />
          </div>}
        </>
      )}
    </div>
  );
}

function ReadOnlyValue({
  value,
  testId,
}: {
  value: number | null | undefined;
  testId?: string;
}) {
  // Keep numeric alignment without presenting a read-only value as an input.
  return (
    <span
      data-testid={testId}
      title={value == null ? "Empty read-only value" : "Read-only value"}
      style={{ ...styles.readonlyValue, color: value == null ? pwc.grey500 : pwc.grey900, fontSize: value == null ? 12 : 14 }}
    >
      {value == null ? <>
        <span aria-hidden="true">—</span>
        <span style={styles.visuallyHidden}>Empty read-only value</span>
      </> : formatAccounting(value)}
    </span>
  );
}

/** The human's value for one slot. Only a difference carries a marker:
 *  ! different value, ○ missed by AI, ◇ AI-only. A human zero opposite an AI
 *  blank and a total are shown muted. Blank when the human file has
 *  nothing there. */
function HumanValueCell({
  slot,
  notCompared,
  testId,
}: {
  slot: HumanFigureSlot | undefined;
  notCompared: boolean;
  testId?: string;
}) {
  if (notCompared || !slot || slot.human_value == null && slot.status !== "ai_only") {
    return <span data-testid={testId} aria-hidden="true" style={styles.humanEmpty} />;
  }
  // Agreement is the normal case and carries no marker; only exceptions do.
  // A zero opposite a blank is not a difference worth a marker.
  return (
    <span data-testid={testId} data-human-status={slot.status}
      style={{
        ...styles.humanCell,
        ...(slot.status === "zero_blank" ? styles.humanQuiet : null),
      }}
      title={HUMAN_STATUS_LABEL[slot.status]}>
      <HumanStatusMarker status={slot.status} />
      <span>{slot.human_value == null ? "" : formatAccounting(slot.human_value)}</span>
    </span>
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
    <div data-testid={testId} role="tablist" className="segmented-control-group" style={styles.segmented}>
      {values.map((value, index) => {
        const active = value === activeValue;
        return (
          <button
            key={value}
            type="button"
            role="tab"
            aria-selected={active}
            data-testid={buttonTestId(value)}
            className="segmented-control-button"
            onClick={() => onChange(value)}
            style={{
              ...styles.segmentedButton,
              borderRight: index < values.length - 1 ? `1px solid ${pwc.grey200}` : "none",
              fontWeight: active ? 600 : 500,
              background: active ? pwc.black : pwc.white,
              color: active ? pwc.white : pwc.grey700,
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
      <StatusIcon symbol={symbol} size={12} />
      {label.replace(/_/g, " ")}
    </span>
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
          <div>
            <div style={styles.evidenceLabel}>Value origin</div>
            <div style={styles.evidenceText}>{describeValueOrigin(concept)}</div>
          </div>
          <div style={styles.evidenceGrid}>
            <div>
              <div style={styles.evidenceLabel}>Template</div>
              <div style={styles.evidenceText}>{templateDisplayName(concept.template_id)}</div>
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
              {displayConceptSource(concept) || (concept.source?.trim().toLowerCase() === "cascade" ? "See component figures for source pages" : "No source page recorded")}
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
  ariaLabel,
  value,
  onEditValue,
  status,
  period,
  scope,
  highlight = false,
  compact = false,
}: {
  uuid: string;
  ariaLabel: string;
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
          role={status === "error" ? "alert" : "status"}
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
        aria-label={ariaLabel}
        inputMode="decimal"
        title={status === "saved" ? "Saved — click to edit" : "Click to edit"}
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
          height: 32,
          padding: `${pwc.space.xs}px ${pwc.space.sm}px`,
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
          role={status === "error" ? "alert" : "status"}
          style={{
            fontSize: 12,
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

/** Height of the shared header band at the top of each review column. */
const REVIEW_HEADER_HEIGHT = 44;

const styles = {
  workspace: {
    minWidth: 0,
    width: "100%",
    fontFamily: pwc.fontBody,
  } as React.CSSProperties,
  // Review workspace shell. No flex-wrap: columns keep their row so the
  // resize handles stay between them; the Results column flexes to fill.
  shell: {
    display: "flex",
    minWidth: 0,
    width: "100%",
    alignItems: "flex-start",
    gap: 0,
    fontFamily: pwc.fontBody,
  } as React.CSSProperties,
  column: {
    display: "flex",
    flexDirection: "column" as const,
    gap: pwc.space.lg,
    minWidth: 0,
    position: "sticky" as const,
    top: 116,
    alignSelf: "flex-start",
    maxHeight: "calc(100vh - 116px)",
    overflowY: "auto" as const,
  } as React.CSSProperties,
  // Worksheet navigation stays in view while the figures scroll, like the
  // PDF column and the Notes rail (design system: keep navigation available).
  templateRail: {
    flex: "0 0 240px",
    minWidth: 0,
    paddingRight: pwc.space.md,
    display: "flex",
    flexDirection: "column" as const,
    gap: pwc.space.lg,
    position: "sticky" as const,
    top: 116,
    alignSelf: "flex-start",
    maxHeight: "calc(100vh - 116px)",
    overflowY: "auto" as const,
  } as React.CSSProperties,
  resultsCol: {
    flex: "1 1 460px",
    minWidth: 0,
    display: "flex",
    flexDirection: "column" as const,
    paddingRight: pwc.space.lg,
  } as React.CSSProperties,
  // Same one-pixel Grey 100 rule as the Source PDF divider, so both edges of
  // the figures table look alike.
  resultsColDivided: {
    borderLeft: `1px solid ${pwc.grey100}`,
    paddingLeft: pwc.space.lg,
  } as React.CSSProperties,
  // Every review column opens with a header band of this height, so the
  // column titles and the Rows filter share one line and the search box,
  // table and PDF card below them all start at the same height.
  columnHeader: {
    ...ui.reviewPaneHeader,
    minHeight: REVIEW_HEADER_HEIGHT,
  } as React.CSSProperties,
  columnHeaderTitle: {
    fontFamily: pwc.fontHeading,
    fontSize: 16,
    fontWeight: 600,
    color: tokens.color.text.primary,
    whiteSpace: "nowrap" as const,
  } as React.CSSProperties,
  columnHideBtn: {
    ...ui.iconButton,
    gap: pwc.space.xs,
    minHeight: 34,
    padding: `0 ${pwc.space.sm}px`,
    fontSize: 13,
    fontWeight: 500,
    color: tokens.color.text.secondary,
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
  } as React.CSSProperties,
  collapsedRailChevron: {
    fontSize: 14,
    lineHeight: 1,
    fontWeight: 500,
  } as React.CSSProperties,
  collapsedRailLabel: {
    writingMode: "vertical-rl" as const,
    transform: "rotate(180deg)",
    fontFamily: pwc.fontHeading,
    fontSize: 12,
    fontWeight: 500,
    letterSpacing: 0,
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
    fontSize: 14,
    fontWeight: 600,
    color: tokens.color.text.primary,
  } as React.CSSProperties,
  panelChevron: {
    display: "inline-flex",
    color: tokens.color.icon.rest,
  } as React.CSSProperties,
  panelBody: {
    padding: pwc.space.lg,
  } as React.CSSProperties,
  errorBanner: {
    marginBottom: pwc.space.md,
    padding: `${pwc.space.sm}px ${pwc.space.md}px`,
    background: pwc.orange50,
    border: "none",
    borderRadius: pwc.radius.sm,
    color: pwc.grey800,
    fontSize: 13,
    lineHeight: 1.5,
  } as React.CSSProperties,
  toolbar: {
    minHeight: REVIEW_HEADER_HEIGHT,
    marginBottom: pwc.space.lg,
    display: "flex",
    flexWrap: "wrap",
    gap: pwc.space.md,
    alignItems: "center",
    position: "sticky" as const,
    top: 0,
    zIndex: 5,
  } as React.CSSProperties,
  controlGroup: {
    display: "flex",
    flexDirection: "column" as const,
    gap: pwc.space.sm,
  } as React.CSSProperties,
  inlineControlGroup: {
    display: "flex",
    alignItems: "center",
    gap: pwc.space.sm,
  } as React.CSSProperties,
  visuallyHidden: {
    position: "absolute",
    width: 1,
    height: 1,
    padding: 0,
    margin: -1,
    overflow: "hidden",
    clip: "rect(0, 0, 0, 0)",
    whiteSpace: "nowrap",
    border: 0,
  } as React.CSSProperties,
  searchGroup: {
    flex: "0 0 auto",
    minWidth: 0,
    display: "flex",
    flexDirection: "column" as const,
    gap: pwc.space.sm,
  } as React.CSSProperties,
  worksheetItem: {
    ...ui.buttonQuiet,
    justifyContent: "flex-start",
    textAlign: "left" as const,
    whiteSpace: "normal" as const,
    fontWeight: pwc.weight.regular,
    color: tokens.color.text.secondary,
  } as React.CSSProperties,
  worksheetItemActive: {
    ...ui.buttonQuiet,
    justifyContent: "flex-start",
    textAlign: "left" as const,
    whiteSpace: "normal" as const,
    fontWeight: pwc.weight.medium,
    color: tokens.color.text.primary,
    background: tokens.surface.sunken,
  } as React.CSSProperties,
  editedValuesSummary: {
    maxWidth: 280,
    color: pwc.grey700,
    fontSize: 12,
    lineHeight: 1.35,
  } as React.CSSProperties,
  attentionControl: {
    minWidth: 36,
    minHeight: 36,
    padding: `0 ${pwc.space.sm}px`,
    border: `1px solid ${pwc.grey300}`,
    borderRadius: pwc.radius.sm,
    background: pwc.white,
    color: pwc.orange500,
    fontFamily: pwc.fontBody,
    fontSize: 12,
    fontWeight: 500,
    cursor: "pointer",
  } as React.CSSProperties,
  attentionPanel: {
    display: "grid",
    gap: pwc.space.lg,
    margin: `-${pwc.space.sm}px 0 ${pwc.space.lg}px`,
    padding: pwc.space.lg,
    border: `1px solid ${pwc.grey200}`,
    borderRadius: pwc.radius.md,
    background: pwc.white,
  } as React.CSSProperties,
  attentionHeading: {
    margin: `0 0 ${pwc.space.sm}px`,
    color: pwc.grey900,
    fontFamily: pwc.fontHeading,
    fontSize: 13,
    fontWeight: pwc.weight.semibold,
  } as React.CSSProperties,
  attentionList: {
    listStyle: "none",
    margin: 0,
    padding: 0,
  } as React.CSSProperties,
  unmatchedList: {
    marginTop: pwc.space.lg,
    display: "flex",
    flexDirection: "column" as const,
    gap: pwc.space.xs,
  } as React.CSSProperties,
  unmatchedItem: {
    display: "grid",
    gridTemplateColumns: "minmax(0, 2fr) minmax(0, 1fr) auto",
    gap: pwc.space.md,
    padding: `${pwc.space.xs}px 0`,
    borderBottom: `1px solid ${pwc.grey100}`,
    fontSize: 13,
  } as React.CSSProperties,
  // Human values are read-only: same footprint as a calculated value so the
  // AI and human columns line up, without a second tint.
  humanCell: {
    display: "flex",
    alignItems: "center",
    justifyContent: "flex-end",
    gap: pwc.space.xs,
    width: "100%",
    boxSizing: "border-box" as const,
    minWidth: 0,
    minHeight: 32,
    // Read-only text, not a field: no border, so it never looks editable.
    // The right padding matches the AI input's text inset so digits align.
    padding: `${pwc.space.xs}px 9px ${pwc.space.xs}px ${pwc.space.xs}px`,
    fontFamily: pwc.fontBody,
    fontSize: 14,
    fontVariantNumeric: "tabular-nums",
    color: pwc.grey900,
  } as React.CSSProperties,
  humanQuiet: {
    color: pwc.grey500,
  } as React.CSSProperties,
  humanEmpty: {
    display: "inline-block",
    width: "100%",
    height: 32,
  } as React.CSSProperties,
  unmatchedSummary: {
    cursor: "pointer",
    fontFamily: pwc.fontHeading,
    fontSize: 14,
    fontWeight: pwc.weight.medium,
    color: pwc.grey700,
  } as React.CSSProperties,
  attentionItem: {
    width: "100%",
    padding: `${pwc.space.sm}px 0`,
    border: "none",
    background: "transparent",
    color: pwc.grey900,
    fontFamily: pwc.fontBody,
    fontSize: 13,
    textAlign: "left" as const,
    cursor: "pointer",
  } as React.CSSProperties,
  // Same geometry as the filing-setup toggles (PreRunPanel).
  segmented: {
    display: "inline-flex",
    border: `1px solid ${pwc.grey200}`,
    borderRadius: pwc.radius.md,
    overflow: "hidden",
    background: pwc.white,
  } as React.CSSProperties,
  segmentedButton: {
    minHeight: 40,
    padding: "8px 24px",
    border: "none",
    borderRadius: 0,
    cursor: "pointer",
    fontFamily: pwc.fontHeading,
    fontSize: 14,
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
    fontSize: 12,
    fontWeight: 500,
    color: pwc.grey500,
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
    gridTemplateColumns: "minmax(130px, 1fr) minmax(100px, 150px)",
    gap: pwc.space.md,
    minWidth: 0,
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
  // Align calculated and linked values with inputs, without a field boundary.
  readonlyValue: {
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "flex-end",
    width: "100%",
    boxSizing: "border-box",
    minHeight: 32,
    padding: `${pwc.space.xs}px ${pwc.space.sm}px`,
    border: "none",
    background: "transparent",
    cursor: "default",
    fontFamily: pwc.fontBody,
    fontVariantNumeric: "tabular-nums",
    fontSize: 14,
    color: pwc.grey900,
  } as React.CSSProperties,
  stateCell: {
    display: "flex",
    alignItems: "center",
    minWidth: 0,
  } as React.CSSProperties,
  // Small caption under a mandatory line item whose value is still blank —
  // names the reason the input is highlighted orange.
  requiredChip: {
    fontSize: 14,
    color: pwc.warningText,
    fontWeight: pwc.weight.medium,
  } as React.CSSProperties,
  // No-source badge + its filter toggle (UX-QA #6).
  noSourceChip: {
    fontSize: 14,
    color: pwc.warningText,
    fontWeight: pwc.weight.medium,
  } as React.CSSProperties,
  sourceJump: {
    padding: 0,
    border: 0,
    background: "transparent",
    color: pwc.grey500,
    font: "inherit",
    fontSize: 14,
    textDecoration: "none",
    cursor: "pointer",
    textAlign: "left" as const,
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
  matrixHumanDivider: {
    alignSelf: "stretch",
    display: "flex",
    alignItems: "center",
    justifyContent: "flex-end",
    borderLeft: `1px solid ${pwc.grey200}`,
  } as React.CSSProperties,
  matrixPeriodHeader: {
    padding: `${pwc.space.xs}px ${pwc.space.sm}px`,
    textAlign: "right" as const,
    color: pwc.grey500,
    fontFamily: pwc.fontHeading,
    fontSize: 14,
    fontWeight: 400,
    borderLeft: `1px solid ${pwc.grey200}`,
  } as React.CSSProperties,
  matrixMovementCell: {
    padding: `${pwc.space.xs}px ${pwc.space.md}px`,
    position: "sticky" as const,
    left: 0,
    zIndex: 2,
    background: pwc.white,
    borderRight: `1px solid ${pwc.grey100}`,
    lineHeight: 1.35,
  } as React.CSSProperties,
};
