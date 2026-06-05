import { useEffect, useState, useCallback } from "react";
import { pwc } from "../lib/theme";
import { ui, uiClass } from "../lib/uiStyles";
import { fetchBenchmarks, createBenchmark, deleteBenchmark } from "../lib/api";
import type { BenchmarkJson } from "../lib/types";
import { ConceptsPage } from "./ConceptsPage";

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
        if (!cancelled) setLoadError(e instanceof Error ? e.message : String(e));
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
  const onDelete = useCallback(
    async (e: React.MouseEvent) => {
      e.stopPropagation();
      // eval_scores.benchmark_id is ON DELETE CASCADE, so deleting a benchmark
      // also removes the scorecard of every run graded against it (and those
      // runs' History score reverts to "—"). Say so honestly.
      if (!window.confirm(`Delete benchmark "${benchmark.name}"? This permanently removes its gold answers AND the scorecard of every run graded against it. This can't be undone.`)) {
        return;
      }
      setDeleting(true);
      try {
        await deleteBenchmark(benchmark.id);
        onDeleted();
      } catch {
        setDeleting(false);
      }
    },
    [benchmark.id, benchmark.name, onDeleted]
  );

  return (
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
  );
}

function AddBenchmarkForm({ onCreated }: { onCreated: () => void }) {
  const [name, setName] = useState("");
  const [standard, setStandard] = useState("mfrs");
  const [level, setLevel] = useState("company");
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);

  const submit = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault();
      setError(null);
      setOk(null);
      if (!file) {
        setError("Choose a filled .xlsx workbook to ingest.");
        return;
      }
      if (!name.trim()) {
        setError("Give the benchmark a name.");
        return;
      }
      setBusy(true);
      try {
        const res = await createBenchmark({
          file,
          name: name.trim(),
          filing_standard: standard,
          filing_level: level,
        });
        setOk(`Created "${name.trim()}" — ${res.ingested} gold cells from ${res.statements.join(", ")}.`);
        setName("");
        setFile(null);
        onCreated();
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setBusy(false);
      }
    },
    [file, name, standard, level, onCreated]
  );

  return (
    <form
      data-testid="add-benchmark-form"
      onSubmit={submit}
      style={styles.formCard}
      className={uiClass.card}
    >
      <div style={styles.formTitle}>Add benchmark</div>
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
    color: pwc.successText ?? pwc.grey800,
    fontSize: 13,
  } as React.CSSProperties,
  errorBanner: {
    padding: `${pwc.space.sm}px ${pwc.space.md}px`,
    background: pwc.errorBg,
    border: `1px solid ${pwc.errorBorder}`,
    borderLeft: `3px solid ${pwc.error}`,
    borderRadius: pwc.radius.sm,
    color: pwc.errorText,
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
    fontFamily: pwc.fontMono,
    fontSize: 13,
    color: pwc.grey700,
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
