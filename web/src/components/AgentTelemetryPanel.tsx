import { useEffect, useRef, useState } from "react";
import { userMessage } from "../lib/errors";
import { pwc } from "../lib/theme";
import { ui, uiClass } from "../lib/uiStyles";
import { fetchAgentTrace } from "../lib/api";
import { displayModelId } from "../lib/modelId";
import { notesTabLabel } from "../lib/appReducer";
import type { RunDetailJson, RunAgentJson, AgentTraceJson } from "../lib/types";

// ---------------------------------------------------------------------------
// AgentTelemetryPanel — the run-detail "Telemetry" tab (Phase 4).
//
// Renders, per agent: the persisted per-turn metrics (token deltas, tool
// activity, timing) as a dense table, plus an on-demand viewer for the
// verbatim conversation trace (what was sent / returned each turn). The
// metrics come from the detail payload; the heavy trace content is fetched
// lazily from GET /api/runs/{id}/agents/{stmt}/trace (hybrid storage).
//
// Data-dense surface on purpose (design memory: don't airify tables).
// ---------------------------------------------------------------------------

function fmtInt(n: number | null | undefined): string {
  return (n ?? 0).toLocaleString();
}

function fmtCost(n: number | null | undefined): string {
  return `$${(n ?? 0).toFixed(4)}`;
}

function fmtDuration(ms: number | null | undefined): string {
  const v = ms ?? 0;
  if (v < 1000) return `${v} ms`;
  return `${(v / 1000).toFixed(1)} s`;
}

/** Friendly agent name — mirrors RunDetailView's AgentCard logic. */
function agentDisplayName(a: RunAgentJson): string {
  if (a.statement_type === "CORRECTION") return "Correction";
  if (a.statement_type === "NOTES_VALIDATOR") return "Notes Validator";
  if (a.statement_type.startsWith("NOTES_")) return notesTabLabel(a.statement_type);
  return a.statement_type;
}

/** On-demand verbatim trace viewer for one agent. */
function TraceViewer({ runId, statement }: { runId: number; statement: string }) {
  const [state, setState] = useState<"idle" | "loading" | "loaded" | "error">("idle");
  const [trace, setTrace] = useState<AgentTraceJson | null>(null);
  const [error, setError] = useState<string>("");

  // Guard against setState after unmount — the user can switch tabs while a
  // trace is still loading (peer-review [7]). fetchAgentTrace has no signal
  // param, so a mounted flag is the lightest correct fix.
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const load = async () => {
    setState("loading");
    try {
      const t = await fetchAgentTrace(runId, statement);
      if (!mounted.current) return;
      setTrace(t);
      setState("loaded");
    } catch (e) {
      if (!mounted.current) return;
      setError(userMessage(e));
      setState("error");
    }
  };

  if (state === "idle") {
    return (
      <button type="button" onClick={load} className={uiClass.btnGhost} style={styles.traceButton}>
        View full request / response trace
      </button>
    );
  }
  if (state === "loading") {
    return <p style={styles.dim}>Loading trace…</p>;
  }
  if (state === "error") {
    return (
      <div style={styles.traceError} role="alert">
        {error}
      </div>
    );
  }
  // loaded — render the raw messages JSON in a scrollable pre. This is the
  // verbatim "what was sent and returned" the user asked to be able to read.
  return (
    <details open style={styles.traceDetails}>
      <summary style={styles.traceSummary}>
        Conversation trace ({(trace?.messages ?? []).length} messages)
      </summary>
      <pre style={styles.tracePre}>
        {JSON.stringify(trace?.messages ?? [], null, 2)}
      </pre>
    </details>
  );
}

