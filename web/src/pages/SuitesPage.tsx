import { Fragment, useCallback, useEffect, useMemo, useState } from "react";
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
} from "recharts";
import { pwc, tokens } from "../lib/theme";
import { ui, uiClass } from "../lib/uiStyles";
import { userMessage } from "../lib/errors";
import { TERMS, humanize } from "../lib/vocabulary";
import { PageHeader } from "../components/PageHeader";
import { EmptyState } from "../components/EmptyState";
import { StatusLabel, type StatusState } from "../components/StatusLabel";
import {
  fetchSuites, createSuite, getSuite, addSuiteDoc, deleteSuiteDoc,
  listSuiteRuns, estimateSuiteRun, launchSuiteRun, resumeSuiteRun, stopSuiteRun,
  getSuiteRun, fetchSuiteResults, compareSuiteRuns, fetchCompareSlotDiff, fetchBenchmarks,
} from "../lib/api";
import type {
  SuiteSummaryJson, SuiteJson, SuiteRunSummaryJson, SuiteRunDetailJson,
  SuiteEstimateJson, SuiteResultsJson, SuiteCompareJson, SlotDiffJson, BenchmarkJson,
} from "../lib/types";

// ---------------------------------------------------------------------------
// SuitesPage — the Evals workspace (Phase E/F). A corpus of documents run as a
// batch, with accuracy/consistency/health scorecards, trends, and compare.
// One page with internal navigation (list → suite → run/results) so it doesn't
// add a fistful of AppView routes.
// ---------------------------------------------------------------------------

type View =
  | { kind: "list" }
  | { kind: "suite"; suiteId: number }
  | { kind: "run"; suiteId: number; suiteRunId: number };

export function SuitesPage() {
  const [view, setView] = useState<View>({ kind: "list" });
  if (view.kind === "list") {
    return <SuiteList onOpen={(id) => setView({ kind: "suite", suiteId: id })} />;
  }
  if (view.kind === "run") {
    return (
      <SuiteRunDetail
        suiteId={view.suiteId}
        suiteRunId={view.suiteRunId}
        onBack={() => setView({ kind: "suite", suiteId: view.suiteId })}
      />
    );
  }
  return (
    <SuiteDetail
      suiteId={view.suiteId}
      onBack={() => setView({ kind: "list" })}
      onOpenRun={(rid) => setView({ kind: "run", suiteId: view.suiteId, suiteRunId: rid })}
    />
  );
}

function pct(v: number | null | undefined): string {
  return v == null ? "—" : `${Math.round(v * 100)}%`;
}

// --- Suite list -----------------------------------------------------------

