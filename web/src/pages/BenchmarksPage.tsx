import { useEffect, useState, useCallback } from "react";
import { userMessage } from "../lib/errors";
import { pwc } from "../lib/theme";
import { ui, uiClass } from "../lib/uiStyles";
import { fetchBenchmarks, createBenchmark, createBenchmarkFromRun, deleteBenchmark, fetchRuns } from "../lib/api";
import type { RunSummaryJson } from "../lib/types";
import type { BenchmarkJson } from "../lib/types";
import { ConceptsPage } from "./ConceptsPage";
import { ConfirmDialog } from "../components/ConfirmDialog";

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
                {bench.gold_cell_count} gold cells
              </span>
            )}
          </h1>
        </div>
        <ConceptsPage runId={null} source="benchmark" benchmarkId={selectedId} />
      </div>
    );
  }

  // List mode.
  return (
    <div data-testid="benchmarks-page" style={styles.page}>
      <header style={styles.header}>
        <div>
          <h1 style={styles.title}>Benchmarks</h1>
          <p style={styles.subtitle}>
            A library of financial statements with human-verified gold answers.
            Attach one to a run to score extraction accuracy automatically.
          </p>
        </div>
      </header>

      <AddBenchmarkForm onCreated={refresh} />

      {loadError && (
        <div style={styles.errorBanner}>Failed to load benchmarks: {loadError}</div>
      )}

      {benchmarks.length === 0 ? (
        <div data-testid="benchmarks-empty" style={styles.emptyCard}>
          No benchmarks yet. Upload a human-filled MBRS template workbook above
          to create your first one.
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
          <span style={ui.badge}>{benchmark.filing_standard.toUpperCase()}</span>
          <span style={ui.badge}>{benchmark.filing_level}</span>
          <span style={styles.cardCount}>{benchmark.gold_cell_count} gold cells</span>
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
          {deleting ? "Deleting…" : "Delete"}
        </button>
      </div>
    </div>
      <ConfirmDialog
        isOpen={confirmOpen}
        title={`Delete benchmark ${benchmark.name}?`}
        message={
          <>
            This permanently removes its gold answers <strong>and the scorecard
            of every run graded against it</strong> (those runs' History score
            reverts to “—”). This can’t be undone.
          </>
        }
        confirmLabel="Delete benchmark"
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
  const [mode, setMode] = useState<"run" | "upload">("run");
  const [name, setName] = useState("");
  const [standard, setStandard] = useState("mfrs");
  const [level, setLevel] = useState("company");
  const [file, setFile] = useState<File | null>(null);
  const [runId, setRunId] = useState("");
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

  const submit = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault();
      setError(null);
      setOk(null);
      setWarning(null);
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
          setOk(`Created "${name.trim()}" — ${res.ingested} gold cells from ${res.statements.join(", ")} (run ${res.source_run_id}).`);
          setName("");
          setRunId("");
          onCreated();
        } else {
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
          setOk(`Created "${name.trim()}" — ${res.ingested} gold cells from ${res.statements.join(", ")}.`);
          if (res.warning) setWarning(res.warning);
          setName("");
          setFile(null);
          onCreated();
        }
      } catch (err) {
        setError(userMessage(err));
      } finally {
        setBusy(false);
      }
    },
    [mode, file, name, runId, standard, level, onCreated]
  );

  return (
    <form
      data-testid="add-benchmark-form"
      onSubmit={submit}
      style={styles.formCard}
      className={uiClass.card}
    >
      <div style={styles.formTitle}>Add benchmark</div>
      <div role="radiogroup" aria-label="Gold source" style={styles.modeRow}>
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
      </div>
      <div style={styles.formGrid}>
        <div style={styles.formField}>
          <label htmlFor="bench-name" style={ui.fieldLabel}>Name</label>
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
            <label htmlFor="bench-run-id" style={ui.fieldLabel}>Run</label>
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
                : "Standard / level are taken from the run. Edit the gold values afterwards in the benchmark editor."}
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
              <label htmlFor="bench-file" style={ui.fieldLabel}>Filled workbook (.xlsx)</label>
              <input
                id="bench-file"
                data-testid="bench-file"
                type="file"
                accept=".xlsx,.xlsm"
                onChange={(e) => setFile(e.target.files?.[0] ?? null)}
                style={{ fontSize: 14 }}
              />
            </div>
          </>
        )}
      </div>
      <div style={styles.formActions}>
        <button
          type="submit"
          data-testid="bench-submit"
          className={uiClass.btnPrimary}
          style={{ ...ui.buttonPrimary, opacity: busy ? 0.6 : 1 }}
          disabled={busy}
        >
          {busy ? "Ingesting…" : "Create benchmark"}
        </button>
        {error && <span data-testid="bench-error" style={styles.formError}>{error}</span>}
        {ok && <span data-testid="bench-ok" style={styles.formOk}>{ok}</span>}
      </div>
      {warning && (
        <div data-testid="bench-warning" style={styles.formWarning}>
          ⚠ {warning}
        </div>
      )}
    </form>
  );
}

const styles = {
  page: {
    display: "flex",
    flexDirection: "column" as const,
    gap: pwc.space.xl,
  } as React.CSSProperties,
  header: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "flex-start",
  } as React.CSSProperties,
  title: {
    fontFamily: pwc.fontHeading,
    fontSize: 24,
    fontWeight: pwc.weight.medium,
    color: pwc.grey900,
    margin: 0,
  } as React.CSSProperties,
  subtitle: {
    margin: `${pwc.space.sm}px 0 0`,
    color: pwc.grey700,
    fontSize: 14,
    maxWidth: 640,
    lineHeight: 1.5,
  } as React.CSSProperties,
  formCard: {
    ...ui.card,
    padding: pwc.space.xl,
    display: "flex",
    flexDirection: "column" as const,
    gap: pwc.space.lg,
  } as React.CSSProperties,
  formTitle: {
    fontFamily: pwc.fontHeading,
    fontSize: 16,
    fontWeight: pwc.weight.medium,
    color: pwc.grey900,
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
  emptyCard: {
    ...ui.card,
    padding: pwc.space.xxl,
    textAlign: "center" as const,
    color: pwc.grey700,
    fontSize: 14,
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
    color: pwc.grey500,
    fontSize: 13,
  } as React.CSSProperties,
  editorHeader: {
    display: "flex",
    alignItems: "center",
    gap: pwc.space.lg,
    marginBottom: pwc.space.lg,
  } as React.CSSProperties,
  editorTitle: {
    fontFamily: pwc.fontHeading,
    fontSize: 20,
    fontWeight: pwc.weight.medium,
    color: pwc.grey900,
    margin: 0,
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
