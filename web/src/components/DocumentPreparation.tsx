import { useEffect, useRef, useState } from "react";
import { apiFetch } from "../lib/api";
import type { PreparationSnapshot } from "../lib/types";
import { ApiError, userMessage } from "../lib/errors";
import { pwc } from "../lib/theme";
import { ui } from "../lib/uiStyles";
import { formatElapsedMs } from "../lib/time";
import { ElapsedTimer } from "./ElapsedTimer";

const activeStatuses = new Set(["queued", "working", "retrying"]);
const labels = { not_started: "Waiting", queued: "Queued", working: "Working", retrying: "Retrying", succeeded: "Complete", failed: "Failed", cancelled: "Cancelled" };

/** Upload-owned progress; terminal outcomes come only from persisted server state. */
export function DocumentPreparation({ sessionId, onSnapshot, hideCompleted = false }: {
  sessionId: string;
  onSnapshot?: (snapshot: PreparationSnapshot) => void;
  hideCompleted?: boolean;
}) {
  const [snapshot, setSnapshot] = useState<PreparationSnapshot | null>(null);
  const [connectionError, setConnectionError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const callback = useRef(onSnapshot);
  callback.current = onSnapshot;
  const operation = useRef(0);
  const mounted = useRef(true);
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

  if (hideCompleted && snapshot?.status === "succeeded") return null;
  const active = snapshot != null && activeStatuses.has(snapshot.status);
  return <section aria-label="Document preparation" style={{ padding: `${pwc.space.lg}px 0`, borderBottom: `1px solid ${pwc.grey200}`, marginBottom: pwc.space.lg }}>
    <div style={{ display: "flex", alignItems: "center", gap: pwc.space.md, flexWrap: "wrap" }}>
      <h2 style={{ ...ui.sectionTitle, margin: 0 }}>Document preparation</h2>
      <span>{snapshot ? labels[snapshot.status] : "Connecting"}</span>
      {snapshot?.started_at != null && (active
        ? <ElapsedTimer startTime={snapshot.started_at * 1000} isRunning />
        : snapshot.updated_at != null ? <span>{formatElapsedMs(Math.max(0, snapshot.updated_at - snapshot.started_at) * 1000)}</span> : null)}
      {active && <button style={ui.buttonSecondary} disabled={busy} onClick={() => void act("cancel")}>Stop preparation</button>}
      {(snapshot?.status === "failed" || snapshot?.status === "cancelled") && <button style={ui.buttonSecondary} disabled={busy} onClick={() => void act("retry")}>Retry preparation</button>}
    </div>
    <p role="status" aria-live="polite">{connectionError ?? snapshot?.message ?? "Connecting to document preparation…"}</p>
    {snapshot?.prepared && snapshot.status !== "succeeded" && <p>Document prepared. {snapshot.stage === "scouting" && active ? "Document map and notes inventory are being built." : "Notes inventory is not ready."}</p>}
    {snapshot?.total != null && snapshot.total > 0 && <div style={{ display: "flex", gap: pwc.space.lg, flexWrap: "wrap" }}>
      <span>Pages captured: {snapshot.captured ?? 0} of {snapshot.total}</span>
      <span>Pages checked: {snapshot.checked ?? snapshot.verified ?? 0} of {snapshot.total}</span>
    </div>}
  </section>;
}
