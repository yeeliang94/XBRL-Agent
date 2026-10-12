import { useCallback, useEffect, useRef, useState } from "react";
import { deleteRun, fetchHomeStats, fetchRuns } from "../lib/api";
import type { HomeStats } from "../lib/api";
import type { RunSummaryJson } from "../lib/types";
import { userMessage } from "../lib/errors";
import { pwc } from "../lib/theme";
import { ui, uiClass } from "../lib/uiStyles";
import { PageHeader } from "../components/PageHeader";
import { HistoryPage } from "./HistoryPage";
import { ArrowForward } from "../components/iconGlyphs";
import { StatusLabel } from "../components/StatusLabel";
import { StatusIcon } from "../components/StatusIcon";
import { runStatusDisplay } from "../lib/runStatus";

import { documentStageLabel, documentStageSymbol } from "../lib/documentStage";
import { formatUploadTime } from "../lib/documentTime";
import { ConfirmDialog } from "../components/ConfirmDialog";
export { documentStageLabel, documentStageSymbol } from "../lib/documentStage";

/** One serial poll for the document list and switcher; preparation is durable. */
export function useDocuments(enabled: boolean) {
  const [runs, setRuns] = useState<RunSummaryJson[]>([]);
  const [recentDocuments, setRecentDocuments] = useState<RunSummaryJson[]>([]);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [pages, setPages] = useState(1);
  const [revision, setRevision] = useState(0);
  const [historyRefreshKey, setHistoryRefreshKey] = useState(0);
  const lastHistoryRefresh = useRef(Date.now());
  const [visible, setVisible] = useState(() => document.visibilityState !== "hidden");
  useEffect(() => {
    const update = () => setVisible(document.visibilityState !== "hidden");
    document.addEventListener("visibilitychange", update);
    return () => document.removeEventListener("visibilitychange", update);
  }, []);
  const refresh = useCallback(() => setRevision((value) => value + 1), []);
  useEffect(() => {
    if (!enabled || !visible) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      let delay = 10_000;
      try {
        const [queueResult, recent] = await Promise.allSettled([
          fetchRuns({ documentGroup: "progress", limit: 50 }),
          fetchRuns({ limit: 8 }),
        ]);
        if (queueResult.status === "rejected") throw queueResult.reason;
        const response = queueResult.value;
        for (let page = 1; page < pages && page * 50 < response.total && !cancelled; page++) {
          const next = await fetchRuns({ documentGroup: "progress", limit: 50, offset: page * 50 });
          response.runs.push(...next.runs);
        }
        response.runs = [...new Map(response.runs.map((run) => [run.id, run])).values()];
        if (cancelled) return;
        setRuns(response.runs);
        if (recent.status === "fulfilled") setRecentDocuments(recent.value.runs);
        setTotal(response.total);
        // Reuse the list poll; saved activity only refreshes History every 30s.
        if (Date.now() - lastHistoryRefresh.current >= 30_000) {
          lastHistoryRefresh.current = Date.now();
          setHistoryRefreshKey((value) => value + 1);
        }
        setError(null);
        delay = response.total > 0 ? 2_000 : 30_000;
      } catch (e) {
        if (!cancelled) setError(userMessage(e));
      } finally {
        if (!cancelled) {
          setLoading(false);
          timer = setTimeout(poll, delay);
        }
      }
    };
    void poll();
    return () => { cancelled = true; clearTimeout(timer); };
  }, [enabled, visible, pages, revision]);
  return { runs, recentDocuments, total, error, loading, refresh, historyRefreshKey,
    loadMore: () => setPages((value) => value + 1) };
}

