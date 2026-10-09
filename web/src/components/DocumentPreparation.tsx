import { useEffect, useRef, useState } from "react";
import { apiFetch } from "../lib/api";
import type { PreparationSnapshot } from "../lib/types";
import { ApiError, userMessage } from "../lib/errors";
import { pwc } from "../lib/theme";
import { ui } from "../lib/uiStyles";
import { formatElapsedMs } from "../lib/time";
import { ElapsedTimer } from "./ElapsedTimer";
import { PipelineStages } from "./PipelineStages";

const activeStatuses = new Set(["queued", "working", "retrying"]);
const labels = { not_started: "Waiting", queued: "Queued", working: "Working", retrying: "Retrying", succeeded: "Complete", failed: "Failed", cancelled: "Stopped" };

// One term per state, matching the rest of the app.
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
  return <section aria-label="Document preparation" style={{ padding: `${pwc.space.lg}px 0` }}>
    <div style={{ display: "flex", alignItems: "center", gap: pwc.space.md, flexWrap: "wrap" }}>
      <h2 style={{ ...ui.sectionTitle, margin: 0 }}>Document preparation</h2>
      <span data-testid="preparation-status">{snapshot ? labels[snapshot.status] : missingDocument ? "Unavailable" : "Connecting"}</span>
      {snapshot?.started_at != null && (active
        ? <ElapsedTimer startTime={snapshot.started_at * 1000} isRunning />
        : snapshot.updated_at != null ? <span>{formatElapsedMs(Math.max(0, snapshot.updated_at - snapshot.started_at) * 1000)}</span> : null)}
      {active && <button style={ui.buttonSecondary} disabled={busy} onClick={() => void act("cancel")}>Stop preparation</button>}
      {snapshot?.status === "not_started" && <button style={ui.buttonSecondary} disabled={busy} onClick={() => void act("retry")}>Start preparation</button>}
      {(snapshot?.status === "failed" || snapshot?.status === "cancelled") && <button style={ui.buttonSecondary} disabled={busy} onClick={() => void act("retry")}>Retry preparation</button>}
    </div>
    {!missingDocument && <div style={{ marginTop: pwc.space.lg }}>
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
    <p role="status" aria-live="polite" style={{ margin: `${pwc.space.md}px 0` }}>{connectionError ?? snapshot?.message ?? "Connecting to document preparation…"}</p>
    {snapshot?.prepared && snapshot.status !== "succeeded" && (
      <p style={{ margin: `${pwc.space.md}px 0` }}>Document prepared. {active && mapActive ? "Document map and notes inventory are being built." : "Notes inventory is not ready."}</p>
    )}
    {actionRequired !== "none" || snapshot?.status === "not_started" ? (
      <p style={{ margin: `0 0 ${pwc.space.md}px`, color: actionRequired === "confirm_setup" ? pwc.orange700 : pwc.grey700 }}>
        {actionRequired === "confirm_setup"
          ? "Review the detected filing details, then confirm setup."
          : actionRequired === "retry"
            ? repeatedFailure
              ? "The same step failed after retry. Upload a revised source document to continue."
              : "Retry resumes saved progress. If the same step fails again, upload a revised source document."
            : "Start document preparation."}
      </p>
    ) : null}
    {!missingDocument && <details open={snapshot?.status !== "succeeded"} style={{ marginTop: pwc.space.sm }}>
    <summary style={{ fontWeight: pwc.weight.medium, minHeight: 40 }}>
      {snapshot?.status === "succeeded" ? `Preparation details${total > 0 ? ` · ${total} pages` : ""}` : "Preparation steps"}
    </summary>
    <ol aria-label="Document preparation steps" style={{ listStyle: "none", margin: 0, padding: 0, borderTop: `1px solid ${pwc.grey200}` }}>
      {[
        {
          label: "Read source pages",
          detail: total > 0 ? `Pages captured: ${captured} of ${total}` : "Waiting for page count",
          state: detailStatus({ complete: readComplete, active: active && phase === "preparing_pages" && !readComplete, stopped: stopped && !readComplete, failed }),
        },
        {
          label: "Check page readings and continuations",
          detail: total > 0 ? `Pages checked: ${checked} of ${total}` : "Waiting for page count",
          state: detailStatus({ complete: checkComplete, active: active && phase === "preparing_pages" && checked > 0 && !checkComplete, stopped: stopped && !checkComplete, failed }),
        },
        {
          label: "Build document map",
          detail: phase === "reconciling_map" ? "Reconciling statement and note ownership" : "Identify statements, formats, notes and denomination",
          state: detailStatus({ complete: mapComplete, active: active && mapActive, stopped: stopped && pagesComplete, failed }),
        },
        {
          label: "Confirm detected setup",
          detail: "Review denomination, filing details and statement formats",
          state: actionRequired === "confirm_setup" ? "Action required" : "Waiting",
        },
      ].map((step) => (
        <li key={step.label} className="preparation-step-row" style={{ display: "grid", gridTemplateColumns: "minmax(180px, 1fr) minmax(220px, 2fr) 120px", gap: pwc.space.md, alignItems: "center", padding: `${pwc.space.sm}px 0`, borderBottom: `1px solid ${pwc.grey200}`, fontSize: 14 }}>
          <span style={{ fontFamily: pwc.fontHeading }}>{step.label}</span>
          <span style={{ color: pwc.grey700 }}>{step.detail}</span>
          <span style={{ textAlign: "right", color: step.state === "Action required" || step.state === "Failed" ? pwc.orange700 : pwc.grey700, fontWeight: step.state === "Working" || step.state === "Action required" ? pwc.weight.medium : pwc.weight.regular }}>
            {step.state}
          </span>
        </li>
      ))}
    </ol>
    </details>}
  </section>;
}
