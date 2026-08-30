import { useEffect, useRef, useState } from "react";
import { userMessage } from "../lib/errors";
import { pwc } from "../lib/theme";
import { ui, uiClass } from "../lib/uiStyles";
import { fetchAgentTrace, fetchAgentTraceManifest } from "../lib/api";
import { displayModelId } from "../lib/modelId";
import { notesTabLabel } from "../lib/appReducer";
import { pseudoAgentLabel } from "../lib/vocabulary";
import type {
  RunDetailJson,
  RunAgentJson,
  AgentTraceJson,
  RunIncidentJson,
} from "../lib/types";

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

function incidentNextAction(incident: RunIncidentJson): string {
  const actions: Record<string, string> = {
    unknown_statement: "Choose a supported statement and start a new extraction.",
    unknown_notes_template: "Choose a supported notes template and try again.",
    unknown_variant: "Review the statement format selection and try again.",
    variant_standard_mismatch: "Choose a format available for this filing standard.",
    model_override_failed: "Check the selected model configuration and connection.",
    model_setup_failed: "Check model credentials and connectivity, then retry.",
    invalid_infopack: "Run Auto-detect again before restarting extraction.",
    canonical_bootstrap_failed: "Restart the server after resolving the taxonomy import error.",
    benchmark_not_found: "Choose an existing benchmark or disable eval testing.",
    benchmark_scope_mismatch: "Choose a benchmark with the same standard and filing level.",
    scout_failed: "Retry Auto-detect or continue without page suggestions.",
    unhandled_orchestration_exception: "Use the support reference below to investigate server logs.",
  };
  return actions[incident.error_code]
    ?? "Review the technical details and support reference before retrying.";
}

function RunIncidentPanel({ incidents }: { incidents: RunIncidentJson[] }) {
  if (incidents.length === 0) return null;
  return (
    <section aria-label="Run incidents" style={styles.incidentSection}>
      <h3 style={styles.sectionTitle}>Run incidents</h3>
      {incidents.map((incident) => (
        <article
          key={incident.id}
          role={incident.severity === "fatal" ? "alert" : "status"}
          style={{
            ...styles.incidentCard,
            ...(incident.severity === "fatal"
              ? styles.incidentCardFatal
              : styles.incidentCardAdvisory),
          }}
        >
          <div style={styles.incidentHeader}>
            <strong>{incident.user_message}</strong>
            <span style={styles.incidentCode}>{incident.error_code.replace(/_/g, " ")}</span>
          </div>
          <p style={styles.incidentAction}>{incidentNextAction(incident)}</p>
          <div style={styles.incidentMeta}>
            {incident.stage && <span>Stage: {incident.stage.replace(/_/g, " ")}</span>}
            {incident.exception_type && <span>Type: {incident.exception_type}</span>}
            {incident.correlation_id && <span>Support reference: {incident.correlation_id}</span>}
          </div>
          {(incident.technical_message || incident.details) && (
            <details style={styles.technicalDetails}>
              <summary>Technical details</summary>
              <pre style={styles.technicalPre}>{JSON.stringify({
                message: incident.technical_message,
                details: incident.details,
              }, null, 2)}</pre>
            </details>
          )}
        </article>
      ))}
    </section>
  );
}

function RunEventTimeline({ detail }: { detail: RunDetailJson }) {
  const events = detail.run_events ?? [];
  if (events.length === 0) return null;
  return (
    <details style={styles.runEvents}>
      <summary style={styles.runEventsSummary}>
        Coordinator timeline ({events.length} {events.length === 1 ? "event" : "events"})
      </summary>
      <ol style={styles.runEventList}>
        {events.map((event, index) => {
          const data = event.data as { stage?: string; phase?: string; message?: string };
          const label = data.stage ?? data.phase ?? data.message;
          return (
            <li key={`${event.timestamp}-${event.event}-${index}`} style={styles.runEventRow}>
              <span style={styles.runEventName}>{event.event.replace(/_/g, " ")}</span>
              {label && <span>{label.replace(/_/g, " ")}</span>}
              <time>{new Date(event.timestamp * 1000).toLocaleString()}</time>
            </li>
          );
        })}
      </ol>
    </details>
  );
}