export function DocumentsPage({ documents, section, onSection, onAdd, onOpen }: {
  documents: Omit<ReturnType<typeof useDocuments>, "recentDocuments" | "historyRefreshKey"> & { recentDocuments?: RunSummaryJson[]; historyRefreshKey?: number };
  section: "progress" | "history";
  onSection: (section: "progress" | "history") => void;
  onAdd: () => void;
  onOpen: (run: Pick<RunSummaryJson, "id" | "status">) => void;
}) {
  const [deleteTarget, setDeleteTarget] = useState<RunSummaryJson | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const tabRef = useRef<HTMLDivElement>(null);
  const [summary, setSummary] = useState<HomeStats | null>(null);
  const [recent, setRecent] = useState<RunSummaryJson[]>([]);
  const [recentLoading, setRecentLoading] = useState(true);
  const [summaryError, setSummaryError] = useState(false);
  const [recentError, setRecentError] = useState(false);
  const [summaryRetry, setSummaryRetry] = useState(0);
  // Refresh the overview when the queue changes, not on every stage poll.
  const queueRevision = documents.runs.map((run) => `${run.id}:${run.status}`).join(",");
  useEffect(() => {
    if (section !== "progress" || documents.loading) return;
    let cancelled = false;
    void Promise.allSettled([
      fetchHomeStats(),
      fetchRuns({ documentGroup: "results", limit: 5 }),
    ]).then(([stats, results]) => {
      if (cancelled) return;
      setSummary(stats.status === "fulfilled" ? stats.value : null);
      setSummaryError(stats.status === "rejected");
      setRecent(results.status === "fulfilled" ? results.value.runs : []);
      setRecentError(results.status === "rejected");
      setRecentLoading(false);
    });
    return () => { cancelled = true; };
  }, [section, documents.loading, documents.total, queueRevision, summaryRetry]);
  return <div style={{ ...ui.pageWide, display: "flex", flexDirection: "column", gap: 24 }}>
    <PageHeader title={section === "history" ? "History" : "Work queue"} actions={<button type="button" className={uiClass.btnPrimary} style={ui.buttonPrimary} onClick={onAdd}>Add documents</button>} />
    {section === "progress" && <section aria-label="Queue summary">
      <div className="queue-summary" style={{ display: "flex", flexWrap: "wrap", gap: 48 }}>
        {([
          ["In queue", documents.loading || documents.error ? undefined : documents.total],
          ["Of these, not started", summary?.drafts],
          ["Completed this month", summary?.completedThisMonth],
        ] as const).map(([label, count]) => <div key={label} style={{ display: "flex", flexDirection: "column", gap: 4, minWidth: 0 }}>
          <span style={ui.metadata}>{label}</span>
          <span style={{ ...ui.bodyText, fontSize: 20, lineHeight: 1.4, fontVariantNumeric: "tabular-nums" }}>{count ?? "—"}</span>
        </div>)}
      </div>
      {summaryError && <div role="status" style={{ ...ui.metadata, display: "flex", alignItems: "center", flexWrap: "wrap", gap: 8, marginTop: 12 }}>
        <span>Summary counts unavailable.</span>
        <button type="button" style={ui.buttonGhost} onClick={() => { setSummaryError(false); setSummaryRetry((value) => value + 1); }}>Retry summary</button>
      </div>}
    </section>}
    <div ref={tabRef} role="tablist" aria-label="Document lists" style={ui.tabBar}>
      {(["progress", "history"] as const).map((key, index) => <button key={key} type="button" role="tab"
        id={`documents-${key}`} aria-controls={`documents-${key}-panel`}
        aria-selected={section === key} tabIndex={section === key ? 0 : -1}
        style={{ ...ui.tab, ...(section === key ? ui.tabActive : {}), gap: 16 }}
        onClick={() => onSection(key)}
        onKeyDown={(event) => {
          if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
          event.preventDefault();
          const next = event.key === "Home" ? 0 : event.key === "End" ? 1 : 1 - index;
          onSection(next === 0 ? "progress" : "history");
          tabRef.current?.querySelectorAll<HTMLButtonElement>('[role="tab"]')[next]?.focus();
        }}>
        {key === "progress" ? <>In progress <span style={{ minWidth: 20, color: pwc.grey700, fontVariantNumeric: "tabular-nums" }}>{documents.loading ? "—" : documents.total}</span></> : "History"}
      </button>)}
    </div>
    <section role="tabpanel" id={`documents-${section}-panel`} aria-labelledby={`documents-${section}`}>
      {section === "history" ? <HistoryPage selectedId={null} onResumeDraft={(id) => onOpen({ id, status: "draft" })} refreshKey={JSON.stringify([documents.historyRefreshKey, [...documents.runs].sort((a, b) => a.id - b.id).map((run) => [run.id, run.status, run.preparation?.status, run.preparation?.phase])])} hideHeader onSelectRun={(id) => {
        if (id != null) onOpen({ id, status: "completed" });
      }} /> : <>
        {deleteError && <p role="alert" style={ui.bodyText}>{deleteError}</p>}
        {documents.error && <p role="alert" style={ui.bodyText}>{documents.error}</p>}
        {documents.loading ? <p role="status" style={ui.bodyText}>Loading documents…</p> : documents.runs.length === 0 && !documents.error ?
          <p style={ui.bodyText}>No documents in progress.</p> : <div role="table" aria-label="Documents in progress">
            <div role="row" className="document-list-row" style={headerRowStyle}>
              <span role="columnheader">Document</span><span role="columnheader">Stage</span><span role="columnheader" style={{ textAlign: "right" }}>Action</span>
            </div>
            {documents.runs.map((run) => <div role="row" key={run.id} className="document-list-row" style={rowStyle}>
              <div role="cell" style={{ minWidth: 0 }}><button type="button" title={run.pdf_filename} onClick={() => onOpen(run)} style={nameButtonStyle}>{run.pdf_filename}</button><span style={{ ...ui.metadata, fontSize: 12 }}>Uploaded {formatUploadTime(run.created_at)}</span></div>
              <span role="cell" style={{ ...ui.status, fontSize: 14 }}><StatusIcon symbol={documentStageSymbol(run)} />{documentStageLabel(run)}</span>
              <div role="cell" style={{ display: "flex", flexWrap: "wrap", justifyContent: "flex-end", gap: 8 }}><button type="button" className={uiClass.btnQuiet} style={{ ...ui.buttonQuiet, marginRight: -15 }} onClick={() => onOpen(run)}>{run.status === "draft" ? "Open setup" : "Open"}</button><button type="button" className={uiClass.btnQuiet} style={ui.buttonQuiet} aria-label={`Delete ${run.pdf_filename}`} disabled={run.status === "running" || ["queued", "working", "retrying"].includes(run.preparation?.status || "")} title="Active documents must finish or be stopped before deletion." onClick={() => { setDeleteError(null); setDeleteTarget(run); }}>Delete</button></div>
            </div>)}
          </div>}
        {documents.runs.length < documents.total && <div style={{ marginTop: 16 }}><button type="button" className={uiClass.btnSecondary} style={ui.buttonSecondary} onClick={documents.loadMore}>Load more documents</button></div>}
        <section aria-label="Recent results" style={{ marginTop: 40 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 16, marginBottom: 8 }}>
            <h2 style={ui.sectionTitle}>Recent results</h2>
            <button type="button" className={uiClass.btnQuiet} style={{ ...ui.buttonQuiet, marginRight: -15 }} onClick={() => onSection("history")}>View history<ArrowForward size={16} /></button>
          </div>
          {recentLoading ? <p role="status" style={ui.metadata}>Loading recent results…</p>
            : recentError ? <p role="status" style={ui.metadata}>Recent results unavailable. <button type="button" style={ui.buttonGhost} onClick={() => onSection("history")}>Open history</button></p>
            : recent.length === 0 ? <p style={ui.metadata}>No results yet.</p>
            : recent.map((run) => <div key={run.id} className="document-list-row queue-recent-row" style={rowStyle}>
              <div style={{ minWidth: 0 }}>
                <button type="button" title={run.pdf_filename} style={nameButtonStyle} onClick={() => onOpen(run)}>{run.pdf_filename}</button>
              </div>
              <StatusLabel state="inactive" symbol={runStatusDisplay(run.status).symbol} label={runStatusDisplay(run.status).label} />
              <span style={{ ...ui.metadata, textAlign: "right" }}>{(run.filing_standard || "mfrs").toUpperCase()} · {run.filing_level === "group" ? "Group" : "Company"}</span>
            </div>)}
        </section>
      </>}
    </section>
    <ConfirmDialog isOpen={deleteTarget != null} title="Delete this document?" message={`Delete ${deleteTarget?.pdf_filename || "this document"} and its saved results from Work queue and History? This cannot be undone.`} confirmLabel="Delete document" busyLabel="Deleting…" busy={deleting} onCancel={() => setDeleteTarget(null)} onConfirm={() => {
      if (!deleteTarget || deleting) return;
      setDeleting(true);
      void deleteRun(deleteTarget.id).then(() => { setDeleteTarget(null); documents.refresh(); }).catch((error) => { setDeleteTarget(null); setDeleteError(userMessage(error)); }).finally(() => setDeleting(false));
    }} />
  </div>;
}
// One grid for in-progress rows and recent results so their columns line up.
const rowStyle: React.CSSProperties = { display: "grid", gridTemplateColumns: "minmax(0, 1.5fr) minmax(0, 1fr) 160px", gap: 24, alignItems: "center", minHeight: 48, padding: "4px 0", borderBottom: `1px solid ${pwc.grey100}`, fontFamily: pwc.fontBody, fontSize: 14 };
const headerRowStyle: React.CSSProperties = { ...rowStyle, minHeight: 0, padding: "0 0 8px", borderBottom: `1px solid ${pwc.grey200}`, color: pwc.grey700, fontSize: 12 };
const nameButtonStyle: React.CSSProperties = { ...ui.buttonQuiet, padding: 0, minHeight: 0, maxWidth: "100%", display: "block", textAlign: "left", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontWeight: pwc.weight.regular };
