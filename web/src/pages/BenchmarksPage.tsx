import { useEffect, useState, useCallback } from "react";
import { userMessage } from "../lib/errors";
import { pwc } from "../lib/theme";
import { ui, uiClass } from "../lib/uiStyles";
import { fetchBenchmarks, createBenchmark, createBenchmarkFromRun, createBenchmarkFromMtool, fetchEvalTemplates, deleteBenchmark, markBenchmarkScaleVerified, fetchRuns } from "../lib/api";
import type { RunSummaryJson } from "../lib/types";
import type { BenchmarkJson } from "../lib/types";
import { ConceptsPage } from "./ConceptsPage";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { PageHeader } from "../components/PageHeader";
import { EmptyState } from "../components/EmptyState";

// ---------------------------------------------------------------------------
// BenchmarksPage — gold-standard eval library (v16).
//
// Two modes:
//   - list (selectedId == null): the benchmark library + an "Add benchmark"
//     form (upload a human-filled MBRS template workbook → reverse-ingest).
//   - editor (selectedId != null): the gold-value editor, which reuses the
//     ConceptsPage grid in source='benchmark' mode.
//
// Inline styles + pwc tokens only (gotcha #7).
// ---------------------------------------------------------------------------

export interface BenchmarksPageProps {
  selectedId: number | null;
  onSelectBenchmark: (id: number | null) => void;
}