function SuiteList({ onOpen }: { onOpen: (id: number) => void }) {
  const [suites, setSuites] = useState<SuiteSummaryJson[]>([]);
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    fetchSuites().then(setSuites).catch(() => setSuites([]));
  }, [reload]);

  const create = useCallback(async () => {
    setError(null);
    if (!name.trim()) { setError("Give the suite a name."); return; }
    try {
      const s = await createSuite(name.trim());
      setName("");
      setReload((r) => r + 1);
      onOpen(s.id);
    } catch (e) {
      setError(userMessage(e));
    }
  }, [name, onOpen]);

  return (
    <div className="responsive-page" style={styles.page}>
      <PageHeader
        title={TERMS.evaluationSuites}
        description="Group documents into a suite, run the whole set as one batch, and track accuracy, consistency, and health over time."
      />
      <div style={styles.createGroup}>
        <div style={styles.cardTitle}>Create a suite</div>
        <label htmlFor="suite-name" style={ui.fieldLabel}>Suite name</label>
        <div style={styles.formRow}>
          <input
            id="suite-name"
            data-testid="suite-name"
            style={ui.input}
            placeholder="MFRS Company regression set"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
          <button data-testid="suite-create" className={uiClass.btnPrimary}
            style={ui.buttonPrimary} onClick={create}>
            Create suite
          </button>
        </div>
        {error && <span style={styles.error}>{error}</span>}
      </div>
      {suites.length === 0 ? (
        <div data-testid="suites-empty">
          <EmptyState
            title="No evaluation suites yet"
            explanation="Create one above, add representative filings, then run them together to compare extraction accuracy, consistency, and run health over time."
          />
        </div>
      ) : (
        <div style={styles.list}>
          {suites.map((s) => (
            <button
              key={s.id}
              data-testid={`suite-card-${s.id}`}
              className={uiClass.card}
              style={styles.rowCard}
              onClick={() => onOpen(s.id)}
            >
              <span style={styles.rowTitle}>{s.name}</span>
              <span style={styles.rowMeta}>
                {s.doc_count} doc{s.doc_count === 1 ? "" : "s"} · {s.run_count} run
                {s.run_count === 1 ? "" : "s"}
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

// --- Suite detail (docs, launch, runs, results) ---------------------------

function SuiteDetail({
  suiteId, onBack, onOpenRun,
}: { suiteId: number; onBack: () => void; onOpenRun: (rid: number) => void }) {
  const [suite, setSuite] = useState<SuiteJson | null>(null);
  const [runs, setRuns] = useState<SuiteRunSummaryJson[]>([]);
  const [benchmarks, setBenchmarks] = useState<BenchmarkJson[]>([]);
  const [tab, setTab] = useState<"setup" | "results">("setup");
  const [reload, setReload] = useState(0);

  useEffect(() => {
    getSuite(suiteId).then(setSuite).catch(() => setSuite(null));
    listSuiteRuns(suiteId).then(setRuns).catch(() => setRuns([]));
    fetchBenchmarks().then(setBenchmarks).catch(() => setBenchmarks([]));
  }, [suiteId, reload]);

  if (!suite) return <p style={styles.muted}>Loading suite…</p>;

  return (
    <div className="responsive-page" style={styles.page}>
      <button style={styles.back} onClick={onBack}>← All suites</button>
      <h1 style={ui.pageTitleCompact}>{suite.name}</h1>
      <div style={styles.tabBar}>
        <TabBtn active={tab === "setup"} onClick={() => setTab("setup")} testid="suite-tab-setup">
          Documents &amp; runs
        </TabBtn>
        <TabBtn active={tab === "results"} onClick={() => setTab("results")} testid="suite-tab-results">
          Results
        </TabBtn>
      </div>

      {tab === "setup" ? (
        <>
          <AddDocForm
            suiteId={suiteId}
            benchmarks={benchmarks}
            onAdded={() => setReload((r) => r + 1)}
          />
          <DocList
            suite={suite}
            onRemoved={() => setReload((r) => r + 1)}
          />
          <LaunchForm
            suiteId={suiteId}
            docCount={suite.docs.length}
            onLaunched={() => setReload((r) => r + 1)}
          />
          <RunList suiteId={suiteId} runs={runs} onOpenRun={onOpenRun}
            onChanged={() => setReload((r) => r + 1)} />
        </>
      ) : (
        <ResultsView suiteId={suiteId} />
      )}
    </div>
  );
}

function AddDocForm({
  suiteId, benchmarks, onAdded,
}: { suiteId: number; benchmarks: BenchmarkJson[]; onAdded: () => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [standard, setStandard] = useState("mfrs");
  const [level, setLevel] = useState("company");
  const [benchmarkId, setBenchmarkId] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const candidates = benchmarks.filter(
    (b) => b.filing_standard === standard && b.filing_level === level,
  );

  const submit = useCallback(async () => {
    setError(null);
    if (!file) { setError("Choose a PDF or .docx document."); return; }
    setBusy(true);
    try {
      await addSuiteDoc({
        suiteId, file, filing_standard: standard, filing_level: level,
        benchmark_id: benchmarkId ? Number(benchmarkId) : null,
      });
      setFile(null); setBenchmarkId("");
      onAdded();
    } catch (e) {
      setError(userMessage(e));
    } finally {
      setBusy(false);
    }
  }, [suiteId, file, standard, level, benchmarkId, onAdded]);

  return (
    <div style={styles.card}>
      <div style={styles.cardTitle}>Add a document</div>
      <div style={styles.grid}>
        <label style={styles.field}>
          <span style={ui.fieldLabel}>File (PDF or .docx)</span>
          <input data-testid="doc-file" type="file" accept=".pdf,.docx"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)} style={{ fontSize: 14 }} />
        </label>
        <label style={styles.field}>
          <span style={ui.fieldLabel}>Standard</span>
          <select data-testid="doc-standard" style={ui.select} value={standard}
            onChange={(e) => setStandard(e.target.value)}>
            <option value="mfrs">MFRS</option>
            <option value="mpers">MPERS</option>
          </select>
        </label>
        <label style={styles.field}>
          <span style={ui.fieldLabel}>Level</span>
          <select data-testid="doc-level" style={ui.select} value={level}
            onChange={(e) => setLevel(e.target.value)}>
            <option value="company">Company</option>
            <option value="group">Group</option>
          </select>
        </label>
        <label style={styles.field}>
          <span style={ui.fieldLabel}>Reference answers (optional)</span>
          <select data-testid="doc-benchmark" style={ui.select} value={benchmarkId}
            onChange={(e) => setBenchmarkId(e.target.value)}>
            <option value="">None — measure consistency and health only</option>
            {candidates.map((b) => (
              <option key={b.id} value={String(b.id)}>{b.name}</option>
            ))}
          </select>
        </label>
      </div>
      <div style={styles.formRow}>
        <button data-testid="doc-add" className={uiClass.btnPrimary} style={ui.buttonPrimary}
          disabled={busy} onClick={submit}>
          {busy ? "Adding…" : "Add document"}
        </button>
        {error && <span style={styles.error}>{error}</span>}
      </div>
    </div>
  );
}

function DocList({ suite, onRemoved }: { suite: SuiteJson; onRemoved: () => void }) {
  if (suite.docs.length === 0) {
    return <p style={styles.muted}>No documents yet — add one above.</p>;
  }
  return (
    <div style={styles.tableWrap}>
      <table style={styles.table}>
        <thead>
          <tr>
            <th style={styles.th}>Document</th>
            <th style={styles.th}>Filing</th>
            <th style={styles.th}>Reference answers</th>
            <th style={styles.th}></th>
          </tr>
        </thead>
        <tbody>
          {suite.docs.map((d) => (
            <tr key={d.id} data-testid={`doc-row-${d.id}`}>
              <td style={styles.td}>{d.label || d.source_filename}</td>
              <td style={styles.td}>{d.filing_standard.toUpperCase()} · {d.filing_level}</td>
              <td style={styles.td}>{d.benchmark_id != null ? "✓" : "—"}</td>
              <td style={styles.td}>
                <button style={styles.linkBtn}
                  onClick={() => deleteSuiteDoc(suite.id, d.id).then(onRemoved)}>
                  Remove
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function LaunchForm({
  suiteId, docCount, onLaunched,
}: { suiteId: number; docCount: number; onLaunched: () => void }) {
  const [label, setLabel] = useState("");
  const [repeats, setRepeats] = useState(1);
  const [scout, setScout] = useState(false);
  const [estimate, setEstimate] = useState<SuiteEstimateJson | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const launchBody = useMemo(
    () => ({ label, repeats, use_scout: scout }),
    [label, repeats, scout],
  );

  useEffect(() => {
    if (docCount === 0) { setEstimate(null); return; }
    estimateSuiteRun(suiteId, launchBody).then(setEstimate).catch(() => setEstimate(null));
  }, [suiteId, launchBody, docCount]);

  const launch = useCallback(async () => {
    setError(null);
    setBusy(true);
    try {
      await launchSuiteRun(suiteId, launchBody);
      onLaunched();
    } catch (e) {
      setError(userMessage(e));
    } finally {
      setBusy(false);
    }
  }, [suiteId, launchBody, onLaunched]);

  return (
    <div style={styles.card}>
      <div style={styles.cardTitle}>Run the suite</div>
      <div style={styles.grid}>
        <label style={styles.field}>
          <span style={ui.fieldLabel}>Run label</span>
          <input data-testid="run-label" style={ui.input} placeholder="gpt-5.4 baseline"
            value={label} onChange={(e) => setLabel(e.target.value)} />
          <span style={styles.fieldHint}>A short name for comparing this result later.</span>
        </label>
        <label style={styles.field}>
          <span style={ui.fieldLabel}>Repeats per document</span>
          <select data-testid="run-repeats" style={ui.select} value={repeats}
            onChange={(e) => setRepeats(Number(e.target.value))}>
            {[1, 2, 3, 4, 5].map((n) => <option key={n} value={n}>{n}</option>)}
          </select>
          <span style={styles.fieldHint}>Run each filing more than once to measure consistency.</span>
        </label>
        <label style={{ ...styles.field, flexDirection: "row", alignItems: "center", gap: 8 }}>
          <input data-testid="run-scout" type="checkbox" checked={scout}
            onChange={(e) => setScout(e.target.checked)} />
          <span style={ui.fieldLabel}>Use document pre-scan</span>
        </label>
      </div>
      {estimate && (
        <div data-testid="run-estimate" style={styles.estimate}>
          {estimate.extraction_runs} extraction run{estimate.extraction_runs === 1 ? "" : "s"}
          {" "}({estimate.documents} doc × {estimate.repeats} repeat), 3 at a time
          {estimate.estimated_wall_seconds != null &&
            ` · ≈${Math.ceil(estimate.estimated_wall_seconds / 60)} min`}
          . Each run spends real tokens.
        </div>
      )}
      <div style={styles.formRow}>
        <button data-testid="run-launch" className={uiClass.btnPrimary} style={ui.buttonPrimary}
          disabled={busy || docCount === 0} onClick={launch}>
          {busy ? "Launching…" : "Launch suite run"}
        </button>
        {error && <span style={styles.error}>{error}</span>}
      </div>
    </div>
  );
}

function RunList({
  suiteId, runs, onOpenRun, onChanged,
}: { suiteId: number; runs: SuiteRunSummaryJson[]; onOpenRun: (rid: number) => void; onChanged: () => void }) {
  if (runs.length === 0) return null;
  return (
    <div>
      <div style={styles.cardTitle}>Suite runs</div>
      <div style={styles.tableWrap}>
        <table style={styles.table}>
          <thead>
            <tr>
              <th style={styles.th}>Run</th>
              <th style={styles.th}>Label</th>
              <th style={styles.th}>Status</th>
              <th style={styles.th}></th>
            </tr>
          </thead>
          <tbody>
            {runs.map((r) => (
              <tr key={r.id} data-testid={`suite-run-${r.id}`}>
                <td style={styles.td}>#{r.id} · {new Date(r.created_at).toLocaleDateString()}</td>
                <td style={styles.td}>{r.label || "—"}</td>
                <td style={styles.td}><StatusChip status={r.status} /></td>
                <td style={styles.td}>
                  <button style={styles.linkBtn} onClick={() => onOpenRun(r.id)}>Scores</button>
                  {r.status === "running" && (
                    <button data-testid={`suite-run-stop-${r.id}`} style={styles.linkBtn}
                      onClick={() => stopSuiteRun(suiteId, r.id).then(onChanged).catch(() => {})}>
                      Stop
                    </button>
                  )}
                  {r.status === "partial" && (
                    <button style={styles.linkBtn}
                      onClick={() => resumeSuiteRun(suiteId, r.id).then(onChanged)}>
                      Resume
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// --- Suite run detail (scorecards) ----------------------------------------

function SuiteRunDetail({
  suiteId, suiteRunId, onBack,
}: { suiteId: number; suiteRunId: number; onBack: () => void }) {
  const [detail, setDetail] = useState<SuiteRunDetailJson | null>(null);
  const [stopping, setStopping] = useState(false);

  useEffect(() => {
    let live = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = () => {
      getSuiteRun(suiteId, suiteRunId).then((d) => {
        if (!live) return;
        setDetail(d);
        // Keep polling while the batch is still running; store the handle so
        // navigating away clears it (no orphaned timer/request chain).
        if (d.suite_run.status === "running") timer = setTimeout(poll, 5000);
      }).catch(() => {});
    };
    poll();
    return () => {
      live = false;
      if (timer) clearTimeout(timer);
    };
  }, [suiteId, suiteRunId]);

  if (!detail) return <p style={styles.muted}>Loading scores…</p>;
  const agg = detail.aggregate;
  const running = detail.suite_run.status === "running";

  return (
    <div className="responsive-page" style={styles.page}>
      <button style={styles.back} onClick={onBack}>← Back to suite</button>
      <h1 style={{ ...ui.pageTitleCompact, display: "flex", alignItems: "center", gap: pwc.space.md, flexWrap: "wrap" }}>
        Suite run #{suiteRunId} <StatusChip status={detail.suite_run.status} />
        {running && (
          <button
            data-testid="suite-run-detail-stop"
            className={uiClass.btnDanger}
            style={{ ...ui.buttonDanger, ...ui.buttonSm, marginLeft: pwc.space.md, opacity: stopping ? 0.6 : 1 }}
            disabled={stopping}
            onClick={() => {
              setStopping(true);
              stopSuiteRun(suiteId, suiteRunId).catch(() => {}).finally(() => setStopping(false));
            }}
          >
            {stopping ? "Stopping…" : "Stop run"}
          </button>
        )}
      </h1>
      <div style={styles.metricStrip}>
        <Metric label="Mean accuracy" value={pct(agg.mean_accuracy)} />
        <Metric label="Documents" value={agg.coverage_note} />
        <Metric label="Mean consistency" value={pct(agg.mean_consistency)} />
        <Metric label="Cross-check pass" value={pct(agg.mean_cross_check_pass_rate)} />
      </div>
      {agg.worst_document && (
        <p style={styles.muted}>
          Worst document: <strong>{agg.worst_document.label}</strong> at{" "}
          {pct(agg.worst_document.accuracy)}.
        </p>
      )}
      <div style={styles.tableWrap}>
        <table style={styles.table}>
          <thead>
            <tr>
              <th style={styles.th}>Document</th>
              <th style={styles.thNum}>Accuracy</th>
              <th style={styles.thNum}>Consistency</th>
              <th style={styles.thNum}>Checks</th>
              <th style={styles.thNum}>Coverage</th>
              <th style={styles.th}>Status</th>
            </tr>
          </thead>
          <tbody>
            {detail.documents.map((d) => (
              <tr key={d.run_id} data-testid={`scorecard-${d.run_id}`}>
                <td style={styles.td}>{d.label}</td>
                <td style={styles.tdNum}>{pct(d.accuracy)}</td>
                <td style={styles.tdNum}>{pct(d.consistency)}</td>
                <td style={styles.tdNum}>{pct(d.cross_check_pass_rate)}</td>
                <td style={styles.tdNum}>
                  {d.notes_coverage_available ? pct(d.notes_coverage) : "n/a"}
                </td>
                <td style={styles.td}><StatusChip status={d.status} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// --- Results: trend + compare ---------------------------------------------

function ResultsView({ suiteId }: { suiteId: number }) {
  const [results, setResults] = useState<SuiteResultsJson | null>(null);
  const [a, setA] = useState<string>("");
  const [b, setB] = useState<string>("");
  const [compare, setCompare] = useState<SuiteCompareJson | null>(null);

  useEffect(() => {
    fetchSuiteResults(suiteId).then(setResults).catch(() => setResults(null));
  }, [suiteId]);

  const runCompare = useCallback(() => {
    if (!a || !b) return;
    compareSuiteRuns(suiteId, Number(a), Number(b)).then(setCompare).catch(() => setCompare(null));
  }, [suiteId, a, b]);

  const chartData = useMemo(
    () => (results?.points ?? []).map((p) => ({
      name: `#${p.suite_run_id}`,
      accuracy: p.mean_accuracy != null ? Math.round(p.mean_accuracy * 100) : null,
      consistency: p.mean_consistency != null ? Math.round(p.mean_consistency * 100) : null,
      checks: p.mean_cross_check_pass_rate != null ? Math.round(p.mean_cross_check_pass_rate * 100) : null,
    })),
    [results],
  );

  if (!results) return <p style={styles.muted}>Loading results…</p>;
  if (results.points.length === 0) {
    return <p data-testid="results-empty" style={styles.muted}>No suite runs yet.</p>;
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: pwc.space.xl }}>
      <div style={styles.card} data-testid="results-trend">
        <div style={styles.cardTitle}>Score trend</div>
        <div style={{ width: "100%", height: 280 }}>
          <ResponsiveContainer>
            <LineChart data={chartData} margin={{ top: 8, right: 16, bottom: 8, left: 0 }}>
              <CartesianGrid stroke={pwc.grey200} strokeDasharray="3 3" />
              <XAxis dataKey="name" stroke={pwc.grey500} fontSize={12} />
              <YAxis domain={[0, 100]} stroke={pwc.grey500} fontSize={12} unit="%" />
              <Tooltip />
              <Legend />
              <Line type="monotone" dataKey="accuracy" stroke={pwc.orange500} strokeWidth={2} connectNulls />
              <Line type="monotone" dataKey="consistency" stroke={pwc.grey700} strokeWidth={2} connectNulls />
              <Line type="monotone" dataKey="checks" stroke={pwc.grey500} strokeWidth={2} connectNulls />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>

      <div style={styles.card}>
        <div style={styles.cardTitle}>Compare two runs</div>
        <div style={styles.formRow}>
          <select data-testid="compare-a" style={ui.select} value={a} onChange={(e) => setA(e.target.value)}>
            <option value="">Run A…</option>
            {results.points.map((p) => (
              <option key={p.suite_run_id} value={String(p.suite_run_id)}>
                #{p.suite_run_id} {p.label && `· ${p.label}`}
              </option>
            ))}
          </select>
          <select data-testid="compare-b" style={ui.select} value={b} onChange={(e) => setB(e.target.value)}>
            <option value="">Run B…</option>
            {results.points.map((p) => (
              <option key={p.suite_run_id} value={String(p.suite_run_id)}>
                #{p.suite_run_id} {p.label && `· ${p.label}`}
              </option>
            ))}
          </select>
          <button data-testid="compare-go" className={uiClass.btnPrimary} style={ui.buttonPrimary}
            disabled={!a || !b} onClick={runCompare}>
            Compare
          </button>
        </div>
        {compare && <CompareTable compare={compare} suiteId={suiteId} />}
      </div>
    </div>
  );
}

function CompareTable({ compare, suiteId }: { compare: SuiteCompareJson; suiteId: number }) {
  // Value-level drill-down (Step 12): clicking a graded row fetches which
  // line items regressed / were fixed between the two runs.
  const [openDoc, setOpenDoc] = useState<number | null>(null);
  const [diff, setDiff] = useState<SlotDiffJson | null>(null);
  const toggleRow = (d: SuiteCompareJson["documents"][number]) => {
    if (!d.in_both || d.benchmark_id == null) return;
    if (openDoc === d.doc_id) {
      setOpenDoc(null);
      setDiff(null);
      return;
    }
    setOpenDoc(d.doc_id);
    setDiff(null);
    fetchCompareSlotDiff(suiteId, compare.suite_run_a, compare.suite_run_b, d.doc_id)
      .then(setDiff)
      .catch(() => setDiff(null));
  };
  return (
    <div data-testid="compare-result" style={{ marginTop: pwc.space.md }}>
      <p style={styles.muted}>
        Aggregate accuracy delta:{" "}
        <strong style={{ color: deltaColor(compare.aggregate_delta) }}>
          {fmtDelta(compare.aggregate_delta)}
        </strong>{" "}
        over {compare.common_documents} common document
        {compare.common_documents === 1 ? "" : "s"}
        {compare.only_in_one > 0 &&
          ` · ${compare.only_in_one} document(s) in only one run (excluded)`}.
      </p>
      {compare.gold_changed_any && (
        <p data-testid="compare-gold-warning" style={styles.warn}>
          Reference answers changed between these runs — score changes may reflect the reference
          edit, not a pipeline change.
        </p>
      )}
      {Object.keys(compare.taxonomy_delta).length > 0 && (
        <p style={styles.muted}>
          Taxonomy shifts:{" "}
          {Object.entries(compare.taxonomy_delta)
            .filter(([, v]) => v !== 0)
            .map(([k, v]) => `${k} ${v > 0 ? "+" : ""}${v}`)
            .join(", ") || "none"}
        </p>
      )}
      <div style={styles.tableWrap}>
        <table style={styles.table}>
          <thead>
            <tr>
              <th style={styles.th}>Document</th>
              <th style={styles.thNum}>A</th>
              <th style={styles.thNum}>B</th>
              <th style={styles.thNum}>Δ</th>
            </tr>
          </thead>
          <tbody>
            {compare.documents.map((d) => {
              const drillable = d.in_both && d.benchmark_id != null;
              return (
                <Fragment key={d.doc_id}>
                  <tr
                    data-testid={`compare-row-${d.doc_id}`}
                    onClick={() => toggleRow(d)}
                    style={{
                      opacity: d.in_both ? 1 : 0.5,
                      cursor: drillable ? "pointer" : "default",
                    }}
                  >
                    <td style={styles.td}>
                      {d.label}{!d.in_both && " (one run only)"}
                      {d.gold_changed && " ⚠"}
                      {drillable && (
                        <span style={styles.drillHint}>
                          {openDoc === d.doc_id ? " ▾" : " ▸"}
                        </span>
                      )}
                    </td>
                    <td style={styles.tdNum}>{pct(d.accuracy_a)}</td>
                    <td style={styles.tdNum}>{pct(d.accuracy_b)}</td>
                    <td style={{ ...styles.tdNum, color: deltaColor(d.delta) }}>{fmtDelta(d.delta)}</td>
                  </tr>
                  {openDoc === d.doc_id && (
                    <tr data-testid={`compare-slots-${d.doc_id}`}>
                      <td colSpan={4} style={styles.td}>
                        <SlotDiffDetail diff={diff} />
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function SlotDiffDetail({ diff }: { diff: SlotDiffJson | null }) {
  if (diff == null) {
    return <span style={styles.muted}>Loading line-item changes…</span>;
  }
  const slotName = (r: SlotDiffJson["regressions"][number]) =>
    r.label
      ? [r.sheet, r.label, r.key[1], r.key[2]].filter(Boolean).join(" · ")
      : r.key.join(" · ");
  if (diff.regressions.length === 0 && diff.fixes.length === 0) {
    return (
      <span style={styles.muted}>
        No line item changed correctness between these two runs.
      </span>
    );
  }
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: pwc.space.sm }}>
      {diff.regressions.length > 0 && (
        <div>
          <strong style={{ color: pwc.errorText, fontSize: 13 }}>
            Went wrong in B ({diff.regressions.length})
          </strong>
          <ul style={styles.slotList}>
            {diff.regressions.map((r, i) => (
              <li key={i} title={r.key.join(" · ")}>
                {slotName(r)} — should be {r.gold.toLocaleString()}
              </li>
            ))}
          </ul>
        </div>
      )}
      {diff.fixes.length > 0 && (
        <div>
          <strong style={{ color: pwc.successText, fontSize: 13 }}>
            Fixed in B ({diff.fixes.length})
          </strong>
          <ul style={styles.slotList}>
            {diff.fixes.map((r, i) => (
              <li key={i} title={r.key.join(" · ")}>
                {slotName(r)} — now correctly {r.gold.toLocaleString()}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function fmtDelta(v: number | null): string {
  if (v == null) return "—";
  const p = Math.round(v * 100);
  return `${p > 0 ? "+" : ""}${p} pt`;
}
function deltaColor(v: number | null): string {
  if (v == null || v === 0) return pwc.grey700;
  return v > 0 ? pwc.successText : pwc.errorText;
}

// --- Small shared bits ----------------------------------------------------

function TabBtn({ active, onClick, children, testid }: {
  active: boolean; onClick: () => void; children: React.ReactNode; testid: string;
}) {
  return (
    <button
      data-testid={testid}
      onClick={onClick}
      className={uiClass.tab}
      aria-selected={active}
      style={active ? { ...ui.tab, ...ui.tabActive } : ui.tab}
    >
      {children}
    </button>
  );
}

function StatusChip({ status }: { status: string }) {
  // Raw suite/run enums → explicit human label + canonical neutral symbol
  // (design-system Status: no coloured dot, border, pill, or fill).
  const state: StatusState =
    status === "complete" || status === "completed" ? "success" :
    status === "failed" ? "failure" :
    status === "running" ? "inProgress" :
    status === "partial" || status === "completed_with_errors" ? "attention" :
    "inactive";
  const label =
    status === "complete" ? "Completed" :
    status === "partial" ? "Partial — resume to finish" :
    humanize(status);
  return <StatusLabel state={state} label={label} />;
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div style={styles.metric}>
      <span style={styles.metricValue}>{value}</span>
      <span style={styles.metricLabel}>{label}</span>
    </div>
  );
}

const styles = {
  page: { ...ui.pageStandard, display: "flex", flexDirection: "column" as const, gap: pwc.space.xl } as React.CSSProperties,
  back: { alignSelf: "flex-start", background: "none", border: "none", color: pwc.grey700, cursor: "pointer", fontSize: 13, padding: 0 } as React.CSSProperties,
  card: { ...ui.card, padding: pwc.space.xl, display: "flex", flexDirection: "column" as const, gap: pwc.space.md } as React.CSSProperties,
  // Compact bordered setup group — related controls, not another equal card.
  createGroup: { ...ui.borderedGroup, display: "flex", flexDirection: "column" as const, gap: pwc.space.sm, maxWidth: 560 } as React.CSSProperties,
  cardTitle: { ...ui.subsectionTitle, fontSize: 15 } as React.CSSProperties,
  formRow: { display: "flex", gap: pwc.space.md, alignItems: "center", flexWrap: "wrap" as const } as React.CSSProperties,
  grid: { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: pwc.space.md } as React.CSSProperties,
  field: { display: "flex", flexDirection: "column" as const, gap: pwc.space.xs } as React.CSSProperties,
  fieldHint: { color: pwc.grey700, fontSize: 12, lineHeight: 1.4 } as React.CSSProperties,
  list: { display: "flex", flexDirection: "column" as const, gap: pwc.space.sm } as React.CSSProperties,
  rowCard: { ...ui.card, padding: pwc.space.lg, display: "flex", justifyContent: "space-between", alignItems: "center", cursor: "pointer", textAlign: "left" as const, background: pwc.white } as React.CSSProperties,
  rowTitle: { fontFamily: pwc.fontHeading, fontSize: 15, fontWeight: 600, color: pwc.grey900 } as React.CSSProperties,
  rowMeta: { fontSize: 13, color: pwc.grey700 } as React.CSSProperties,
  tabBar: { ...ui.tabBar } as React.CSSProperties,
  metricStrip: { display: "flex", gap: pwc.space.md, flexWrap: "wrap" as const } as React.CSSProperties,
  metric: { ...ui.statTile, display: "flex", flexDirection: "column" as const, gap: 2, minWidth: 140 } as React.CSSProperties,
  metricValue: { fontFamily: pwc.fontHeading, fontWeight: pwc.weight.regular, fontSize: 22, color: pwc.grey900 } as React.CSSProperties,
  metricLabel: { fontFamily: pwc.fontBody, fontSize: 12, color: pwc.grey700 } as React.CSSProperties,
  tableWrap: { overflowX: "auto" as const, border: `1px solid ${pwc.grey200}`, borderRadius: pwc.radius.md } as React.CSSProperties,
  table: { borderCollapse: "collapse" as const, width: "100%", fontSize: 13 } as React.CSSProperties,
  th: { ...ui.thDense } as React.CSSProperties,
  thNum: { ...ui.thDense, textAlign: "right" as const } as React.CSSProperties,
  td: { ...ui.tdDense, color: pwc.grey800 } as React.CSSProperties,
  tdNum: { ...ui.tdDense, color: pwc.grey800, textAlign: "right" as const, fontFamily: pwc.fontMono, fontVariantNumeric: "tabular-nums" } as React.CSSProperties,
  linkBtn: { background: "none", border: "none", color: tokens.color.action.primary, cursor: "pointer", fontSize: 13, padding: "4px 8px 4px 0" } as React.CSSProperties,
  estimate: { fontSize: 13, color: pwc.grey700, background: pwc.grey50, padding: pwc.space.md, borderRadius: pwc.radius.sm } as React.CSSProperties,
  error: { color: pwc.errorText, fontSize: 13 } as React.CSSProperties,
  warn: { color: pwc.grey800, fontSize: 13, borderLeft: `3px solid ${pwc.warning}`, paddingLeft: pwc.space.md } as React.CSSProperties,
  muted: { color: pwc.grey700, fontSize: 14 } as React.CSSProperties,
  drillHint: { color: pwc.grey500, fontSize: 12 } as React.CSSProperties,
  slotList: {
    margin: `${pwc.space.xs}px 0 0`,
    paddingLeft: 18,
    fontSize: 13,
    color: pwc.grey800,
    lineHeight: 1.6,
  } as React.CSSProperties,
} as const;
