import { useEffect, useRef, useState } from "react";
import { apiFetch } from "../lib/api";
import type { PreparationSnapshot } from "../lib/types";
import { ApiError, userMessage } from "../lib/errors";
import { pwc } from "../lib/theme";
import { ui } from "../lib/uiStyles";
import { formatElapsedMs } from "../lib/time";
import { ElapsedTimer } from "./ElapsedTimer";
import { PipelineStages } from "./PipelineStages";
import { DisclosureChevron } from "./icons";
import { StatusIcon } from "./StatusIcon";
import { STATUS_SYMBOLS, type StatusSymbol } from "../lib/runStatus";

const activeStatuses = new Set(["queued", "working", "retrying"]);
const labels = { not_started: "Waiting", queued: "Queued", working: "Working", retrying: "Retrying", succeeded: "Complete", failed: "Failed", cancelled: "Stopped" };

// One term per state, matching the rest of the app.
const visuallyHidden: React.CSSProperties = { position: "absolute", width: 1, height: 1, overflow: "hidden", clip: "rect(0 0 0 0)", whiteSpace: "nowrap" };

const stateSymbols: Record<string, StatusSymbol> = {
  Complete: STATUS_SYMBOLS.success,
  Working: STATUS_SYMBOLS.inProgress,
  Failed: STATUS_SYMBOLS.failure,
  Stopped: STATUS_SYMBOLS.inactive,
  Waiting: STATUS_SYMBOLS.inactive,
};
// The page strip is a glance aid; very long documents rely on the counters.
const PAGE_STRIP_LIMIT = 600;

function detailStatus({ complete, active, stopped, failed = false }: { complete: boolean; active: boolean; stopped: boolean; failed?: boolean }) {
  if (complete) return "Complete";
  if (stopped) return failed ? "Failed" : "Stopped";
  if (active) return "Working";
  return "Waiting";
}