export function BenchmarksPage({ selectedId, onSelectBenchmark }: BenchmarksPageProps) {
  const [benchmarks, setBenchmarks] = useState<BenchmarkJson[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let cancelled = false;
    fetchBenchmarks()
      .then((bs) => {
        if (!cancelled) setBenchmarks(bs);
      })
      .catch((e) => {
        if (!cancelled) setLoadError(userMessage(e));
      });
    return () => {
      cancelled = true;
    };
  }, [reloadKey]);

  const refresh = useCallback(() => setReloadKey((k) => k + 1), []);

  // Editor mode: the gold-value grid for one benchmark.
  if (selectedId != null) {
    const bench = benchmarks.find((b) => b.id === selectedId);
    return (
      <div data-testid="benchmark-editor-page">
        <div style={styles.editorHeader}>
          <button
            type="button"
            data-testid="benchmark-back"
            className={uiClass.btnSecondary}
            style={ui.buttonSecondary}
            onClick={() => onSelectBenchmark(null)}
          >
            ← Benchmarks
          </button>
          <h1 style={styles.editorTitle}>
            {bench ? bench.name : `Benchmark ${selectedId}`}
            {bench && (
              <span style={styles.editorMeta}>
                {bench.filing_standard.toUpperCase()} · {bench.filing_level} ·{" "}
                {bench.gold_cell_count} reference values
              </span>
            )}
          </h1>
        </div>
        <ConceptsPage runId={null} source="benchmark" benchmarkId={selectedId} />
      </div>
    );
  }

  // List mode.
  const hasBenchmarks = benchmarks.length > 0;
  return (
    <div data-testid="benchmarks-page" className="responsive-page" style={styles.page}>
      <PageHeader
        title="Benchmarks"
        description="A library of financial statements with human-verified reference answers. Attach one to a run to score extraction accuracy automatically."
      />

      {/* Setup group: open and primary in the empty state; collapsed once a
          library exists so the list leads. Uncontrolled <details> keeps the
          user's toggle. */}
      <details style={styles.addGroup} open={hasBenchmarks ? undefined : true}>
        <summary style={styles.addSummary}>Add benchmark</summary>
        <AddBenchmarkForm onCreated={refresh} />
      </details>

      {loadError && (
        <div role="alert" style={styles.errorBanner}>Failed to load benchmarks: {loadError}</div>
      )}

      {benchmarks.length === 0 ? (
        <div data-testid="benchmarks-empty">
          <EmptyState
            title="No benchmarks yet"
            explanation="Seed reference answers from a finished run (recommended) or upload a human-filled MBRS template workbook above to create your first one."
          />
        </div>
      ) : (
        <div style={styles.list}>
          {benchmarks.map((b) => (
            <BenchmarkCard
              key={b.id}
              benchmark={b}
              onOpen={() => onSelectBenchmark(b.id)}
              onDeleted={refresh}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function BenchmarkCard({
  benchmark,
  onOpen,
  onDeleted,
}: {
  benchmark: BenchmarkJson;
  onOpen: () => void;
  onDeleted: () => void;
}) {
  const [deleting, setDeleting] = useState(false);
  // Use the shared ConfirmDialog (UX-QA #4) instead of window.confirm so every
  // destructive action in the app confirms the same way.
  const [confirmOpen, setConfirmOpen] = useState(false);
  const runDelete = useCallback(async () => {
    setConfirmOpen(false);
    setDeleting(true);
    try {
      await deleteBenchmark(benchmark.id);
      onDeleted();
    } catch {
      setDeleting(false);
    }
  }, [benchmark.id, onDeleted]);
  const onDelete = useCallback((e: React.MouseEvent) => {
    e.stopPropagation();
    setConfirmOpen(true);
  }, []);

  return (
    // The ConfirmDialog is a SIBLING of the clickable card, not a child (UX-QA
    // review): rendered inside the card, its Confirm/Cancel/backdrop clicks
    // bubble through React's synthetic-event tree to the card's onClick and
    // open the benchmark while you're confirming a delete.
    <>
    <div
      data-testid={`benchmark-card-${benchmark.id}`}
      style={styles.card}
      className={uiClass.card}
      onClick={onOpen}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") onOpen();
      }}
    >
      <div style={styles.cardMain}>
        <div style={styles.cardName}>{benchmark.name}</div>
        <div style={styles.cardMeta}>
          {benchmark.document && <span>{benchmark.document}</span>}
          <span>{benchmark.filing_standard.toUpperCase()} · {benchmark.filing_level}</span>
          <span style={styles.cardCount}>{benchmark.gold_cell_count} reference values</span>
          {benchmark.source === "mtool" && benchmark.scale_verified === false && (
            <span
              data-testid={`benchmark-scale-unverified-${benchmark.id}`}
              style={styles.scaleBadge}
              title="Imported from an mTool file. Whether mTool stores the printed (thousands) figure or the full figure hasn't been confirmed against a real filing yet — treat scores against this benchmark as provisional."
            >
              ! Scale unverified
              <button
                type="button"
                data-testid={`benchmark-scale-verify-${benchmark.id}`}
                style={styles.scaleVerifyBtn}
                onClick={(e) => {
                  e.stopPropagation();
                  markBenchmarkScaleVerified(benchmark.id).then(onDeleted).catch(() => {});
                }}
              >
                Mark verified
              </button>
            </span>
          )}
        </div>
        <div style={styles.cardStatements}>
          {benchmark.statements.join(" · ") || "no statements"}
        </div>
      </div>
      <div style={styles.cardActions}>
        {/* An explicit Open button — the whole card is clickable, but that
            affordance wasn't discoverable next to a lone Delete (E5). */}
        <button
          type="button"
          data-testid={`benchmark-open-${benchmark.id}`}
          className={uiClass.btnSecondary}
          style={ui.buttonSecondary}
          onClick={(e) => {
            e.stopPropagation();
            onOpen();
          }}
        >
          Open
        </button>
        <button
          type="button"
          data-testid={`benchmark-delete-${benchmark.id}`}
          className={uiClass.btnDanger}
          style={{ ...ui.buttonDanger, opacity: deleting ? 0.6 : 1 }}
          onClick={onDelete}
          disabled={deleting}
        >
          {deleting ? "Archiving…" : "Archive"}
        </button>
      </div>
    </div>
      <ConfirmDialog
        isOpen={confirmOpen}
        title={`Archive benchmark ${benchmark.name}?`}
        message={
          <>
            Archiving hides it from the pickers, but <strong>keeps its reference
            answers and every historical score</strong> — trends and past run
            grades stay intact. An administrator can still permanently delete
            it later if it was created by mistake.
          </>
        }
        confirmLabel="Archive benchmark"
        onConfirm={runDelete}
        onCancel={() => setConfirmOpen(false)}
      />
    </>
  );
}

function AddBenchmarkForm({ onCreated }: { onCreated: () => void }) {
  // "run" (recommended) seeds gold straight from a finished run's facts —
  // lossless. "upload" reverse-ingests a filled workbook, which silently drops
  // un-recalculated formula cells (SOCIE matrix + cross-sheet rollups).
  const [mode, setMode] = useState<"run" | "upload" | "mtool">("run");
  const [name, setName] = useState("");
  const [standard, setStandard] = useState("mfrs");
  const [level, setLevel] = useState("company");
  const [file, setFile] = useState<File | null>(null);
  const [runId, setRunId] = useState("");
  // mTool source (Step C4): the operator declares the figure unit (no default —
  // a wrong unit silently 1000×'s every value) and picks the variant-precise
  // template set (gotcha #21). The ingest report is shown after creation.
  const [unit, setUnit] = useState<"" | "full" | "thousands">("");
  const [templates, setTemplates] = useState<
    { template_id: string; statement: string; variant: string; label: string }[]
  >([]);
  const [selectedTemplateIds, setSelectedTemplateIds] = useState<string[]>([]);
  // Column-map fallback (Step 14): when auto-detection can't confidently
  // find the value columns, the backend 422s asking for an explicit map —
  // this textarea is the recovery path that used to be unreachable.
  const [columnMap, setColumnMap] = useState("");
  const [showColumnMap, setShowColumnMap] = useState(false);
  const [report, setReport] = useState<Awaited<
    ReturnType<typeof createBenchmarkFromMtool>
  > | null>(null);
  // Seedable runs for the picker — gold can only come from a terminal run
  // (from-run rejects draft/running/failed/aborted), so filter to those.
  const [runOptions, setRunOptions] = useState<RunSummaryJson[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);
  const [warning, setWarning] = useState<string | null>(null);

  // Load recent seedable runs for the "From a run" picker (E5) — replaces the
  // typo-prone free-text run number. Fetch each terminal status SERVER-SIDE so
  // valid older completed runs aren't hidden behind a newest page that happens
  // to be mostly drafts/failed (a client-side filter of one page would drop
  // them). Only when that mode is active.
  useEffect(() => {
    if (mode !== "run") return;
    let cancelled = false;
    Promise.all([
      fetchRuns({ status: "completed", limit: 100, offset: 0 }),
      fetchRuns({ status: "completed_with_errors", limit: 100, offset: 0 }),
    ])
      .then(([done, withErrors]) => {
        if (cancelled) return;
        const merged = [...done.runs, ...withErrors.runs].sort((a, b) =>
          a.created_at < b.created_at ? 1 : -1, // newest first
        );
        setRunOptions(merged);
      })
      .catch(() => {
        // A failed list just leaves the picker empty — the user can retry by
        // reopening the form; never blocks the upload-workbook path.
        if (!cancelled) setRunOptions([]);
      });
    return () => {
      cancelled = true;
    };
  }, [mode]);

  // mTool source: load the family's variant list so the operator picks the
  // exact templates (the from-mtool endpoint requires an explicit set).
  useEffect(() => {
    if (mode !== "mtool") return;
    let cancelled = false;
    fetchEvalTemplates(standard, level)
      .then((ts) => {
        if (!cancelled) {
          setTemplates(ts);
          setSelectedTemplateIds([]); // reset on family change
        }
      })
      .catch(() => {
        if (!cancelled) setTemplates([]);
      });
    return () => {
      cancelled = true;
    };
  }, [mode, standard, level]);

  const submit = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault();
      setError(null);
      setOk(null);
      setWarning(null);
      setReport(null);
      if (!name.trim()) {
        setError("Give the benchmark a name.");
        return;
      }
      setBusy(true);
      try {
        if (mode === "run") {
          // Strict digits only — Number.parseInt would silently accept
          // "159abc" / "159.9" and seed the WRONG run's gold.
          const trimmed = runId.trim();
          if (!/^[1-9]\d*$/.test(trimmed)) {
            setError("Enter the run number to seed gold from (e.g. 159).");
            setBusy(false);
            return;
          }
          const res = await createBenchmarkFromRun({
            run_id: Number(trimmed),
            name: name.trim(),
          });
          setOk(`Created "${name.trim()}" — ${res.ingested} reference values from ${res.statements.join(", ")} (run ${res.source_run_id}).`);
          setName("");
          setRunId("");
          onCreated();
        } else if (mode === "upload") {
          if (!file) {
            setError("Choose a filled .xlsx workbook to ingest.");
            setBusy(false);
            return;
          }
          const res = await createBenchmark({
            file,
            name: name.trim(),
            filing_standard: standard,
            filing_level: level,
          });
          setOk(`Created "${name.trim()}" — ${res.ingested} reference values from ${res.statements.join(", ")}.`);
          if (res.warning) setWarning(res.warning);
          setName("");
          setFile(null);
          onCreated();
        } else {
          // mode === "mtool"
          if (!file) {
            setError("Choose a human-filled mTool .xlsx to ingest.");
            setBusy(false);
            return;
          }
          if (unit !== "full" && unit !== "thousands") {
            setError("Declare the figure unit — full figures or thousands (RM'000).");
            setBusy(false);
            return;
          }
          if (selectedTemplateIds.length === 0) {
            setError("Pick at least one statement variant to map against.");
            setBusy(false);
            return;
          }
          const res = await createBenchmarkFromMtool({
            file,
            name: name.trim(),
            filing_standard: standard,
            filing_level: level,
            unit,
            template_ids: selectedTemplateIds,
            column_map: columnMap.trim() || undefined,
          });
          setReport(res);
          setOk(`Created "${name.trim()}" — ${res.ingested} reference values captured.`);
          if (res.scale_warning) setWarning(res.scale_warning);
          setName("");
          setFile(null);
          onCreated();
        }
      } catch (err) {
        const msg = userMessage(err);
        setError(msg);
        if (mode === "mtool" && msg.toLowerCase().includes("column map")) {
          setShowColumnMap(true);
        }
      } finally {
        setBusy(false);
      }
    },
    [mode, file, name, runId, standard, level, unit, selectedTemplateIds, columnMap, onCreated]
  );

  const createDisabledReason = !name.trim()
    ? "Enter a benchmark name to continue."
    : mode === "run" && !runId
      ? "Select a finished run to continue."
      : mode !== "run" && !file
        ? "Choose a filled workbook to continue."
        : mode === "mtool" && !unit
          ? "Declare the workbook's figure unit to continue."
          : mode === "mtool" && selectedTemplateIds.length === 0
            ? "Select at least one statement variant to continue."
            : null;

  return (
    <form
      data-testid="add-benchmark-form"
      onSubmit={submit}
      style={styles.formCard}
    >
      <div style={ui.fieldLabel}>Build reference answers from</div>
      <div role="radiogroup" aria-label="Build reference answers from" style={styles.modeRow}>
        <label style={styles.modeOption}>
          <input
            type="radio"
            name="bench-mode"
            data-testid="bench-mode-run"
            checked={mode === "run"}
            onChange={() => setMode("run")}
          />
          From a run (recommended — captures sub-sheets)
        </label>
        <label style={styles.modeOption}>
          <input
            type="radio"
            name="bench-mode"
            data-testid="bench-mode-upload"
            checked={mode === "upload"}
            onChange={() => setMode("upload")}
          />
          From an uploaded workbook
        </label>
        <label style={styles.modeOption}>
          <input
            type="radio"
            name="bench-mode"
            data-testid="bench-mode-mtool"
            checked={mode === "mtool"}
            onChange={() => setMode("mtool")}
          />
          From an mTool file (human-filled)
        </label>
      </div>
      <div style={styles.formGrid}>
        <div style={styles.formField}>
          <label htmlFor="bench-name" style={ui.fieldLabel}>Name (required)</label>
          <input
            id="bench-name"
            data-testid="bench-name"
            style={ui.input}
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="FINCO 2021 MFRS Company"
          />
        </div>
        {mode === "run" ? (
          <div style={styles.formField}>
            <label htmlFor="bench-run-id" style={ui.fieldLabel}>Finished run (required)</label>
            <select
              id="bench-run-id"
              data-testid="bench-run-id"
              style={ui.select}
              value={runId}
              onChange={(e) => setRunId(e.target.value)}
            >
              <option value="">Select a finished run…</option>
              {runOptions.map((r) => (
                <option key={r.id} value={String(r.id)}>
                  {`Run ${r.id} · ${r.pdf_filename} · ${new Date(r.created_at).toLocaleDateString()}`}
                </option>
              ))}
            </select>
            <span style={styles.fieldHint}>
              {runOptions.length === 0
                ? "No finished runs yet — complete an extraction first."
                : "Standard and level come from the run. Review the reference values afterwards in the benchmark editor."}
            </span>
          </div>
        ) : (
          <>
            <div style={styles.formField}>
              <label htmlFor="bench-standard" style={ui.fieldLabel}>Standard</label>
              <select
                id="bench-standard"
                data-testid="bench-standard"
                style={ui.select}
                value={standard}
                onChange={(e) => setStandard(e.target.value)}
              >
                <option value="mfrs">MFRS</option>
                <option value="mpers">MPERS</option>
              </select>
            </div>
            <div style={styles.formField}>
              <label htmlFor="bench-level" style={ui.fieldLabel}>Level</label>
              <select
                id="bench-level"
                data-testid="bench-level"
                style={ui.select}
                value={level}
                onChange={(e) => setLevel(e.target.value)}
              >
                <option value="company">Company</option>
                <option value="group">Group</option>
              </select>
            </div>
            <div style={styles.formField}>
              <label htmlFor="bench-file" style={ui.fieldLabel}>
                {mode === "mtool" ? "Human-filled mTool file (.xlsx, required)" : "Filled workbook (.xlsx, required)"}
              </label>
              <input
                id="bench-file"
                data-testid="bench-file"
                type="file"
                accept=".xlsx,.xlsm"
                onChange={(e) => setFile(e.target.files?.[0] ?? null)}
                style={{ fontSize: 14 }}
              />
            </div>
            {mode === "mtool" && (
              <div style={styles.formField}>
                <label htmlFor="bench-unit" style={ui.fieldLabel}>Figure unit (required)</label>
                <select
                  id="bench-unit"
                  data-testid="bench-unit"
                  style={ui.select}
                  value={unit}
                  onChange={(e) => setUnit(e.target.value as "" | "full" | "thousands")}
                >
                  <option value="">Declare the unit…</option>
                  <option value="full">Full figures (RM)</option>
                  <option value="thousands">Thousands (RM'000)</option>
                </select>
                <span style={styles.fieldHint}>
                  Authoritative — a wrong unit silently multiplies every value
                  by 1,000.
                </span>
              </div>
            )}
          </>
        )}
      </div>
      {mode === "mtool" && (
        <div style={styles.formField} data-testid="bench-template-picker">
          <label style={ui.fieldLabel}>Statement variants to map against (required)</label>
          {templates.length === 0 ? (
            <span style={styles.fieldHint}>
              No templates imported for this filing family yet.
            </span>
          ) : (
            <div style={styles.templateGrid}>
              {templates.map((t) => {
                const checked = selectedTemplateIds.includes(t.template_id);
                return (
                  <label key={t.template_id} style={styles.modeOption}>
                    <input
                      type="checkbox"
                      data-testid={`bench-template-${t.template_id}`}
                      checked={checked}
                      onChange={() =>
                        setSelectedTemplateIds((prev) =>
                          checked
                            ? prev.filter((id) => id !== t.template_id)
                            : [...prev, t.template_id],
                        )
                      }
                    />
                    {t.label}
                  </label>
                );
              })}
            </div>
          )}
        </div>
      )}
      {mode === "mtool" && showColumnMap && (
        <div style={styles.formField} data-testid="bench-column-map">
          <label htmlFor="bench-column-map-input" style={ui.fieldLabel}>
            Column map (auto-detection was not confident)
          </label>
          <span style={styles.fieldHint}>
            Tell the import which columns hold the labels and values, per
            sheet. Example for a Company filing:{" "}
            <code>{'{"SOFP": {"label_column": "D", "columns": {"current_year": "E", "prior_year": "F"}}}'}</code>
            {" "}(Group filings use group_current_year / group_prior_year /
            company_current_year / company_prior_year.)
          </span>
          <textarea
            id="bench-column-map-input"
            data-testid="bench-column-map-input"
            style={styles.columnMapArea}
            rows={4}
            value={columnMap}
            onChange={(e) => setColumnMap(e.target.value)}
            placeholder='{"SOFP": {"label_column": "D", "columns": {"current_year": "E", "prior_year": "F"}}}'
          />
        </div>
      )}
      <div style={styles.formActions}>
        <button
          type="submit"
          data-testid="bench-submit"
          className={uiClass.btnPrimary}
          style={{ ...ui.buttonPrimary, opacity: busy || createDisabledReason ? 0.55 : 1 }}
          disabled={busy || createDisabledReason !== null}
          aria-describedby={createDisabledReason ? "bench-create-reason" : undefined}
        >
          {busy ? "Ingesting…" : "Create benchmark"}
        </button>
        {createDisabledReason && !busy && (
          <span id="bench-create-reason" data-testid="bench-create-reason" style={styles.fieldHint}>
            {createDisabledReason}
          </span>
        )}
        {error && <span data-testid="bench-error" style={styles.formError}>{error}</span>}
        {ok && <span data-testid="bench-ok" style={styles.formOk}>{ok}</span>}
      </div>
      {warning && (
        <div data-testid="bench-warning" style={styles.formWarning}>
          ⚠ {warning}
        </div>
      )}
      {report && (
        <div data-testid="bench-ingest-report" style={styles.reportCard}>
          <div style={styles.reportTitle}>Ingest report</div>
          <div style={styles.reportRow}>
            {report.ingested} reference values captured
            {Object.keys(report.matched_by_statement).length > 0 && (
              <span style={styles.fieldHint}>
                {" "}
                (
                {Object.entries(report.matched_by_statement)
                  .map(([s, n]) => `${s}: ${n}`)
                  .join(", ")}
                )
              </span>
            )}
          </div>
          {report.prose_notes_captured > 0 && (
            <div style={styles.reportRow}>
              {report.prose_notes_captured} note prose payloads captured for a
              future prose-fidelity pass
            </div>
          )}
          {report.unmatched_rows.length > 0 && (
            <div style={styles.reportRow} data-testid="bench-unmatched">
              ⚠️ {report.unmatched_rows.length} row
              {report.unmatched_rows.length === 1 ? "" : "s"} read but not matched
              to a concept — enter these by hand in the reference editor:
              <ul style={styles.unmatchedList}>
                {report.unmatched_rows.slice(0, 25).map((r, i) => (
                  <li key={i} style={styles.unmatchedItem}>
                    <code style={styles.code}>{r.sheet}</code> row {r.row}:{" "}
                    {r.label}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {!!report.matrix_warning && (
            <div style={styles.reportRow} data-testid="bench-matrix-warning">
              ⚠️ {report.matrix_warning}
            </div>
          )}
          {(report.ambiguous ?? []).length > 0 && (
            <div style={styles.reportRow} data-testid="bench-ambiguous">
              ⚠️ {(report.ambiguous ?? []).length} label
              {(report.ambiguous ?? []).length === 1 ? "" : "s"} appeared on more than
              one row and {(report.ambiguous ?? []).length === 1 ? "was" : "were"} skipped
              — check these in the reference editor:
              <ul style={styles.unmatchedList}>
                {(report.ambiguous ?? []).slice(0, 25).map((r, i) => (
                  <li key={i} style={styles.unmatchedItem}>
                    <code style={styles.code}>{r.sheet}</code> {r.label}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {(report.sheets_missing ?? []).length > 0 && (
            <div style={styles.reportRow} data-testid="bench-sheets-missing">
              ⚠️ Expected statement sheet{(report.sheets_missing ?? []).length === 1 ? "" : "s"} not
              found in the workbook: {(report.sheets_missing ?? []).join(", ")} — those
              statements captured no reference values.
            </div>
          )}
          <div style={styles.reportRow}>
            Review and correct the imported reference values — click the new
            benchmark in the list to open its editor.
          </div>
        </div>
      )}
    </form>
  );
}

const styles = {
  scaleBadge: {
    display: "inline-flex",
    alignItems: "center",
    gap: 6,
    border: `1px solid ${pwc.orange700}`,
    color: pwc.orange700,
    background: pwc.orange50,
    fontSize: 12,
    padding: "1px 6px",
  } as React.CSSProperties,
  scaleVerifyBtn: {
    border: "none",
    background: "transparent",
    color: pwc.orange700,
    fontSize: 12,
    textDecoration: "underline",
    cursor: "pointer",
    padding: 0,
  } as React.CSSProperties,
  columnMapArea: {
    fontFamily: pwc.fontMono,
    fontSize: 12,
    border: `1px solid ${pwc.grey300}`,
    padding: 8,
    width: "100%",
    boxSizing: "border-box" as const,
  } as React.CSSProperties,
  page: {
    ...ui.pageStandard,
    display: "flex",
    flexDirection: "column" as const,
    gap: pwc.space.xl,
  } as React.CSSProperties,
  addGroup: {
    ...ui.borderedGroup,
    padding: `${pwc.space.lg}px ${pwc.space.xl}px`,
  } as React.CSSProperties,
  addSummary: {
    ...ui.subsectionTitle,
    fontSize: 15,
    cursor: "pointer",
  } as React.CSSProperties,
  formCard: {
    display: "flex",
    flexDirection: "column" as const,
    gap: pwc.space.lg,
    paddingTop: pwc.space.lg,
  } as React.CSSProperties,
  formGrid: {
    display: "grid",
    gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))",
    gap: pwc.space.lg,
  } as React.CSSProperties,
  formField: {
    display: "flex",
    flexDirection: "column" as const,
    gap: pwc.space.xs,
  } as React.CSSProperties,
  fieldHint: {
    fontSize: 12,
    color: pwc.grey700,
    lineHeight: 1.4,
  } as React.CSSProperties,
  modeRow: {
    display: "flex",
    gap: pwc.space.lg,
    flexWrap: "wrap" as const,
    fontSize: 13,
    color: pwc.grey900,
  } as React.CSSProperties,
  modeOption: {
    display: "flex",
    alignItems: "center",
    gap: pwc.space.xs,
    cursor: "pointer",
  } as React.CSSProperties,
  templateGrid: {
    display: "grid",
    gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))",
    gap: pwc.space.sm,
    fontSize: 13,
    color: pwc.grey900,
  } as React.CSSProperties,
  reportCard: {
    ...ui.card,
    padding: pwc.space.lg,
    display: "flex",
    flexDirection: "column" as const,
    gap: pwc.space.sm,
    fontSize: 13,
    color: pwc.grey800,
  } as React.CSSProperties,
  reportTitle: {
    fontFamily: pwc.fontHeading,
    fontSize: 14,
    fontWeight: pwc.weight.medium,
    color: pwc.grey900,
  } as React.CSSProperties,
  reportRow: {
    lineHeight: 1.5,
  } as React.CSSProperties,
  unmatchedList: {
    margin: `${pwc.space.xs}px 0 0`,
    paddingLeft: pwc.space.lg,
  } as React.CSSProperties,
  unmatchedItem: {
    lineHeight: 1.5,
  } as React.CSSProperties,
  code: {
    fontFamily: pwc.fontMono,
    fontSize: 12,
    color: pwc.grey700,
  } as React.CSSProperties,
  formWarning: {
    padding: `${pwc.space.sm}px ${pwc.space.md}px`,
    background: pwc.white,
    border: `1px solid ${pwc.grey200}`,
    borderLeft: `3px solid ${pwc.warning}`,
    borderRadius: pwc.radius.sm,
    color: pwc.grey800,
    fontSize: 13,
    lineHeight: 1.5,
  } as React.CSSProperties,
  formActions: {
    display: "flex",
    alignItems: "center",
    gap: pwc.space.lg,
    flexWrap: "wrap" as const,
  } as React.CSSProperties,
  formError: {
    color: pwc.errorText,
    fontSize: 13,
  } as React.CSSProperties,
  formOk: {
    color: pwc.successText,
    fontSize: 13,
  } as React.CSSProperties,
  errorBanner: {
    padding: `${pwc.space.sm}px ${pwc.space.md}px`,
    background: pwc.white,
    border: `1px solid ${pwc.grey200}`,
    borderLeft: `3px solid ${pwc.error}`,
    borderRadius: pwc.radius.sm,
    color: pwc.grey800,
    fontSize: 13,
  } as React.CSSProperties,
  list: {
    display: "flex",
    flexDirection: "column" as const,
    gap: pwc.space.md,
  } as React.CSSProperties,
  card: {
    ...ui.card,
    padding: pwc.space.xl,
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    gap: pwc.space.lg,
    cursor: "pointer",
  } as React.CSSProperties,
  cardMain: {
    display: "flex",
    flexDirection: "column" as const,
    gap: pwc.space.xs,
    minWidth: 0,
  } as React.CSSProperties,
  cardName: {
    fontFamily: pwc.fontHeading,
    fontSize: 16,
    fontWeight: pwc.weight.medium,
    color: pwc.grey900,
  } as React.CSSProperties,
  cardMeta: {
    display: "flex",
    alignItems: "center",
    gap: pwc.space.sm,
    flexWrap: "wrap" as const,
    color: pwc.grey700,
    fontSize: 13,
  } as React.CSSProperties,
  cardCount: {
    // Proportional, matching the rest of the card (was monospace, E5).
    fontSize: 13,
    color: pwc.grey700,
  } as React.CSSProperties,
  cardActions: {
    display: "flex",
    gap: pwc.space.sm,
    alignItems: "center",
  } as React.CSSProperties,
  cardStatements: {
    color: pwc.grey700,
    fontSize: 13,
  } as React.CSSProperties,
  editorHeader: {
    display: "flex",
    alignItems: "center",
    gap: pwc.space.lg,
    marginBottom: pwc.space.lg,
  } as React.CSSProperties,
  editorTitle: {
    ...ui.pageTitleCompact,
    display: "flex",
    flexDirection: "column" as const,
    gap: 2,
  } as React.CSSProperties,
  editorMeta: {
    fontSize: 13,
    fontWeight: pwc.weight.regular,
    color: pwc.grey700,
  } as React.CSSProperties,
} as const;
