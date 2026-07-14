import { useEffect, useMemo, useState } from "react";
import { pwc } from "../lib/theme";
import { ui } from "../lib/uiStyles";
import { fetchRepeatGroup } from "../lib/api";
import type { RepeatGroupJson, ConsistencyDisagreement } from "../lib/types";

// ---------------------------------------------------------------------------
// ConsistencyPanel — run-to-run agreement for a repeat group (Evals workspace,
// Step D3). Mounts on the Overview tab of any grouped run. Polls the group
// while it's still running (the later repeats finish after the first one's
// stream ends), then shows the headline agreement %, a disagreement table
// (presence vs value, sortable by spread), and — when a benchmark is attached
// — the systematic-vs-stochastic cross. A section, NOT a role="tab" (gotcha #7).
// ---------------------------------------------------------------------------

export interface ConsistencyPanelProps {
  groupId: number;
}

function pct(v: number | null): string {
  return v == null ? "—" : `${Math.round(v * 100)}%`;
}

export function ConsistencyPanel({ groupId }: ConsistencyPanelProps) {
  const [group, setGroup] = useState<RepeatGroupJson | null>(null);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const poll = () => {
      fetchRepeatGroup(groupId)
        .then((g) => {
          if (cancelled) return;
          setGroup(g);
          setLoaded(true);
          // Keep polling while the group is still running (later repeats land
          // after the first repeat's SSE stream closes). Stop once terminal.
          if (g && g.status === "running") {
            timer = setTimeout(poll, 5000);
          }
        })
        .catch(() => {
          if (!cancelled) setLoaded(true);
        });
    };
    poll();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [groupId]);

  if (!loaded) {
    return <p style={styles.muted}>Loading consistency…</p>;
  }
  if (group == null) {
    return null;
  }

  const c = group.consistency;
  const finished = group.runs.filter(
    (r) => r.status === "completed" || r.status === "completed_with_errors",
  ).length;
  // Per-repeat accuracy (PRD: "how right" next to "how stable", Step 11).
  const gradedRepeats = group.runs.filter((r) => r.accuracy != null);

  return (
    <section data-testid="consistency-panel" style={styles.wrap}>
      <h4 style={styles.sectionHeading}>
        Run-to-run consistency
        <span style={styles.subtle}>
          {" "}· {finished}/{group.repeats_requested} repeats finished
          {group.status === "partial" && " (partial)"}
          {group.status === "running" && " (running…)"}
        </span>
      </h4>

      {gradedRepeats.length > 0 && (
        <div data-testid="consistency-repeat-accuracies" style={styles.repeatStrip}>
          {gradedRepeats.map((r) => (
            <span key={r.id} style={styles.repeatChip}>
              Repeat {(r.repeat_index ?? 0) + 1}: {pct(r.accuracy ?? null)}
            </span>
          ))}
        </div>
      )}

      {!c || !c.available ? (
        <div data-testid="consistency-unavailable" style={styles.card}>
          <div style={styles.muted}>
            Consistency needs at least 2 finished repeats
            {group.status === "running"
              ? " — still running."
              : " — unavailable for this group."}
          </div>
        </div>
      ) : (
        <>
          <div style={styles.card}>
            <div data-testid="consistency-headline" style={styles.headline}>
              {pct(c.consistency)}
            </div>
            <div style={styles.fraction}>
              {c.unanimous} / {c.union_slots} value slots identical across all{" "}
              {c.n_repeats} repeats
            </div>
          </div>

          <div style={styles.detailGrid}>
            <Metric label="Presence disagreements" value={c.presence_disagreements.length} />
            <Metric label="Value disagreements" value={c.value_disagreements.length} />
            {c.unanimous_wrong != null && (
              <Metric
                label="Systematic (all wrong)"
                value={c.unanimous_wrong}
                tone={c.unanimous_wrong > 0 ? "warning" : undefined}
              />
            )}
            {c.unanimous_right != null && (
              <Metric label="Unanimously right" value={c.unanimous_right} />
            )}
          </div>

          {c.unanimous_wrong != null && (
            <p style={styles.note}>
              Against the attached benchmark, {c.unanimous_wrong} slots were
              wrong the same way every time (systematic — fixable by prompt) and
              the disagreeing slots are stochastic (needs a model/config change).
            </p>
          )}

          <DisagreementTable
            title="Value disagreements (all repeats filled it, differently)"
            testid="consistency-value-disagreements"
            rows={c.value_disagreements}
            kind="value"
          />
          <DisagreementTable
            title="Presence disagreements (some repeats left it blank)"
            testid="consistency-presence-disagreements"
            rows={c.presence_disagreements}
            kind="presence"
          />
        </>
      )}
    </section>
  );
}