/** Upload-owned progress; terminal outcomes come only from persisted server state. */
export function DocumentPreparation({ sessionId, onSnapshot }: {
  sessionId: string;
  onSnapshot?: (snapshot: PreparationSnapshot) => void;
}) {
  const [snapshot, setSnapshot] = useState<PreparationSnapshot | null>(null);
  const [connectionError, setConnectionError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [retriedFailure, setRetriedFailure] = useState<{ message: string; attemptId: string | null } | null>(null);
  const [refresh, setRefresh] = useState(0);
  const [detailsChoice, setDetailsChoice] = useState<boolean | null>(null);
  // Live activity is the sequence of progress messages seen while this view
  // is open. It is not persisted, so it appears only during preparation.
  const [activity, setActivity] = useState<string[]>([]);
  const callback = useRef(onSnapshot);
  callback.current = onSnapshot;
  const operation = useRef(0);
  const mounted = useRef(true);
  useEffect(() => { setRetriedFailure(null); }, [sessionId]);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; operation.current += 1; };
  }, []);
  useEffect(() => {
    let disposed = false;
    let timer: ReturnType<typeof setTimeout>;
    const controller = new AbortController();
    const endpoint = `/api/preparation/${encodeURIComponent(sessionId)}`;
    async function poll() {
      const revision = operation.current;
      try {
        const next = await apiFetch<PreparationSnapshot>(endpoint, { signal: controller.signal });
        if (disposed || revision !== operation.current) return;
        setSnapshot(next);
        setConnectionError(null);
        callback.current?.(next);
        if (activeStatuses.has(next.status) && next.message) {
          setActivity((items) => items[items.length - 1] === next.message ? items : [...items, next.message].slice(-50));
        }
        if (activeStatuses.has(next.status) || next.status === "not_started") timer = setTimeout(poll, 1500);
      } catch (error) {
        if (disposed || revision !== operation.current) return;
        if (error instanceof ApiError && error.status === 404) {
          setSnapshot(null);
          setConnectionError("Document not found. Upload it again to prepare it.");
          return;
        }
        setConnectionError(`Progress connection interrupted. Reconnecting. ${userMessage(error)}`);
        timer = setTimeout(poll, 3000);
      }
    }
    void poll();
    return () => { disposed = true; controller.abort(); clearTimeout(timer); };
  }, [sessionId, refresh]);

  async function act(action: "retry" | "cancel") {
    const revision = ++operation.current;
    if (action === "retry" && snapshot?.status === "failed") {
      setRetriedFailure({ message: snapshot.message, attemptId: snapshot.attempt_id ?? null });
    }
    setBusy(true);
    try {
      const next = await apiFetch<PreparationSnapshot>(`/api/preparation/${encodeURIComponent(sessionId)}${action === "cancel" ? "/cancel" : ""}`, { method: "POST" });
      if (!mounted.current || revision !== operation.current) return;
      setSnapshot(next);
      callback.current?.(next);
      setConnectionError(null);
    } catch (error) {
      if (mounted.current && revision === operation.current) setConnectionError(userMessage(error));
    } finally {
      if (mounted.current && revision === operation.current) {
        setBusy(false);
        setRefresh((value) => value + 1);
      }
    }
  }

  const active = snapshot != null && activeStatuses.has(snapshot.status);
  const phase = snapshot?.phase ?? "pending";
  const stopped = snapshot?.status === "failed" || snapshot?.status === "cancelled";
  const failed = snapshot?.status === "failed";
  const pagesComplete = snapshot?.prepared === true || phase === "building_map" || phase === "reconciling_map" || phase === "awaiting_confirmation";
  const mapComplete = phase === "awaiting_confirmation";
  const mapActive = phase === "building_map" || phase === "reconciling_map";
  const actionRequired = snapshot?.action_required ?? "none";
  const repeatedFailure = snapshot?.status === "failed"
    && retriedFailure?.message === snapshot.message
    && retriedFailure.attemptId !== (snapshot.attempt_id ?? null);
  const total = snapshot?.total ?? 0;
  const captured = snapshot?.captured ?? 0;
  const checked = snapshot?.checked ?? snapshot?.verified ?? 0;
  const readComplete = pagesComplete || (total > 0 && captured >= total);
  // Page counts exclude the cross-page continuation checks that can finish later.
  const checkComplete = pagesComplete;
  const missingDocument = snapshot == null && connectionError?.startsWith("Document not found.");
  const succeeded = snapshot?.status === "succeeded";
  const detailsOpen = detailsChoice ?? !succeeded;
  // While work is live the activity stream carries the current message, so
  // the status line only speaks for connection problems and stopped states.
  const statusText = connectionError
    ?? (snapshot == null ? "Connecting to document preparation…" : active ? null : snapshot.message);
  const steps = [
    {
      label: "Read source pages",
      detail: total > 0 ? `Pages captured: ${captured} of ${total}` : "",
      state: detailStatus({ complete: readComplete, active: active && phase === "preparing_pages" && !readComplete, stopped: stopped && !readComplete, failed }),
    },
    {
      label: "Check page readings and continuations",
      detail: total > 0 ? `Pages checked: ${checked} of ${total}` : "",
      state: detailStatus({ complete: checkComplete, active: active && phase === "preparing_pages" && checked > 0 && !checkComplete, stopped: stopped && !checkComplete, failed }),
    },
    {
      label: "Build document map",
      detail: phase === "reconciling_map" ? "Reconciling statement and note ownership" : "",
      state: detailStatus({ complete: mapComplete, active: active && mapActive, stopped: stopped && pagesComplete, failed }),
    },
  ];
  const stepList = (
    <ol id="preparation-steps" hidden={!detailsOpen} aria-label="Document preparation steps" style={{ listStyle: "none", margin: 0, padding: 0 }}>
      {steps.map((step) => (
        <li key={step.label} className="preparation-step-row" style={{ display: "grid", gridTemplateColumns: "16px minmax(0, 1fr) auto", gap: pwc.space.md, alignItems: "center", minHeight: 48, padding: `${pwc.space.sm}px 0`, borderTop: `1px solid ${pwc.grey100}` }}>
          <StatusIcon symbol={stateSymbols[step.state]} />
          <span style={{ minWidth: 0 }}>
            <span style={{ ...ui.bodyText, display: "block" }}>{step.label}</span>
            {step.detail && <span style={{ ...ui.metadata, display: "block" }}>{step.detail}</span>}
          </span>
          <span style={{ ...ui.metadata, color: step.state === "Failed" ? pwc.orange700 : pwc.grey700, fontWeight: step.state === "Working" ? pwc.weight.medium : pwc.weight.regular }}>
            {step.state}
          </span>
        </li>
      ))}
    </ol>
  );
  const showPages = !succeeded && total > 0 && total <= PAGE_STRIP_LIMIT;
  // Same rhythm as Filing setup: a 40px header row, 8 to its content, 16
  // between blocks; the page puts 32 before the next section.
  return <section aria-label="Document preparation" style={{ display: "flex", flexDirection: "column", gap: pwc.space.sm }}>
    <div style={{ display: "flex", alignItems: "center", gap: pwc.space.md, flexWrap: "wrap", minHeight: 40 }}>
      <h2 style={{ ...ui.sectionTitle, margin: 0 }}>Document preparation</h2>
      <span data-testid="preparation-status">{snapshot ? labels[snapshot.status] : missingDocument ? "Unavailable" : "Connecting"}</span>
      {snapshot?.started_at != null && (active
        ? <ElapsedTimer startTime={snapshot.started_at * 1000} isRunning />
        : snapshot.updated_at != null ? <span>{formatElapsedMs(Math.max(0, snapshot.updated_at - snapshot.started_at) * 1000)}</span> : null)}
      {active && <button style={ui.buttonSecondary} disabled={busy} onClick={() => void act("cancel")}>Stop preparation</button>}
      {snapshot?.status === "not_started" && <button style={ui.buttonSecondary} disabled={busy} onClick={() => void act("retry")}>Start preparation</button>}
      {(snapshot?.status === "failed" || snapshot?.status === "cancelled") && <button style={ui.buttonSecondary} disabled={busy} onClick={() => void act("retry")}>Retry preparation</button>}
    </div>
    <div style={{ display: "flex", flexDirection: "column", gap: pwc.space.lg }}>
    {!missingDocument && <div>
      <PipelineStages
        currentPhase={null}
        preparationPhase={phase}
        preparationActive={active}
        preparationStopped={stopped}
        preparationAction={actionRequired}
        isRunning={false}
        isComplete={false}
      />
    </div>}
    {statusText != null && (
      <p role="status" aria-live="polite" style={succeeded && !connectionError ? visuallyHidden : { margin: 0 }}>{statusText}</p>
    )}
    {snapshot?.prepared && !succeeded && (
      <p style={{ margin: 0 }}>Document prepared. {active && mapActive ? "Document map and notes inventory are being built." : "Notes inventory is not ready."}</p>
    )}
    {actionRequired === "retry" || snapshot?.status === "not_started" ? (
      <p style={{ margin: 0, color: pwc.grey700 }}>
        {actionRequired === "retry"
          ? repeatedFailure
            ? "The same step failed after retry. Upload a revised source document to continue."
            : "Retry resumes saved progress. If the same step fails again, upload a revised source document."
          : "Start document preparation."}
      </p>
    ) : null}
    {!missingDocument && (succeeded ? (
      <div style={{ display: "flex", flexDirection: "column", gap: pwc.space.sm }}>
        {/* Once setup opens, the preparation record folds behind one control. */}
        <button
          type="button"
          aria-expanded={detailsOpen}
          aria-controls="preparation-steps"
          onClick={() => setDetailsChoice(!detailsOpen)}
          style={{ display: "flex", alignItems: "center", gap: pwc.space.sm, minHeight: 40, padding: 0, background: "transparent", border: "none", cursor: "pointer", alignSelf: "flex-start", ...ui.bodyText, fontWeight: pwc.weight.medium }}
        >
          Preparation details
          <DisclosureChevron open={detailsOpen} />
        </button>
        {stepList}
      </div>
    ) : (
      <div style={{ display: "flex", flexDirection: "column", gap: pwc.space.lg }}>
        {stepList}
        {showPages && (
          <div aria-hidden="true" data-testid="preparation-page-strip" style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(9px, 1fr))", gap: 3 }}>
            {Array.from({ length: total }, (_, i) => (
              <span key={i} style={{
                aspectRatio: "3 / 4",
                borderRadius: 2,
                // Pages are read in parallel, so the strip shows proportions only.
                background: i < checked ? pwc.black : i < captured ? pwc.grey300 : pwc.grey100,
              }} />
            ))}
          </div>
        )}
        {activity.length > 0 && (
          <div style={{ display: "flex", flexDirection: "column", gap: pwc.space.sm }}>
            <h3 style={{ ...ui.subsectionTitle, margin: 0, minHeight: 40, display: "flex", alignItems: "center" }}>Live activity</h3>
            <ol aria-label="Live activity" aria-live="polite" style={{ listStyle: "none", margin: 0, padding: 0 }}>
              {/* Newest first, matching the run Activity stream. */}
              {activity.map((text, i) => ({ text, i })).reverse().map(({ text, i }) => (
                <li key={`${i}-${text}`} className="activity-sentence-enter" style={{ display: "grid", gridTemplateColumns: "10px minmax(0, 1fr)", gap: pwc.space.sm, minHeight: 40, padding: `${pwc.space.xs}px 0 ${pwc.space.sm}px` }}>
                  <span aria-hidden="true" style={{ position: "relative", width: 10, marginLeft: 3, borderLeft: `1px solid ${pwc.grey200}` }}>
                    <span style={{ position: "absolute", top: 6, left: -4, width: 7, height: 7, borderRadius: "50%", background: active && i === activity.length - 1 ? pwc.orange500 : pwc.grey300 }} />
                  </span>
                  <span style={ui.bodyText}>{text}</span>
                </li>
              ))}
            </ol>
          </div>
        )}
      </div>
    ))}
    </div>
  </section>;
}