/** Friendly agent name — mirrors RunDetailView's AgentCard logic.
 *  Pseudo-agents resolve through the central vocabulary ("AI review" /
 *  "Notes review") so this panel matches the rest of the product. */
function agentDisplayName(a: RunAgentJson): string {
  const pseudo = pseudoAgentLabel(a.statement_type);
  if (pseudo) return pseudo;
  if (a.statement_type.startsWith("NOTES_")) return notesTabLabel(a.statement_type);
  return a.statement_type;
}

/** On-demand verbatim trace viewer for one agent. */
function TraceViewer({ runId, statement }: { runId: number; statement: string }) {
  const [state, setState] = useState<"idle" | "loading" | "loaded" | "error">("idle");
  const [trace, setTrace] = useState<AgentTraceJson | null>(null);
  const [error, setError] = useState<string>("");
  const [traceOptions, setTraceOptions] = useState<Array<{ id: string; label: string }>>([]);
  const [selectedTraceId, setSelectedTraceId] = useState<string>("");

  // Guard against setState after unmount — the user can switch tabs while a
  // trace is still loading (peer-review [7]). fetchAgentTrace has no signal
  // param, so a mounted flag is the lightest correct fix.
  const mounted = useRef(true);
  const requestSequence = useRef(0);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const load = async () => {
    const request = ++requestSequence.current;
    setState("loading");
    try {
      const manifest = await fetchAgentTraceManifest(runId, statement);
      const first = manifest.traces[0];
      if (!first) throw new Error("No conversation trace was captured for this agent.");
      const t = await fetchAgentTrace(runId, statement, first.id);
      if (!mounted.current || request !== requestSequence.current) return;
      setTraceOptions(manifest.traces.map(({ id, label }) => ({ id, label })));
      setSelectedTraceId(first.id);
      setTrace(t);
      setState("loaded");
    } catch (e) {
      if (!mounted.current || request !== requestSequence.current) return;
      setError(userMessage(e));
      setState("error");
    }
  };

  const selectTrace = async (traceId: string) => {
    const request = ++requestSequence.current;
    setSelectedTraceId(traceId);
    setState("loading");
    try {
      const next = await fetchAgentTrace(runId, statement, traceId);
      if (!mounted.current || request !== requestSequence.current) return;
      setTrace(next);
      setState("loaded");
    } catch (e) {
      if (!mounted.current || request !== requestSequence.current) return;
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
    <div style={styles.traceStack}>
      {traceOptions.length > 1 && (
        <label style={styles.tracePickerLabel}>
          Trace
          <select
            aria-label="Conversation trace"
            value={selectedTraceId}
            onChange={(event) => void selectTrace(event.target.value)}
            style={styles.tracePicker}
          >
            {traceOptions.map((option) => (
              <option key={option.id} value={option.id}>{option.label}</option>
            ))}
          </select>
        </label>
      )}
      <details open style={styles.traceDetails}>
        <summary style={styles.traceSummary}>
          Conversation trace ({(trace?.messages ?? []).length} messages)
        </summary>
        <pre style={styles.tracePre}>
          {JSON.stringify(trace?.messages ?? [], null, 2)}
        </pre>
      </details>
    </div>
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
          {fmtInt(agent.total_tokens)} tokens ·{" "}
          <span
            style={agent.pricing_unconfirmed ? styles.costEstimated : undefined}
            title={
              agent.pricing_unconfirmed
                ? "This model's rates are a placeholder, not a published rate card. The cost is indicative only."
                : undefined
            }
          >
            {fmtCost(agent.total_cost)}
            {agent.pricing_unconfirmed ? " (est. rate)" : ""}
          </span>
          {bd ? ` · ${bd.turn_count} turns · ${bd.tool_call_count} tool calls` : ""}
          {bd?.thinking_tokens
            ? ` · ${fmtInt(bd.thinking_tokens)} reasoning tokens`
            : ""}
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
                <th style={styles.thNum}>Reasoning</th>
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
                  <td style={styles.tdNum}>{fmtInt(t.thinking_tokens)}</td>
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
      <RunIncidentPanel incidents={detail.incidents ?? []} />
      <RunEventTimeline detail={detail} />
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
  incidentSection: {
    display: "flex",
    flexDirection: "column" as const,
    gap: pwc.space.sm,
  } as React.CSSProperties,
  sectionTitle: {
    margin: 0,
    fontFamily: pwc.fontHeading,
    fontSize: 15,
    fontWeight: pwc.weight.semibold,
  } as React.CSSProperties,
  incidentCard: {
    borderRadius: pwc.radius.md,
    padding: pwc.space.md,
  } as React.CSSProperties,
  incidentCardFatal: {
    border: `1px solid ${pwc.errorBorder}`,
    borderLeft: `4px solid ${pwc.error}`,
    background: pwc.errorBg,
  } as React.CSSProperties,
  incidentCardAdvisory: {
    border: `1px solid ${pwc.warningBorder}`,
    borderLeft: `4px solid ${pwc.warning}`,
    background: pwc.warningBg,
  } as React.CSSProperties,
  incidentHeader: {
    display: "flex",
    alignItems: "flex-start",
    justifyContent: "space-between",
    gap: pwc.space.md,
  } as React.CSSProperties,
  incidentCode: {
    fontFamily: pwc.fontMono,
    fontSize: 11,
    color: pwc.grey700,
    whiteSpace: "nowrap" as const,
  } as React.CSSProperties,
  incidentAction: {
    margin: `${pwc.space.sm}px 0 0`,
    color: pwc.grey900,
  } as React.CSSProperties,
  incidentMeta: {
    display: "flex",
    flexWrap: "wrap" as const,
    gap: `${pwc.space.xs}px ${pwc.space.lg}px`,
    marginTop: pwc.space.sm,
    color: pwc.grey700,
    fontFamily: pwc.fontMono,
    fontSize: 11,
  } as React.CSSProperties,
  technicalDetails: {
    marginTop: pwc.space.sm,
    fontSize: 12,
  } as React.CSSProperties,
  technicalPre: {
    margin: `${pwc.space.sm}px 0 0`,
    padding: pwc.space.sm,
    maxHeight: 240,
    overflow: "auto" as const,
    background: pwc.white,
    fontFamily: pwc.fontMono,
    fontSize: 11,
    whiteSpace: "pre-wrap" as const,
  } as React.CSSProperties,
  runEvents: {
    border: `1px solid ${pwc.grey200}`,
    borderRadius: pwc.radius.md,
  } as React.CSSProperties,
  runEventsSummary: {
    padding: pwc.space.md,
    cursor: "pointer",
    fontWeight: pwc.weight.semibold,
  } as React.CSSProperties,
  runEventList: {
    margin: 0,
    padding: `0 ${pwc.space.md}px ${pwc.space.md}px ${pwc.space.xxl}px`,
  } as React.CSSProperties,
  runEventRow: {
    display: "grid",
    gridTemplateColumns: "minmax(150px, 0.7fr) minmax(160px, 1fr) auto",
    gap: pwc.space.sm,
    padding: `${pwc.space.xs}px 0`,
    color: pwc.grey700,
    fontSize: 12,
  } as React.CSSProperties,
  runEventName: {
    color: pwc.grey900,
    fontFamily: pwc.fontMono,
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
    border: "none",
    borderBottom: `1px solid ${pwc.grey200}`,
    borderRadius: 0,
    padding: `${pwc.space.md}px 0 ${pwc.space.lg}px`,
    background: "transparent",
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
  // A cost derived from a placeholder rate. Dotted underline rather than a
  // colour: this is "read the caveat", not an error.
  costEstimated: {
    borderBottom: `1px dotted ${pwc.grey700}`,
    cursor: "help",
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
  traceStack: {
    display: "flex",
    flexDirection: "column" as const,
    gap: pwc.space.sm,
  } as React.CSSProperties,
  tracePickerLabel: {
    display: "flex",
    alignItems: "center",
    gap: pwc.space.sm,
    color: pwc.grey700,
    fontSize: 12,
  } as React.CSSProperties,
  tracePicker: {
    ...ui.input,
    minWidth: 260,
    fontFamily: pwc.fontMono,
    fontSize: 12,
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