function DisagreementTable({
  title,
  testid,
  rows,
  kind,
}: {
  title: string;
  testid: string;
  rows: ConsistencyDisagreement[];
  kind: "value" | "presence";
}) {
  // Value rows arrive sorted by spread from the server; presence rows are as
  // discovered. Memo keeps a stable order for the render.
  const sorted = useMemo(() => rows, [rows]);
  if (sorted.length === 0) return null;
  return (
    <div style={styles.section} data-testid={testid}>
      <div style={styles.sectionTitle}>{title}</div>
      <div style={styles.tableWrap}>
        <table style={styles.table}>
          <thead>
            <tr>
              <th style={styles.th}>Slot (concept · period · scope)</th>
              {kind === "value" ? (
                <>
                  <th style={styles.thNum}>Values</th>
                  <th style={styles.thNum}>Spread</th>
                </>
              ) : (
                <th style={styles.thNum}>Filled by</th>
              )}
            </tr>
          </thead>
          <tbody>
            {sorted.map((r, i) => (
              <tr key={i}>
                <td style={styles.td}>
                  {r.label ? (
                    // Human line-item name (resolved server-side); the raw
                    // concept key stays reachable via the tooltip.
                    <span title={r.key.join(" · ")}>
                      {[r.sheet, r.label, r.key[1], r.key[2]]
                        .filter(Boolean)
                        .join(" · ")}
                    </span>
                  ) : (
                    <code style={styles.code}>{r.key.join(" · ")}</code>
                  )}
                </td>
                {kind === "value" ? (
                  <>
                    <td style={styles.tdNum}>
                      {(r.values ?? []).map((v) => v.toLocaleString()).join(", ")}
                    </td>
                    <td style={styles.tdNum}>{(r.spread ?? 0).toLocaleString()}</td>
                  </>
                ) : (
                  <td style={styles.tdNum}>
                    {r.n_present}/{r.n_repeats} repeats
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Metric({
  label,
  value,
  tone,
}: {
  label: string;
  value: number;
  tone?: "warning";
}) {
  return (
    <div style={styles.metric}>
      <span
        style={{
          ...styles.metricValue,
          color: tone === "warning" ? pwc.orange500 : pwc.grey900,
        }}
      >
        {value}
      </span>
      <span style={styles.metricLabel}>{label}</span>
    </div>
  );
}

const styles = {
  wrap: {
    display: "flex",
    flexDirection: "column" as const,
    gap: pwc.space.md,
    marginTop: pwc.space.xl,
  } as React.CSSProperties,
  sectionHeading: {
    fontFamily: pwc.fontHeading,
    fontSize: 15,
    fontWeight: 600,
    color: pwc.grey900,
    margin: 0,
  } as React.CSSProperties,
  subtle: {
    fontFamily: pwc.fontBody,
    fontSize: 13,
    fontWeight: 400,
    color: pwc.grey500,
  } as React.CSSProperties,
  repeatStrip: {
    display: "flex",
    flexWrap: "wrap" as const,
    gap: pwc.space.sm,
  } as React.CSSProperties,
  repeatChip: {
    fontFamily: pwc.fontMono,
    fontSize: 12,
    color: pwc.grey700,
    border: `1px solid ${pwc.grey300}`,
    borderRadius: 2,
    padding: `2px ${pwc.space.sm}px`,
    background: pwc.white,
  } as React.CSSProperties,
  card: {
    ...ui.card,
    padding: pwc.space.xl,
    textAlign: "center" as const,
  } as React.CSSProperties,
  headline: {
    fontFamily: pwc.fontMono,
    fontSize: 30,
    fontWeight: pwc.weight.regular,
    color: pwc.grey900,
    lineHeight: 1,
  } as React.CSSProperties,
  fraction: {
    marginTop: pwc.space.sm,
    color: pwc.grey700,
    fontSize: 14,
  } as React.CSSProperties,
  section: {
    display: "flex",
    flexDirection: "column" as const,
    gap: pwc.space.sm,
  } as React.CSSProperties,
  sectionTitle: {
    fontFamily: pwc.fontHeading,
    fontSize: 13,
    fontWeight: 600,
    color: pwc.grey800,
  } as React.CSSProperties,
  detailGrid: {
    display: "grid",
    gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))",
    gap: pwc.space.md,
  } as React.CSSProperties,
  metric: {
    ...ui.card,
    padding: pwc.space.lg,
    display: "flex",
    flexDirection: "column" as const,
    gap: 2,
  } as React.CSSProperties,
  metricValue: {
    fontFamily: pwc.fontMono,
    fontSize: 22,
    fontWeight: pwc.weight.regular,
  } as React.CSSProperties,
  metricLabel: {
    fontFamily: pwc.fontHeading,
    fontSize: 12,
    fontWeight: 500,
    color: pwc.grey500,
  } as React.CSSProperties,
  tableWrap: {
    overflowX: "auto" as const,
    border: `1px solid ${pwc.grey200}`,
    borderRadius: pwc.radius.md,
  } as React.CSSProperties,
  table: {
    borderCollapse: "collapse" as const,
    width: "100%",
    fontSize: 13,
  } as React.CSSProperties,
  th: {
    textAlign: "left" as const,
    padding: "8px 12px",
    fontFamily: pwc.fontHeading,
    fontSize: 12,
    fontWeight: 600,
    color: pwc.grey700,
    background: pwc.grey100,
    borderBottom: `1px solid ${pwc.grey200}`,
  } as React.CSSProperties,
  thNum: {
    textAlign: "right" as const,
    padding: "8px 12px",
    fontFamily: pwc.fontHeading,
    fontSize: 12,
    fontWeight: 600,
    color: pwc.grey700,
    background: pwc.grey100,
    borderBottom: `1px solid ${pwc.grey200}`,
  } as React.CSSProperties,
  td: {
    padding: "8px 12px",
    borderBottom: `1px solid ${pwc.grey100}`,
    color: pwc.grey800,
  } as React.CSSProperties,
  tdNum: {
    padding: "8px 12px",
    borderBottom: `1px solid ${pwc.grey100}`,
    color: pwc.grey800,
    textAlign: "right" as const,
    fontFamily: pwc.fontMono,
  } as React.CSSProperties,
  code: {
    fontFamily: pwc.fontMono,
    fontSize: 12,
    color: pwc.grey700,
  } as React.CSSProperties,
  note: {
    margin: 0,
    color: pwc.grey500,
    fontSize: 12,
    lineHeight: 1.5,
  } as React.CSSProperties,
  muted: {
    color: pwc.grey700,
    fontSize: 14,
  } as React.CSSProperties,
} as const;
