import { useEffect, useState, useCallback, useRef } from "react";
import { pwc } from "../lib/theme";
import { ui } from "../lib/uiStyles";
import { ReconciliationQueue } from "../components/ReconciliationQueue";
import { NotesReviewTab } from "../components/NotesReviewTab";
import { downloadFilledUrl } from "../lib/api";
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

// Accountant-style display: thousands separators, parentheses for negatives,
// blank for null. Used for read-only cells (COMPUTED totals, matrix cells);
// the editable LEAF input keeps the raw number so typing isn't fought. Phase 3.1.
export function formatAccounting(n: number | null | undefined): string {
  if (n == null) return "";
  const abs = Math.abs(n);
  const s = abs.toLocaleString("en-US", { maximumFractionDigits: 2 });
  return n < 0 ? `(${s})` : s;
}

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
  // Phase 4 — per-(scope, period) facts for Group runs.
  // Shape: {Company: {CY: 100, PY: 110}, Group: {CY: 200, PY: 220}}.
  // Absent (or single-scope) on Company runs.
  scope_facts?: Record<string, Record<string, number | null>>;
}

export interface ConceptsPageProps {
  // Null when the Concepts top-nav tab is opened without a run selected —
  // the page then shows a "pick a run" empty state instead of fetching.
  runId: number | null;
}

type Period = "CY" | "PY";

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

function treeColumns(showPeriods: boolean): string {
  return showPeriods
    ? "minmax(260px, 1fr) minmax(130px, 160px) minmax(130px, 160px) 120px minmax(120px, 180px)"
    : "minmax(260px, 1fr) minmax(150px, 190px) 120px minmax(120px, 180px)";
}

