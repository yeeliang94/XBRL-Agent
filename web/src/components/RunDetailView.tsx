import { pwc } from "../lib/theme";
import { runStatusDisplay, agentStatusDisplay } from "../lib/runStatus";
import type { RunStatusDisplay } from "../lib/runStatus";
import type { RunDetailJson, RunAgentJson, CrossCheckResult } from "../lib/types";
import { ValidatorTab } from "./ValidatorTab";
import { AgentTimeline } from "./AgentTimeline";
import { buildToolTimeline } from "../lib/buildToolTimeline";

// ---------------------------------------------------------------------------
// RunDetailView — hydrated detail panel for a single past run.
//
// Pure presentational. The parent owns the detail payload (HistoryPage
// fetches it) and passes callbacks for the destructive / navigational
// actions. Cross-check rendering is reused from ValidatorTab so live runs
// and past runs share a single visual treatment.
// ---------------------------------------------------------------------------

export interface RunDetailViewProps {
  detail: RunDetailJson;
  onDownload: (runId: number) => void;
  onDelete: (runId: number) => void;
}

/** Strip a PydanticAI `Model(...)` repr wrapper if one leaked into storage.
 *  Legacy runs (pre-v2 schema) sometimes recorded the raw repr of the model
 *  object — e.g. "GoogleModel(model_name='gemini-3-flash-preview', ...)" —
 *  instead of a clean id. For display purposes we extract the inner
 *  `model_name=...` if present, else fall back to the raw string. New runs
 *  already store a clean id via `_model_id()` server-side, so this is a
 *  defensive no-op for them. */