/** Per-agent block: rollup line + per-turn metrics table + trace viewer. */
function AgentTelemetry({ runId, agent }: { runId: number; agent: RunAgentJson }) {
  const turns = agent.turns ?? [];
  const bd = agent.token_breakdown;
  return (
    <article style={styles.agentBlock}>
      <header style={styles.agentHeader}>
        <span style={styles.agentName}>{agentDisplayName(agent)}</span>
        {agent.variant && <span style={styles.agentVariant}>({agent.variant})</span>}
        <span style={styles.agentModel}>{displayModelId(agent.model)}</span>
        <span style={styles.agentRollup}>
          {fmtInt(agent.total_tokens)} tokens · {fmtCost(agent.total_cost)}
          {bd ? ` · ${bd.turn_count} turns · ${bd.tool_call_count} tool calls` : ""}
          {bd && (bd.cache_read_tokens || bd.cache_write_tokens)
            ? ` · cache ${fmtInt(bd.cache_read_tokens)} read / ${fmtInt(bd.cache_write_tokens)} write`
            : ""}
        </span>
      </header>

      {turns.length === 0 ? (
        <p style={styles.dim}>
          No per-turn telemetry was captured for this agent (older run, or it
          failed before any model turn).
        </p>
      ) : (
        <div style={styles.tableScroller}>
          <table style={styles.table}>
            <thead>
              <tr>
                <th style={styles.thNum}>#</th>
                <th style={styles.th}>Kind</th>
                <th style={styles.th}>Tools</th>
                <th style={styles.thNum}>Prompt</th>
                <th style={styles.thNum}>Completion</th>
                <th style={styles.thNum}>Cache read</th>
                <th style={styles.thNum}>Cache write</th>
                <th style={styles.thNum}>Turn total</th>
                <th style={styles.thNum}>Cumulative</th>
                <th style={styles.thNum}>Cost</th>
                <th style={styles.thNum}>Time</th>
              </tr>
            </thead>
            <tbody>
              {turns.map((t) => (
                <tr key={t.turn_index}>
                  <td style={styles.tdNum}>{t.turn_index}</td>
                  <td style={styles.td}>
                    {t.node_kind === "call_tools" ? "tools" : "model"}
                  </td>
                  <td style={styles.td}>{t.tool_names || "—"}</td>
                  <td style={styles.tdNum}>{fmtInt(t.prompt_tokens)}</td>
                  <td style={styles.tdNum}>{fmtInt(t.completion_tokens)}</td>
                  <td style={styles.tdNum}>{fmtInt(t.cache_read_tokens)}</td>
                  <td style={styles.tdNum}>{fmtInt(t.cache_write_tokens)}</td>
                  <td style={styles.tdNum}>{fmtInt(t.total_tokens)}</td>
                  <td style={styles.tdNum}>{fmtInt(t.cumulative_tokens)}</td>
                  <td style={styles.tdNum}>{fmtCost(t.cost_estimate)}</td>
                  <td style={styles.tdNum}>{fmtDuration(t.duration_ms)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <TraceViewer runId={runId} statement={agent.statement_type} />
    </article>
  );
}

export function AgentTelemetryPanel({ detail }: { detail: RunDetailJson }) {
  // Per-turn token splits are deltas of pydantic-ai's cumulative usage — exact
  // for timing and tool activity, best-effort for the token split. Surface
  // that honestly rather than over-trusting the numbers (CLAUDE.md gotcha #6).
  return (
    <div style={styles.root}>
      <p style={styles.caveat}>
        Per-turn token figures are derived from the model's cumulative usage
        and are approximate; timing and tool activity are exact.
      </p>
      {detail.agents.length === 0 ? (
        <p style={styles.dim}>No agents were recorded for this run.</p>
      ) : (
        detail.agents.map((a) => (
          <AgentTelemetry key={a.id} runId={detail.id} agent={a} />
        ))
      )}
    </div>
  );
}

const styles = {
  root: {
    display: "flex",
    flexDirection: "column" as const,
    gap: pwc.space.lg,
  } as React.CSSProperties,
  caveat: {
    margin: 0,
    fontSize: 12,
    fontStyle: "italic" as const,
    color: pwc.grey500,
    fontFamily: pwc.fontBody,
  } as React.CSSProperties,
  dim: {
    color: pwc.grey700,
    fontSize: 13,
    fontFamily: pwc.fontBody,
    margin: 0,
  } as React.CSSProperties,
  agentBlock: {
    display: "flex",
    flexDirection: "column" as const,
    gap: pwc.space.sm,
    border: `1px solid ${pwc.grey200}`,
    borderRadius: pwc.radius.sm,
    padding: pwc.space.md,
    background: pwc.white,
  } as React.CSSProperties,
  agentHeader: {
    display: "flex",
    alignItems: "baseline",
    gap: pwc.space.sm,
    flexWrap: "wrap" as const,
  } as React.CSSProperties,
  agentName: {
    fontFamily: pwc.fontMono,
    fontWeight: 600,
    fontSize: 14,
    color: pwc.grey900,
  } as React.CSSProperties,
  agentVariant: {
    color: pwc.grey500,
    fontSize: 13,
  } as React.CSSProperties,
  agentModel: {
    fontFamily: pwc.fontMono,
    fontSize: 12,
    color: pwc.grey700,
  } as React.CSSProperties,
  agentRollup: {
    marginLeft: "auto",
    fontFamily: pwc.fontMono,
    fontSize: 12,
    color: pwc.grey700,
  } as React.CSSProperties,
  tableScroller: {
    overflowX: "auto" as const,
    maxWidth: "100%",
  } as React.CSSProperties,
  table: {
    borderCollapse: "collapse" as const,
    width: "100%",
    fontFamily: pwc.fontMono,
    fontSize: 12,
  } as React.CSSProperties,
  th: {
    textAlign: "left" as const,
    padding: `${pwc.space.xs}px ${pwc.space.sm}px`,
    borderBottom: `1px solid ${pwc.grey200}`,
    color: pwc.grey700,
    fontWeight: 600,
    whiteSpace: "nowrap" as const,
  } as React.CSSProperties,
  thNum: {
    textAlign: "right" as const,
    padding: `${pwc.space.xs}px ${pwc.space.sm}px`,
    borderBottom: `1px solid ${pwc.grey200}`,
    color: pwc.grey700,
    fontWeight: 600,
    whiteSpace: "nowrap" as const,
  } as React.CSSProperties,
  td: {
    padding: `${pwc.space.xs}px ${pwc.space.sm}px`,
    borderBottom: `1px solid ${pwc.grey100}`,
    color: pwc.grey900,
  } as React.CSSProperties,
  tdNum: {
    textAlign: "right" as const,
    padding: `${pwc.space.xs}px ${pwc.space.sm}px`,
    borderBottom: `1px solid ${pwc.grey100}`,
    color: pwc.grey900,
    whiteSpace: "nowrap" as const,
  } as React.CSSProperties,
  traceButton: {
    ...ui.buttonGhost,
    ...ui.buttonSm,
    alignSelf: "flex-start" as const,
  } as React.CSSProperties,
  traceError: {
    ...ui.alertError,
    padding: pwc.space.sm,
    fontSize: 12,
  } as React.CSSProperties,
  traceDetails: {
    border: `1px solid ${pwc.grey200}`,
    borderRadius: pwc.radius.sm,
  } as React.CSSProperties,
  traceSummary: {
    padding: pwc.space.sm,
    cursor: "pointer",
    fontFamily: pwc.fontHeading,
    fontSize: 12,
    fontWeight: 600,
    color: pwc.grey700,
  } as React.CSSProperties,
  tracePre: {
    margin: 0,
    padding: pwc.space.md,
    maxHeight: 480,
    overflow: "auto" as const,
    background: pwc.grey50,
    fontFamily: pwc.fontMono,
    fontSize: 11,
    lineHeight: 1.5,
    color: pwc.grey900,
    whiteSpace: "pre-wrap" as const,
    wordBreak: "break-word" as const,
  } as React.CSSProperties,
} as const;