export function ConceptsPage({ runId }: ConceptsPageProps) {
  const [concepts, setConcepts] = useState<ConceptRow[]>([]);
  const [activeTemplate, setActiveTemplate] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  // Phase 2 (step 2.10): cross-template search.  We keep the search
  // index in the page rather than re-querying so multi-statement
  // navigation stays snappy on slow networks.
  const [searchQuery, setSearchQuery] = useState("");
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
  const [selectedConceptUuid, setSelectedConceptUuid] = useState<string | null>(
    null
  );
  // Abort an in-flight recheck on unmount so its response can't setState on a
  // gone component (parity with the other fetches on this page).
  const recheckAbort = useRef<AbortController | null>(null);
  useEffect(() => () => recheckAbort.current?.abort(), []);

  // Initial load.  Peer-review #11: abort the in-flight request on
  // unmount / runId change so a slow response can't land on a stale
  // component or clobber a newer run's data.
  useEffect(() => {
    if (runId == null) return;
    const controller = new AbortController();
    fetch(`/api/runs/${runId}/concepts`, { signal: controller.signal })
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((data) => {
        setConcepts(data.concepts || []);
        const firstTemplate = (data.concepts || [])[0]?.template_id || null;
        setActiveTemplate(firstTemplate);
      })
      .catch((err) => {
        // AbortError is expected on cleanup — don't surface it.
        if (err?.name === "AbortError") return;
        setLoadError(String(err));
      });
    return () => {
      controller.abort();
    };
  }, [runId]);

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
        const resp = await fetch(
          `/api/runs/${runId}/facts/${concept_uuid}`,
          {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              value,
              period,
              entity_scope,
            }),
            keepalive,
          }
        );
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
        // cascade — refresh the reconciliation queue.
        setConflictReloadKey((k) => k + 1);
      } catch (err) {
        if (keepalive) return;
        setEditStatus((s) => ({ ...s, [editKey]: "error" }));
      }
    },
    [runId, activeScope]
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
      const results: Array<{ status: string }> = data.results || [];
      // Backend statuses are "passed" / "failed" / "warning" /
      // "not_applicable" / "pending" (cross_checks.framework). Match those
      // exactly — "pass"/"fail" would always count 0.
      const passed = results.filter((r) => r.status === "passed").length;
      const failed = results.filter((r) => r.status === "failed").length;
      const warnings = results.filter((r) => r.status === "warning").length;
      setRecheck({
        running: false,
        summary: `${passed} passed · ${failed} failed · ${warnings} warnings`,
      });
    } catch (err) {
      if ((err as { name?: string })?.name === "AbortError") return;
      setRecheck({ running: false, summary: "Re-check failed" });
    }
  }, [runId]);

  // Distinct templates for the selector dropdown — order-preserving so
  // SOFP appears before SOPL/SOCI/SOCF when a run carries all four.
  const templates: string[] = [];
  for (const c of concepts) {
    if (!templates.includes(c.template_id)) templates.push(c.template_id);
  }

  // Search overrides the template filter (matches happen across all
  // templates so a user can hop between statements via the result
  // list).  Empty query falls back to the active-template view.
  const notesActive = activeTemplate === NOTES_KEY;
  const q = searchQuery.trim().toLowerCase();
  const baseRows = q
    ? concepts.filter((c) => {
        const canon = c.canonical_label.toLowerCase();
        const disp = (c.display_label || "").toLowerCase();
        return canon.includes(q) || disp.includes(q);
      })
    : activeTemplate
    ? concepts.filter((c) => c.template_id === activeTemplate)
    : concepts;

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

  const filtered = baseRows;

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
    setSelectedConceptUuid(firstDataRow.concept_uuid);
  }, [notesActive, filtered, selectedConceptUuid]);

  const selectedConcept =
    selectedConceptUuid == null
      ? null
      : filtered.find((r) => r.concept_uuid === selectedConceptUuid) || null;

  const editableCount = concepts.filter((c) => c.editable === true).length;
  const shownCount = filtered.filter((c) => c.kind !== "ABSTRACT").length;

  if (runId == null) {
    // No run selected → this surface becomes the global template settings
    // (master-template label editing), separate from per-run value review
    // (Phase 5.1 / 5.3). The "pick a run" guidance moves into the panel.
    return (
      <div data-testid="concepts-page-empty">
        <div
          style={{
            padding: `${pwc.space.md}px ${pwc.space.xl}px 0`,
            color: pwc.grey700,
            fontSize: 13,
          }}
        >
          Open a run from the History tab to review its extracted values. Below,
          you can rename template field labels that apply to all runs.
        </div>
        <TemplateSettingsPage />
      </div>
    );
  }

  return (
    <div
      data-testid="concepts-page"
      style={{
        display: "flex",
        flexWrap: "wrap",
        gap: pwc.space.xl,
        alignItems: "start",
        fontFamily: pwc.fontBody,
      }}
    >
      <main style={{ minWidth: 0, flex: "1 1 760px" }}>
        <section style={styles.reviewHeader}>
          <div style={styles.titleRow}>
            <div>
              <div style={styles.kicker}>Post-run review</div>
              <h1 style={styles.pageTitle}>Review extracted values</h1>
            </div>
            <div style={styles.actionRow}>
              {recheck.summary && (
                <span data-testid="recheck-summary" style={styles.recheckSummary}>
                  {recheck.summary}
                </span>
              )}
              <button
                data-testid="recheck-btn"
                onClick={onRecheck}
                disabled={recheck.running}
                style={{
                  ...ui.buttonSecondary,
                  cursor: recheck.running ? "default" : "pointer",
                  opacity: recheck.running ? 0.7 : 1,
                }}
              >
                {recheck.running ? "Checking..." : "Re-run checks"}
              </button>
              <a
                data-testid="generate-final-excel"
                href={downloadFilledUrl(runId)}
                style={ui.buttonPrimary}
              >
                Download final Excel
              </a>
            </div>
          </div>

          <div style={styles.summaryStrip} aria-label="Review summary">
            <ReviewMetric label="Templates" value={String(templates.length)} />
            <ReviewMetric label="Fields shown" value={String(shownCount)} />
            <ReviewMetric label="Editable" value={String(editableCount)} />
            <ReviewMetric
              label="User edits"
              value={String(editedCount)}
              tone={editedCount > 0 ? "accent" : "neutral"}
            />
          </div>
        </section>

        {loadError && (
          <div style={styles.errorBanner}>
            Failed to load concepts: {loadError}
          </div>
        )}
        {editedCount > 0 && (
          <div data-testid="edited-values-banner" style={styles.editedBanner}>
            {editedCount} value{editedCount === 1 ? "" : "s"} edited since the
            run finished. These are included in the downloaded Excel. Re-running
            an agent will overwrite them.
          </div>
        )}

        <section style={styles.toolbar} aria-label="Review controls">
          <div style={styles.controlGroupWide}>
            <label htmlFor="template-selector" style={ui.fieldLabel}>
              Template
            </label>
            <select
              id="template-selector"
              data-testid="template-selector"
              value={activeTemplate || ""}
              onChange={(e) => setActiveTemplate(e.target.value || null)}
              style={{ ...ui.select, width: "100%" }}
            >
              {templates.map((tid) => (
                <option key={tid} value={tid}>
                  {tid}
                </option>
              ))}
              {/* Phase 3.2 — Notes review lives in the same selector so face
                  statements and notes are one review surface. */}
              <option value={NOTES_KEY}>Notes</option>
            </select>
          </div>

          {!notesActive && isGroupRun && (
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

          {!notesActive && (
            <div style={styles.searchGroup}>
              <label htmlFor="concept-search" style={ui.fieldLabel}>
                Search
              </label>
              <input
                id="concept-search"
                data-testid="concept-search"
                type="search"
                placeholder="Search across all templates"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                style={{ ...ui.input, width: "100%" }}
              />
            </div>
          )}
        </section>

        {notesActive ? (
          <div data-testid="review-notes-panel">
            <NotesReviewTab runId={runId} />
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
          />
        )}
      </main>

      <aside style={styles.sideRail}>
        <ConceptEvidencePanel concept={selectedConcept} />
        <ReconciliationQueue runId={runId} reloadKey={conflictReloadKey} />
      </aside>
    </div>
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
}: {
  rows: ConceptRow[];
  onEditValue: EditValueFn;
  editStatus: Record<string, "saving" | "saved" | "error">;
  selectedUuid: string | null;
  onSelectRow: (uuid: string) => void;
  activeScope: "Company" | "Group";
  showPeriods: boolean;
}) {
  const depthByUuid = new Map<string, number>();
  for (const r of rows) {
    if (r.parent_uuid && depthByUuid.has(r.parent_uuid)) {
      depthByUuid.set(r.concept_uuid, depthByUuid.get(r.parent_uuid)! + 1);
    } else {
      depthByUuid.set(r.concept_uuid, 0);
    }
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
        <div style={styles.headerCell}>Concept</div>
        <div style={styles.headerCell}>{showPeriods ? "CY" : "Value"}</div>
        {showPeriods && <div style={styles.headerCell}>PY</div>}
        <div style={styles.headerCell}>State</div>
        <div style={styles.headerCell}>Source</div>
      </div>
      {rows.map((r) => (
        <ConceptRowView
          key={r.concept_uuid}
          row={r}
          depth={depthByUuid.get(r.concept_uuid) || 0}
          onEditValue={onEditValue}
          editStatus={editStatus}
          selected={selectedUuid === r.concept_uuid}
          onSelectRow={onSelectRow}
          activeScope={activeScope}
          showPeriods={showPeriods}
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

function ConceptMatrixGrid({
  rows,
  onEditValue,
  editStatus,
  selectedUuid,
  onSelectRow,
  activeScope,
  showPeriods,
}: {
  rows: ConceptRow[];
  onEditValue: EditValueFn;
  editStatus: Record<string, "saving" | "saved" | "error">;
  selectedUuid: string | null;
  onSelectRow: (uuid: string) => void;
  activeScope: "Company" | "Group";
  showPeriods: boolean;
}) {
  // Distinct component columns, in spreadsheet order (B, C, …).
  const cols: string[] = [];
  for (const r of rows) {
    if (r.matrix_col && !cols.includes(r.matrix_col)) cols.push(r.matrix_col);
  }
  cols.sort();

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
        mandatory: isMandatoryConcept(r),
      });
    }
  }

  // Wider columns so an input fits without clipping accountant figures.
  const visiblePeriods: Period[] = showPeriods ? ["CY", "PY"] : ["CY"];
  const valueColumns = cols.flatMap((c) =>
    visiblePeriods.map((period) => ({ col: c, period }))
  );
  const gridCols = `minmax(220px, 1fr) repeat(${valueColumns.length}, 112px)`;

  return (
    <div
      data-testid="concept-matrix-grid"
      role="table"
      style={styles.matrixShell}
    >
      <div
        role="row"
        style={{
          display: "grid",
          gridTemplateColumns: gridCols,
          background: pwc.grey100,
          fontWeight: 600,
          fontSize: 12,
        }}
      >
        <div style={{ padding: `${pwc.space.sm}px ${pwc.space.md}px` }}>
          Movement
        </div>
        {valueColumns.map(({ col, period }) => (
          <div
            key={`${col}-${period}`}
            style={{ padding: pwc.space.sm, textAlign: "right" }}
            title={`column ${col} ${period}`}
          >
            {showPeriods ? `${col} ${period}` : col}
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
            <div style={{ padding: `${pwc.space.sm}px ${pwc.space.md}px` }}>
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
                    padding: pwc.space.sm,
                    textAlign: "right",
                    background: selected ? pwc.orange50 : "transparent",
                    boxShadow: selected
                      ? `inset 0 0 0 1px ${pwc.orange400}`
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
}: {
  row: ConceptRow;
  depth: number;
  onEditValue: EditValueFn;
  editStatus: Record<string, "saving" | "saved" | "error">;
  selected: boolean;
  onSelectRow: (uuid: string) => void;
  activeScope: "Company" | "Group";
  showPeriods: boolean;
}) {
  // Phase 5.3 — labels are READ-ONLY in the per-run review; renaming lives
  // on the global Template settings page so there's one coherent place to
  // edit labels and one (here) to edit values. The label still shows the
  // user's global display_label override when set.
  const label = row.display_label || row.canonical_label;
  const isAbstract = row.kind === "ABSTRACT";
  const isComputed = row.kind === "COMPUTED";
  const isMandatory = isMandatoryConcept(row);
  // Phase 2.1 — only genuine LEAF rows are editable. COMPUTED totals are
  // owned by the cascade; ABSTRACT rows are section headers (gotcha #17).
  const isEditable = row.kind === "LEAF";
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
      data-testid={`concept-row-${row.concept_uuid}`}
      data-kind={row.kind}
      onClick={() => onSelectRow(row.concept_uuid)}
      style={{
        display: "grid",
        gridTemplateColumns: isAbstract
          ? "minmax(0, 1fr)"
          : treeColumns(showPeriods),
        gap: pwc.space.md,
        minWidth: 760,
        padding: isAbstract
          ? `${pwc.space.md}px ${pwc.space.lg}px`
          : `${pwc.space.md}px ${pwc.space.lg}px`,
        background: isAbstract
          ? pwc.grey100
          : selected
          ? pwc.orange50
          : pwc.white,
        borderBottom: `1px solid ${pwc.grey100}`,
        fontFamily: pwc.fontBody,
        fontSize: 13,
        fontWeight: isAbstract ? 600 : 400,
        color: isComputed ? pwc.grey700 : pwc.grey900,
        cursor: "pointer",
        alignItems: "center",
      }}
    >
      <div
        title={`canonical: ${row.canonical_label}`}
        style={{
          paddingLeft: depth * 14,
          display: "flex",
          flexDirection: "column",
          gap: 2,
          lineHeight: 1.45,
        }}
      >
        <span data-testid={`label-${row.concept_uuid}`}>{label}</span>
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
          <div style={styles.sourceCell}>{row.source || ""}</div>
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
}: {
  label: string;
  value: string;
  tone?: "neutral" | "accent" | "warning";
}) {
  const palette =
    tone === "accent"
      ? { bg: pwc.orange50, color: pwc.orange700, border: pwc.orange100 }
      : tone === "warning"
      ? { bg: pwc.warningBg, color: pwc.warningText, border: pwc.warningBorder }
      : { bg: pwc.grey50, color: pwc.grey900, border: pwc.grey200 };
  return (
    <div
      style={{
        ...styles.metric,
        background: palette.bg,
        borderColor: palette.border,
      }}
    >
      <span style={styles.metricValue}>{value}</span>
      <span style={{ ...styles.metricLabel, color: palette.color }}>{label}</span>
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
  const palette =
    tone === "accent"
      ? { bg: pwc.orange50, color: pwc.orange700, border: pwc.orange100 }
      : tone === "error"
      ? { bg: pwc.errorBg, color: pwc.errorText, border: pwc.errorBorder }
      : { bg: pwc.grey50, color: pwc.grey700, border: pwc.grey200 };
  return (
    <span
      style={{
        ...ui.badge,
        background: palette.bg,
        color: palette.color,
        border: `1px solid ${palette.border}`,
      }}
    >
      {label.replace(/_/g, " ")}
    </span>
  );
}

function ConceptEvidencePanel({
  concept,
}: {
  concept: ConceptRow | null;
}) {
  return (
    <section style={styles.evidencePanel}>
      <h2 style={styles.panelTitle}>Selected field</h2>
      {concept == null ? (
        <p style={styles.panelMuted}>Select a value row to view source context.</p>
      ) : (
        <div style={styles.evidenceStack}>
          <div>
            <div style={styles.evidenceLabel}>Concept</div>
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
            <div style={styles.evidenceText}>{concept.source || "No source recorded"}</div>
          </div>
          <div>
            <div style={styles.evidenceLabel}>Evidence</div>
            <div style={styles.evidenceText}>
              {concept.evidence || "No evidence snippet recorded for this field."}
            </div>
          </div>
        </div>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------
// EditableValueCell — a number input for a LEAF value with a debounced save
// and a per-cell status badge (Saving / Saved / Failed). Mirrors the notes
// editor: debounce while typing, flush on blur, and flush any pending edit
// with `keepalive` on unmount so navigating away mid-edit never loses a save.
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
}: {
  uuid: string;
  value: number | null;
  onEditValue: EditValueFn;
  status?: "saving" | "saved" | "error";
  period?: Period;
  scope: "Company" | "Group";
  highlight?: boolean;
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
    const t = raw.trim();
    if (t === "") return null;
    const n = Number(t);
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
      : status === "saved"
      ? "Saved"
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

  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
      {badge && (
        <span
          data-testid={statusTestId}
          style={{
            fontSize: 10,
            color: status === "error" ? pwc.error : pwc.grey700,
          }}
        >
          {badge}
        </span>
      )}
      <input
        data-testid={inputTestId}
        inputMode="decimal"
        value={draft}
        onChange={(e) => {
          setDraft(e.target.value);
          schedule(e.target.value);
        }}
        onFocus={() => setFocused(true)}
        onBlur={(e) => {
          setFocused(false);
          flush(e.target.value);
        }}
        style={{
          width: 132,
          textAlign: "right",
          padding: `${pwc.space.xs}px ${pwc.space.sm}px`,
          border: `1px solid ${
            status === "error"
              ? pwc.error
              : highlightEmpty
              ? pwc.orange400
              : pwc.grey300
          }`,
          borderRadius: pwc.radius.sm,
          fontFamily: pwc.fontMono,
          fontSize: 13,
          background: highlightEmpty ? pwc.orange50 : pwc.white,
        }}
      />
    </span>
  );
}

const styles = {
  reviewHeader: {
    ...ui.card,
    padding: pwc.space.xl,
    marginBottom: pwc.space.lg,
  } as React.CSSProperties,
  titleRow: {
    display: "flex",
    alignItems: "flex-start",
    justifyContent: "space-between",
    gap: pwc.space.xl,
    flexWrap: "wrap",
  } as React.CSSProperties,
  kicker: {
    fontFamily: pwc.fontHeading,
    fontSize: 12,
    fontWeight: 600,
    color: pwc.orange700,
    textTransform: "uppercase" as const,
    letterSpacing: "0.04em",
    marginBottom: pwc.space.xs,
  } as React.CSSProperties,
  pageTitle: {
    fontFamily: pwc.fontHeading,
    color: pwc.grey900,
    fontSize: 20,
    fontWeight: 700,
    lineHeight: 1.2,
    margin: 0,
  } as React.CSSProperties,
  actionRow: {
    display: "flex",
    alignItems: "center",
    justifyContent: "flex-end",
    gap: pwc.space.md,
    flexWrap: "wrap",
  } as React.CSSProperties,
  recheckSummary: {
    fontSize: 12,
    color: pwc.grey700,
    whiteSpace: "nowrap",
  } as React.CSSProperties,
  summaryStrip: {
    display: "grid",
    gridTemplateColumns: "repeat(auto-fit, minmax(120px, 1fr))",
    gap: pwc.space.md,
    marginTop: pwc.space.xl,
  } as React.CSSProperties,
  metric: {
    border: `1px solid ${pwc.grey200}`,
    borderRadius: pwc.radius.sm,
    padding: `${pwc.space.sm}px ${pwc.space.md}px`,
    display: "flex",
    flexDirection: "column" as const,
    gap: 2,
  } as React.CSSProperties,
  metricValue: {
    fontFamily: pwc.fontMono,
    fontSize: 18,
    fontWeight: 700,
    color: pwc.grey900,
  } as React.CSSProperties,
  metricLabel: {
    fontFamily: pwc.fontHeading,
    fontSize: 11,
    fontWeight: 600,
    textTransform: "uppercase" as const,
    letterSpacing: "0.03em",
  } as React.CSSProperties,
  errorBanner: {
    marginBottom: pwc.space.md,
    padding: `${pwc.space.sm}px ${pwc.space.md}px`,
    background: pwc.errorBg,
    border: `1px solid ${pwc.errorBorder}`,
    borderRadius: pwc.radius.sm,
    color: pwc.errorText,
    fontSize: 13,
  } as React.CSSProperties,
  editedBanner: {
    marginBottom: pwc.space.md,
    padding: `${pwc.space.sm}px ${pwc.space.md}px`,
    background: pwc.orange50,
    border: `1px solid ${pwc.orange100}`,
    borderRadius: pwc.radius.sm,
    fontSize: 13,
    color: pwc.grey900,
  } as React.CSSProperties,
  toolbar: {
    ...ui.card,
    padding: pwc.space.lg,
    marginBottom: pwc.space.lg,
    display: "flex",
    flexWrap: "wrap",
    gap: pwc.space.md,
    alignItems: "end",
    position: "sticky" as const,
    top: 0,
    zIndex: 5,
  } as React.CSSProperties,
  controlGroupWide: {
    flex: "1 1 340px",
    minWidth: 260,
    display: "flex",
    flexDirection: "column" as const,
    gap: pwc.space.xs,
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
  segmented: {
    display: "inline-flex",
    border: `1px solid ${pwc.grey300}`,
    borderRadius: pwc.radius.sm,
    overflow: "hidden",
    background: pwc.white,
    minHeight: 36,
  } as React.CSSProperties,
  segmentedButton: {
    padding: `${pwc.space.sm}px ${pwc.space.md}px`,
    border: "none",
    borderRight: `1px solid ${pwc.grey200}`,
    cursor: "pointer",
    fontFamily: pwc.fontHeading,
    fontSize: 12,
    fontWeight: 600,
    minWidth: 54,
  } as React.CSSProperties,
  sideRail: {
    display: "flex",
    flexDirection: "column" as const,
    gap: pwc.space.lg,
    position: "sticky" as const,
    top: pwc.space.xl,
    flex: "1 1 320px",
    maxWidth: 380,
    minWidth: 0,
  } as React.CSSProperties,
  evidencePanel: {
    ...ui.card,
    padding: pwc.space.lg,
  } as React.CSSProperties,
  panelTitle: {
    margin: 0,
    marginBottom: pwc.space.md,
    fontFamily: pwc.fontHeading,
    fontSize: 15,
    fontWeight: 700,
    color: pwc.grey900,
  } as React.CSSProperties,
  panelMuted: {
    margin: 0,
    color: pwc.grey700,
    fontSize: 13,
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
    fontWeight: 600,
    color: pwc.grey700,
    textTransform: "uppercase" as const,
    letterSpacing: "0.03em",
    marginBottom: 2,
  } as React.CSSProperties,
  evidenceText: {
    fontSize: 13,
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
    padding: `${pwc.space.sm}px ${pwc.space.lg}px`,
    background: pwc.grey900,
    color: pwc.white,
  } as React.CSSProperties,
  headerCell: {
    fontFamily: pwc.fontHeading,
    fontSize: 12,
    fontWeight: 700,
  } as React.CSSProperties,
  valueCell: {
    textAlign: "right" as const,
    display: "flex",
    justifyContent: "flex-end",
    alignItems: "center",
    minWidth: 0,
    fontFamily: pwc.fontMono,
  } as React.CSSProperties,
  emptyValueBox: {
    display: "inline-block",
    width: 132,
    height: 26,
    border: `1px solid ${pwc.grey300}`,
    borderRadius: pwc.radius.sm,
    background: pwc.white,
  } as React.CSSProperties,
  mandatoryEmptyValueBox: {
    display: "inline-block",
    width: 132,
    height: 26,
    border: `1px solid ${pwc.orange400}`,
    borderRadius: pwc.radius.sm,
    background: pwc.orange50,
  } as React.CSSProperties,
  // Computed totals / non-editable values: same footprint as the editable
  // input but with a read-only look (faint fill, muted border, no caret).
  readonlyValueBox: {
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "flex-end",
    width: 132,
    minHeight: 26,
    padding: `${pwc.space.xs}px ${pwc.space.sm}px`,
    border: `1px solid ${pwc.grey200}`,
    borderRadius: pwc.radius.sm,
    background: pwc.grey50,
    fontFamily: pwc.fontMono,
    fontSize: 13,
    color: pwc.grey800,
  } as React.CSSProperties,
  stateCell: {
    display: "flex",
    alignItems: "center",
    minWidth: 0,
  } as React.CSSProperties,
  sourceCell: {
    color: pwc.grey700,
    fontSize: 12,
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap" as const,
  } as React.CSSProperties,
  matrixShell: {
    ...ui.card,
    overflowX: "auto",
  } as React.CSSProperties,
};
