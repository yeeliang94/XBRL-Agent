import { useCallback, useEffect, useMemo, useState } from "react";
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
} from "recharts";
import { pwc } from "../lib/theme";
import { ui, uiClass } from "../lib/uiStyles";
import { userMessage } from "../lib/errors";
import {
  fetchSuites, createSuite, getSuite, addSuiteDoc, deleteSuiteDoc,
  listSuiteRuns, estimateSuiteRun, launchSuiteRun, resumeSuiteRun, getSuiteRun,
  fetchSuiteResults, compareSuiteRuns, fetchBenchmarks,
} from "../lib/api";
import type {
  SuiteSummaryJson, SuiteJson, SuiteRunSummaryJson, SuiteRunDetailJson,
  SuiteEstimateJson, SuiteResultsJson, SuiteCompareJson, BenchmarkJson,
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
    <div style={styles.page}>
      <div>
        <h1 style={styles.title}>Evals — Suites</h1>
        <p style={styles.subtitle}>
          Group documents into a suite, run the whole set as one batch, and track
          accuracy, consistency, and health over time.
        </p>
      </div>
      <div style={styles.card}>
        <div style={styles.formRow}>
          <input
            data-testid="suite-name"
            style={ui.input}
            placeholder="MFRS Company regression set"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
          <button data-testid="suite-create" className={uiClass.btnPrimary}
            style={ui.buttonPrimary} onClick={create}>
            New suite
          </button>
        </div>
        {error && <span style={styles.error}>{error}</span>}
      </div>
      {suites.length === 0 ? (
        <p data-testid="suites-empty" style={styles.muted}>No suites yet.</p>
      ) : (
        <div style={styles.list}>
          {suites.map((s) => (
            <button
              key={s.id}
              data-testid={`suite-card-${s.id}`}
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
    <div style={styles.page}>
      <button style={styles.back} onClick={onBack}>← All suites</button>
      <h1 style={styles.title}>{suite.name}</h1>
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
          <span style={ui.fieldLabel}>Gold (optional)</span>
          <select data-testid="doc-benchmark" style={ui.select} value={benchmarkId}
            onChange={(e) => setBenchmarkId(e.target.value)}>
            <option value="">No gold — consistency + health only</option>
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
            <th style={styles.th}>Gold</th>
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
        </label>
        <label style={styles.field}>
          <span style={ui.fieldLabel}>Repeats per doc</span>
          <select data-testid="run-repeats" style={ui.select} value={repeats}
            onChange={(e) => setRepeats(Number(e.target.value))}>
            {[1, 2, 3, 4, 5].map((n) => <option key={n} value={n}>{n}</option>)}
          </select>
        </label>
        <label style={{ ...styles.field, flexDirection: "row", alignItems: "center", gap: 8 }}>
          <input data-testid="run-scout" type="checkbox" checked={scout}
            onChange={(e) => setScout(e.target.checked)} />
          <span style={ui.fieldLabel}>Use scout</span>
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

  useEffect(() => {
    let live = true;
    const poll = () => {
      getSuiteRun(suiteId, suiteRunId).then((d) => {
        if (!live) return;
        setDetail(d);
        if (d.suite_run.status === "running") setTimeout(poll, 5000);
      }).catch(() => {});
    };
    poll();
    return () => { live = false; };
  }, [suiteId, suiteRunId]);

  if (!detail) return <p style={styles.muted}>Loading scores…</p>;
  const agg = detail.aggregate;

  return (
    <div style={styles.page}>
      <button style={styles.back} onClick={onBack}>← Back to suite</button>
      <h1 style={styles.title}>
        Suite run #{suiteRunId} <StatusChip status={detail.suite_run.status} />
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
        {compare && <CompareTable compare={compare} />}
      </div>
    </div>
  );
}

function CompareTable({ compare }: { compare: SuiteCompareJson }) {
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
          ⚠ Gold changed between these two runs — score moves may reflect the gold
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
            {compare.documents.map((d) => (
              <tr key={d.doc_id} style={{ opacity: d.in_both ? 1 : 0.5 }}>
                <td style={styles.td}>
                  {d.label}{!d.in_both && " (one run only)"}
                  {d.gold_changed && " ⚠"}
                </td>
                <td style={styles.tdNum}>{pct(d.accuracy_a)}</td>
                <td style={styles.tdNum}>{pct(d.accuracy_b)}</td>
                <td style={{ ...styles.tdNum, color: deltaColor(d.delta) }}>{fmtDelta(d.delta)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
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
    <button data-testid={testid} onClick={onClick} style={{
      fontFamily: pwc.fontHeading, fontSize: 14, fontWeight: active ? 600 : 500,
      padding: "8px 16px", border: "none", borderBottom: `2px solid ${active ? pwc.orange500 : "transparent"}`,
      background: "none", color: active ? pwc.grey900 : pwc.grey500, cursor: "pointer",
    }}>{children}</button>
  );
}

function StatusChip({ status }: { status: string }) {
  const tone =
    status === "complete" || status === "completed" ? pwc.successText :
    status === "failed" ? pwc.errorText :
    status === "running" ? pwc.orange500 : pwc.grey700;
  return (
    <span style={{
      display: "inline-block", fontSize: 12, fontWeight: 600, color: tone,
      border: `1px solid ${tone}`, borderRadius: pwc.radius.sm, padding: "1px 8px",
    }}>{status}</span>
  );
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
  page: { display: "flex", flexDirection: "column" as const, gap: pwc.space.xl } as React.CSSProperties,
  title: { fontFamily: pwc.fontHeading, fontSize: 24, fontWeight: pwc.weight.medium, color: pwc.grey900, margin: 0 } as React.CSSProperties,
  subtitle: { margin: `${pwc.space.sm}px 0 0`, color: pwc.grey700, fontSize: 14, maxWidth: 640, lineHeight: 1.5 } as React.CSSProperties,
  back: { alignSelf: "flex-start", background: "none", border: "none", color: pwc.grey700, cursor: "pointer", fontSize: 13, padding: 0 } as React.CSSProperties,
  card: { ...ui.card, padding: pwc.space.xl, display: "flex", flexDirection: "column" as const, gap: pwc.space.md } as React.CSSProperties,
  cardTitle: { fontFamily: pwc.fontHeading, fontSize: 15, fontWeight: 600, color: pwc.grey900 } as React.CSSProperties,
  formRow: { display: "flex", gap: pwc.space.md, alignItems: "center", flexWrap: "wrap" as const } as React.CSSProperties,
  grid: { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: pwc.space.md } as React.CSSProperties,
  field: { display: "flex", flexDirection: "column" as const, gap: pwc.space.xs } as React.CSSProperties,
  list: { display: "flex", flexDirection: "column" as const, gap: pwc.space.sm } as React.CSSProperties,
  rowCard: { ...ui.card, padding: pwc.space.lg, display: "flex", justifyContent: "space-between", alignItems: "center", cursor: "pointer", textAlign: "left" as const, background: pwc.white } as React.CSSProperties,
  rowTitle: { fontFamily: pwc.fontHeading, fontSize: 15, fontWeight: 600, color: pwc.grey900 } as React.CSSProperties,
  rowMeta: { fontSize: 13, color: pwc.grey500 } as React.CSSProperties,
  tabBar: { display: "flex", gap: pwc.space.sm, borderBottom: `1px solid ${pwc.grey200}` } as React.CSSProperties,
  metricStrip: { display: "flex", gap: pwc.space.md, flexWrap: "wrap" as const } as React.CSSProperties,
  metric: { ...ui.card, padding: pwc.space.lg, display: "flex", flexDirection: "column" as const, gap: 2, minWidth: 140 } as React.CSSProperties,
  metricValue: { fontFamily: pwc.fontMono, fontSize: 22, color: pwc.grey900 } as React.CSSProperties,
  metricLabel: { fontFamily: pwc.fontHeading, fontSize: 12, fontWeight: 500, color: pwc.grey500 } as React.CSSProperties,
  tableWrap: { overflowX: "auto" as const, border: `1px solid ${pwc.grey200}`, borderRadius: pwc.radius.md } as React.CSSProperties,
  table: { borderCollapse: "collapse" as const, width: "100%", fontSize: 13 } as React.CSSProperties,
  th: { textAlign: "left" as const, padding: "8px 12px", fontFamily: pwc.fontHeading, fontSize: 12, fontWeight: 600, color: pwc.grey700, background: pwc.grey100, borderBottom: `1px solid ${pwc.grey200}` } as React.CSSProperties,
  thNum: { textAlign: "right" as const, padding: "8px 12px", fontFamily: pwc.fontHeading, fontSize: 12, fontWeight: 600, color: pwc.grey700, background: pwc.grey100, borderBottom: `1px solid ${pwc.grey200}` } as React.CSSProperties,
  td: { padding: "8px 12px", borderBottom: `1px solid ${pwc.grey100}`, color: pwc.grey800 } as React.CSSProperties,
  tdNum: { padding: "8px 12px", borderBottom: `1px solid ${pwc.grey100}`, color: pwc.grey800, textAlign: "right" as const, fontFamily: pwc.fontMono } as React.CSSProperties,
  linkBtn: { background: "none", border: "none", color: pwc.orange500, cursor: "pointer", fontSize: 13, padding: "0 8px 0 0" } as React.CSSProperties,
  estimate: { fontSize: 13, color: pwc.grey700, background: pwc.grey50, padding: pwc.space.md, borderRadius: pwc.radius.sm } as React.CSSProperties,
  error: { color: pwc.errorText, fontSize: 13 } as React.CSSProperties,
  warn: { color: pwc.grey800, fontSize: 13, borderLeft: `3px solid ${pwc.warning}`, paddingLeft: pwc.space.md } as React.CSSProperties,
  muted: { color: pwc.grey700, fontSize: 14 } as React.CSSProperties,
} as const;