function displayModelId(raw: string | null | undefined): string {
  if (!raw) return "—";
  const reprMatch = /^[A-Za-z_][A-Za-z0-9_]*\(.*model_name=['"]([^'"]+)['"]/.exec(raw);
  if (reprMatch) return reprMatch[1];
  // Some older rows stored just "GoogleModel(gemini-3-flash-preview)" — pull
  // the first positional arg out of the parens if it looks like an id.
  const positional = /^[A-Za-z_][A-Za-z0-9_]*\(([A-Za-z0-9_\-.:/]+)[,)]/.exec(raw);
  if (positional) return positional[1];
  return raw;
}

/** Render a status badge from a precomputed display. Caller picks
 *  runStatusDisplay vs agentStatusDisplay so the right vocabulary is used
 *  in each context (run-level vs per-agent enums differ slightly). */
function statusBadge(display: RunStatusDisplay) {
  return (
    <span
      style={{
        ...styles.badge,
        color: display.color,
        background: display.bg,
      }}
    >
      {display.label}
    </span>
  );
}

/** Render a nested config key/value section in a compact form. */
function ConfigBlock({ config }: { config: Record<string, unknown> | null }) {
  if (!config) {
    return <p style={styles.dim}>No run config captured for this run.</p>;
  }
  const entries: { label: string; value: string }[] = [];
  const stmts = Array.isArray(config.statements)
    ? (config.statements as string[]).join(", ")
    : "—";
  entries.push({ label: "Statements", value: stmts });

  const variants = (config.variants ?? {}) as Record<string, string>;
  if (Object.keys(variants).length > 0) {
    entries.push({
      label: "Variants",
      value: Object.entries(variants)
        .map(([k, v]) => `${k}=${v}`)
        .join(", "),
    });
  }
  const models = (config.models ?? {}) as Record<string, string>;
  if (Object.keys(models).length > 0) {
    entries.push({
      label: "Model overrides",
      value: Object.entries(models)
        .map(([k, v]) => `${k}=${v}`)
        .join(", "),
    });
  }
  entries.push({
    label: "Scout",
    value: config.use_scout ? "Enabled" : "Disabled",
  });
  return (
    <dl style={styles.dl}>
      {entries.map((e) => (
        <div key={e.label} style={styles.dlRow}>
          <dt style={styles.dt}>{e.label}</dt>
          <dd style={styles.dd}>{e.value}</dd>
        </div>
      ))}
    </dl>
  );
}

/** Convert the wire-shape cross_checks into the shape ValidatorTab expects.
 *  They already match structurally — this is a type assertion cast kept
 *  explicit so a future divergence in either shape shows up as a compile
 *  error. */
function crossChecksForValidator(
  rows: RunDetailJson["cross_checks"],
): CrossCheckResult[] {
  return rows.map((r) => ({
    name: r.name,
    status: r.status,
    expected: r.expected,
    actual: r.actual,
    diff: r.diff,
    tolerance: r.tolerance,
    message: r.message,
  }));
}

// Phase 9: one card per agent. Header shows statement, status, model,
// total tokens; body is an AgentTimeline fed by the persisted events so
// past runs replay the same ToolCallCard rows as a live run.
function AgentCard({ agent }: { agent: RunAgentJson }) {
  // buildToolTimeline is pure and cheap relative to the event list sizes
  // we ship (~50-200 events per agent), so a per-render call is fine.
  const toolTimeline = buildToolTimeline(agent.events);
  return (
    <article data-testid="run-detail-agent" style={styles.agentCard}>
      <header style={styles.agentHeader}>
        <div style={styles.agentTitleRow}>
          <span style={styles.agentStatement}>{agent.statement_type}</span>
          {agent.variant && (
            <span style={styles.agentVariant}>({agent.variant})</span>
          )}
          {statusBadge(agentStatusDisplay(agent.status))}
        </div>
        <div style={styles.agentMetaRow}>
          <span style={styles.agentModel}>{displayModelId(agent.model)}</span>
          <span style={styles.agentTokens}>
            {agent.total_tokens != null
              ? `${agent.total_tokens.toLocaleString()} tokens`
              : "— tokens"}
          </span>
        </div>
      </header>
      <AgentTimeline
        events={agent.events}
        toolTimeline={toolTimeline}
        isRunning={false}
      />
    </article>
  );
}

export function RunDetailView({ detail, onDownload, onDelete }: RunDetailViewProps) {
  const canDownload = !!detail.merged_workbook_path;
  // Legacy detection: rows created before the v2 schema never captured a
  // run_config, merged_workbook_path, or per-agent token counts. Rather
  // than leaving several sections mysteriously empty, tag the run so the
  // user knows the gaps are expected and not a data-loss bug.
  const isLegacy = detail.config == null;
  // Peer-review [CRITICAL] guard: block deletion of runs that are still
  // executing. Deleting mid-run cascades through run_agents, agent_events,
  // and cross_checks while the coordinator is still writing new rows —
  // creating orphan children or FK-violation crashes. The backend also
  // returns 409 for this case, but disabling the button in the UI means
  // the bad click is impossible in the normal flow.
  const canDelete = detail.status !== "running";

  const handleDelete = () => {
    // Confirm before destructive action — no native dialog in jsdom so the
    // tests stub window.confirm. We accept the slightly-ugly native prompt
    // here instead of pulling in a modal dependency.
    if (window.confirm(`Delete run #${detail.id} (${detail.pdf_filename})?`)) {
      onDelete(detail.id);
    }
  };

  return (
    <div style={styles.container}>
      <header style={styles.header}>
        <div>
          <h3 style={styles.filename}>{detail.pdf_filename}</h3>
          <div style={styles.metaRow}>
            {statusBadge(runStatusDisplay(detail.status))}
            {isLegacy && (
              <span
                style={styles.legacyBadge}
                title="This run was recorded before the v2 schema — run config, merged workbook path, and token counts were not captured."
              >
                Legacy run (pre-v2)
              </span>
            )}
            <span style={styles.dim}>
              {new Date(detail.created_at).toLocaleString()}
            </span>
          </div>
        </div>
        <div style={styles.actions}>
          <button
            type="button"
            onClick={() => onDownload(detail.id)}
            disabled={!canDownload}
            style={canDownload ? styles.primaryButton : styles.primaryButtonDisabled}
            title={
              canDownload
                ? "Download merged workbook"
                : "No merged workbook (run failed before merge)"
            }
          >
            Download filled workbook
          </button>
          <button
            type="button"
            onClick={handleDelete}
            disabled={!canDelete}
            style={canDelete ? styles.dangerButton : styles.dangerButtonDisabled}
            title={
              canDelete
                ? "Delete run from history (on-disk files are kept)"
                : "Can't delete a run that's still in progress — wait for it to finish or abort it first."
            }
          >
            Delete run
          </button>
        </div>
      </header>

      <section style={styles.section}>
        <h4 style={styles.sectionHeading}>Run configuration</h4>
        <ConfigBlock config={detail.config} />
      </section>

      <section style={styles.section} data-testid="run-detail-agents">
        <h4 style={styles.sectionHeading}>Agents</h4>
        {detail.agents.length === 0 ? (
          <p style={styles.dim}>No agents were recorded for this run.</p>
        ) : (
          <div style={styles.agentStack}>
            {detail.agents.map((agent) => (
              <AgentCard key={agent.id} agent={agent} />
            ))}
          </div>
        )}
      </section>

      <section style={styles.section}>
        <h4 style={styles.sectionHeading}>Cross-checks</h4>
        {/* Horizontal scroll fallback for the 6-column cross-check table.
            Even in the 920px modal, a long "message" column can push the
            table past the available width; the scroller keeps the column
            readable instead of letting it get clipped. */}
        <div style={styles.crossCheckScroller}>
          <ValidatorTab crossChecks={crossChecksForValidator(detail.cross_checks)} />
        </div>
      </section>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Styles
// ---------------------------------------------------------------------------

const styles = {
  // No border/shadow here — the parent (RunDetailModal) provides the
  // modal chrome. Keeping an outer card inside the modal would produce
  // a nested-card look that doubles the visual noise.
  container: {
    display: "flex",
    flexDirection: "column" as const,
    gap: pwc.space.lg,
    // Right padding leaves room for the modal's absolute-positioned close
    // button so the header title never runs into the ×.
    paddingRight: pwc.space.xl,
  } as React.CSSProperties,
  crossCheckScroller: {
    overflowX: "auto" as const,
    maxWidth: "100%",
  } as React.CSSProperties,
  header: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "flex-start",
    gap: pwc.space.lg,
    flexWrap: "wrap" as const,
  } as React.CSSProperties,
  filename: {
    fontFamily: pwc.fontHeading,
    fontSize: 18,
    fontWeight: 600,
    color: pwc.grey900,
    margin: 0,
  } as React.CSSProperties,
  metaRow: {
    display: "flex",
    alignItems: "center",
    gap: pwc.space.sm,
    marginTop: pwc.space.xs,
  } as React.CSSProperties,
  dim: {
    color: pwc.grey700,
    fontSize: 13,
    fontFamily: pwc.fontBody,
  } as React.CSSProperties,
  actions: {
    display: "flex",
    gap: pwc.space.sm,
  } as React.CSSProperties,
  primaryButton: {
    padding: `${pwc.space.sm}px ${pwc.space.lg}px`,
    fontFamily: pwc.fontHeading,
    fontSize: 14,
    fontWeight: 600,
    color: pwc.white,
    background: pwc.orange500,
    border: "none",
    borderRadius: pwc.radius.sm,
    cursor: "pointer",
  } as React.CSSProperties,
  primaryButtonDisabled: {
    padding: `${pwc.space.sm}px ${pwc.space.lg}px`,
    fontFamily: pwc.fontHeading,
    fontSize: 14,
    fontWeight: 600,
    color: pwc.grey500,
    background: pwc.grey100,
    border: "none",
    borderRadius: pwc.radius.sm,
    cursor: "not-allowed",
  } as React.CSSProperties,
  dangerButton: {
    padding: `${pwc.space.sm}px ${pwc.space.lg}px`,
    fontFamily: pwc.fontHeading,
    fontSize: 14,
    fontWeight: 600,
    color: pwc.error,
    background: pwc.white,
    border: `1px solid ${pwc.error}`,
    borderRadius: pwc.radius.sm,
    cursor: "pointer",
  } as React.CSSProperties,
  dangerButtonDisabled: {
    padding: `${pwc.space.sm}px ${pwc.space.lg}px`,
    fontFamily: pwc.fontHeading,
    fontSize: 14,
    fontWeight: 600,
    color: pwc.grey500,
    background: pwc.grey100,
    border: `1px solid ${pwc.grey200}`,
    borderRadius: pwc.radius.sm,
    cursor: "not-allowed",
  } as React.CSSProperties,
  section: {
    display: "flex",
    flexDirection: "column" as const,
    gap: pwc.space.sm,
  } as React.CSSProperties,
  sectionHeading: {
    fontFamily: pwc.fontHeading,
    fontSize: 14,
    fontWeight: 600,
    color: pwc.grey700,
    margin: 0,
    textTransform: "uppercase" as const,
    letterSpacing: 0.5,
  } as React.CSSProperties,
  dl: {
    display: "flex",
    flexDirection: "column" as const,
    gap: pwc.space.xs,
    margin: 0,
  } as React.CSSProperties,
  dlRow: {
    display: "flex",
    gap: pwc.space.sm,
    fontFamily: pwc.fontBody,
    fontSize: 14,
  } as React.CSSProperties,
  dt: {
    fontWeight: 600,
    color: pwc.grey700,
    minWidth: 140,
  } as React.CSSProperties,
  dd: {
    margin: 0,
    color: pwc.grey900,
  } as React.CSSProperties,
  // Phase 9: per-agent card stack replaces the stats table. Each card
  // gets its own rounded border so the agent boundaries stay visible
  // when several timelines share the detail view.
  agentStack: {
    display: "flex",
    flexDirection: "column" as const,
    gap: pwc.space.md,
  } as React.CSSProperties,
  agentCard: {
    display: "flex",
    flexDirection: "column" as const,
    gap: pwc.space.xs,
    border: `1px solid ${pwc.grey200}`,
    borderRadius: pwc.radius.sm,
    background: pwc.white,
  } as React.CSSProperties,
  agentHeader: {
    display: "flex",
    flexDirection: "column" as const,
    gap: 4,
    padding: `${pwc.space.sm}px ${pwc.space.md}px`,
    borderBottom: `1px solid ${pwc.grey100}`,
  } as React.CSSProperties,
  agentTitleRow: {
    display: "flex",
    alignItems: "center",
    gap: pwc.space.sm,
    flexWrap: "wrap" as const,
  } as React.CSSProperties,
  agentStatement: {
    fontFamily: pwc.fontMono,
    fontWeight: 600,
    fontSize: 14,
    color: pwc.grey900,
  } as React.CSSProperties,
  agentVariant: {
    color: pwc.grey500,
    fontSize: 13,
  } as React.CSSProperties,
  agentMetaRow: {
    display: "flex",
    alignItems: "center",
    gap: pwc.space.md,
    fontFamily: pwc.fontMono,
    fontSize: 12,
    color: pwc.grey700,
  } as React.CSSProperties,
  agentModel: {
    // empty — placeholder in case we later want to style model differently
  } as React.CSSProperties,
  agentTokens: {
    marginLeft: "auto",
  } as React.CSSProperties,
  badge: {
    display: "inline-block",
    padding: `2px ${pwc.space.sm}px`,
    borderRadius: pwc.radius.sm,
    fontSize: 12,
    fontWeight: 600,
    lineHeight: 1.6,
  } as React.CSSProperties,
  legacyBadge: {
    display: "inline-block",
    padding: `2px ${pwc.space.sm}px`,
    borderRadius: pwc.radius.sm,
    fontSize: 11,
    fontWeight: 600,
    lineHeight: 1.6,
    color: pwc.grey700,
    background: pwc.grey100,
    border: `1px solid ${pwc.grey200}`,
    textTransform: "uppercase" as const,
    letterSpacing: 0.3,
  } as React.CSSProperties,
} as const;
