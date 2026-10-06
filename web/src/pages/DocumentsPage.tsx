import { useCallback, useEffect, useRef, useState } from "react";
import { fetchRuns } from "../lib/api";
import type { RunSummaryJson } from "../lib/types";
import { userMessage } from "../lib/errors";
import { pwc } from "../lib/theme";
import { ui, uiClass } from "../lib/uiStyles";
import { PageHeader } from "../components/PageHeader";
import { HistoryPage } from "./HistoryPage";

/** One serial poll for the document list and switcher; preparation is durable. */
export function useDocuments(enabled: boolean) {
  const [runs, setRuns] = useState<RunSummaryJson[]>([]);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [pages, setPages] = useState(1);
  const [revision, setRevision] = useState(0);
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
        const response = await fetchRuns({ documentGroup: "progress", limit: 50 });
        for (let page = 1; page < pages && page * 50 < response.total && !cancelled; page++) {
          const next = await fetchRuns({ documentGroup: "progress", limit: 50, offset: page * 50 });
          response.runs.push(...next.runs);
        }
        response.runs = [...new Map(response.runs.map((run) => [run.id, run])).values()];
        if (cancelled) return;
        setRuns(response.runs);
        setTotal(response.total);
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
  return { runs, total, error, loading, refresh,
    loadMore: () => setPages((value) => value + 1) };
}

function stage(run: RunSummaryJson): string {
  if (run.status === "draft") {
    const prep = run.preparation;
    if (prep?.status === "failed") return "Preparation failed";
    if (prep?.status === "cancelled") return "Preparation stopped";
    if (prep?.phase === "awaiting_confirmation") return "Confirm setup";
    if (prep?.phase === "building_map") return "Mapping document";
    if (prep?.phase === "reconciling_map") return "Checking document map";
    if (prep?.status === "queued") return "Waiting to prepare";
    if (prep?.status === "working" || prep?.status === "retrying") return "Preparing document";
    return "Prepare document";
  }
  const names: Record<string, string> = {
    scouting: "Mapping document", reading_source: "Reading document", extracting: "Extracting",
    merging: "Combining statements", cross_checking: "Checking figures", reviewing: "Reviewing figures",
    correcting: "Reviewing figures", re_checking: "Checking corrections", reviewing_notes: "Reviewing notes",
    validating_notes: "Reviewing notes",
    formatting_notes: "Formatting notes", cleaning_notes: "Cleaning notes", saving: "Saving results",
    done: "Saving results",
  };
  return names[run.pipeline_stage || ""] || "Extraction in progress";
}

export function DocumentsPage({ documents, section, onSection, onAdd, onOpen }: {
  documents: ReturnType<typeof useDocuments>;
  section: "progress" | "history";
  onSection: (section: "progress" | "history") => void;
  onAdd: () => void;
  onOpen: (run: Pick<RunSummaryJson, "id" | "status">) => void;
}) {
  const tabRef = useRef<HTMLDivElement>(null);
  return <div style={{ ...ui.pageWide, display: "flex", flexDirection: "column", gap: 24 }}>
    <PageHeader title="Documents" actions={<button type="button" className={uiClass.btnPrimary} style={ui.buttonPrimary} onClick={onAdd}>Add documents</button>} />
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
      {section === "history" ? <HistoryPage selectedId={null} documentGroup="history" refreshKey={documents.runs.map((run) => run.id).join(",")} hideHeader onSelectRun={(id) => {
        if (id != null) onOpen({ id, status: "completed" });
      }} /> : <>
        {documents.error && <p role="alert" style={ui.bodyText}>{documents.error}</p>}
        {documents.loading ? <p role="status" style={ui.bodyText}>Loading documents…</p> : documents.runs.length === 0 && !documents.error ?
          <p style={ui.bodyText}>No documents in progress.</p> : <div role="table" aria-label="Documents in progress">
            <div role="row" className="document-list-row" style={{ ...rowStyle, color: pwc.grey700, fontSize: 12, padding: "0 0 12px" }}>
              <span role="columnheader">Document</span><span role="columnheader">Current stage</span><span role="columnheader">Action</span>
            </div>
            {documents.runs.map((run) => <div role="row" key={run.id} className="document-list-row" style={rowStyle}>
              <div role="cell" style={{ minWidth: 0 }}><button type="button" onClick={() => onOpen(run)} style={{ ...ui.buttonQuiet, padding: 0, minHeight: 0, justifyContent: "flex-start", textAlign: "left", overflowWrap: "anywhere" }}>{run.pdf_filename}</button>
                <div style={{ ...ui.metadata, marginTop: 8 }}>{run.status === "draft" ? "Setup" : `${(run.filing_standard || "mfrs").toUpperCase()} · ${run.filing_level === "group" ? "Group" : "Company"}`}</div>
              </div>
              <span role="cell" style={{ paddingTop: 2, color: run.preparation?.action_required ? pwc.errorText : pwc.black }}>{stage(run)}</span>
              <div role="cell"><button type="button" style={{ ...ui.buttonGhost, width: "100%", justifyContent: "flex-start", padding: 0 }} onClick={() => onOpen(run)}>{run.status === "draft" ? "Open setup" : "Open"}</button></div>
            </div>)}
          </div>}
        {documents.runs.length < documents.total && <button type="button" style={ui.buttonGhost} onClick={documents.loadMore}>Load more documents</button>}
      </>}
    </section>
  </div>;
}
const rowStyle: React.CSSProperties = { display: "grid", gridTemplateColumns: "minmax(0, 1.5fr) minmax(0, 1fr) 132px", gap: 24, alignItems: "start", padding: "20px 0", borderBottom: `1px solid ${pwc.grey100}`, fontFamily: pwc.fontBody, fontSize: 14 };
