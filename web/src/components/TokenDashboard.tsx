import type { TokenData } from "../lib/types";
import { pwc } from "../lib/theme";

interface Props {
  tokens: TokenData | null;
  isRunning: boolean;
}

function fmt(n: number): string {
  return n.toLocaleString();
}

const styles = {
  container: {
    background: pwc.white,
    borderRadius: pwc.radius.md,
    border: `1px solid ${pwc.grey200}`,
    boxShadow: pwc.shadow.card,
    padding: pwc.space.lg,
  } as React.CSSProperties,
  waiting: {
    background: pwc.white,
    borderRadius: pwc.radius.md,
    border: `1px solid ${pwc.grey200}`,
    padding: pwc.space.lg,
    textAlign: "center" as const,
    fontFamily: pwc.fontBody,
    fontSize: 14,
    color: pwc.grey500,
  } as React.CSSProperties,
  row: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    gap: pwc.space.lg,
    flexWrap: "wrap" as const,
  } as React.CSSProperties,
  metrics: {
    display: "flex",
    gap: pwc.space.xl,
  } as React.CSSProperties,
  metric: {
    textAlign: "center" as const,
  } as React.CSSProperties,
  metricLabel: {
    fontFamily: pwc.fontHeading,
    fontSize: 11,
    color: pwc.grey500,
    textTransform: "uppercase" as const,
    letterSpacing: "0.05em",
  } as React.CSSProperties,
  metricValue: {
    fontFamily: pwc.fontMono,
    fontSize: 18,
    fontWeight: pwc.weight.regular,
    color: pwc.grey900,
  } as React.CSSProperties,
  costSection: {
    textAlign: "right" as const,
    display: "flex",
    flexDirection: "column" as const,
    alignItems: "flex-end",
  } as React.CSSProperties,
  costLabel: {
    fontFamily: pwc.fontHeading,
    fontSize: 11,
    color: pwc.grey500,
    textTransform: "uppercase" as const,
    letterSpacing: "0.05em",
    display: "flex",
    alignItems: "center",
    gap: pwc.space.xs,
  } as React.CSSProperties,
  costValue: {
    fontFamily: pwc.fontMono,
    fontSize: 18,
    fontWeight: pwc.weight.regular,
    color: pwc.success,
  } as React.CSSProperties,
  // Honesty caption (UX-QA #23): this live meter sums only the extraction
  // agents. The pre-scan (scout) runs on a separate stream and the AI-review
  // pass emits no token events, so both land on the final total but not here —
  // say so, or a user watching the meter under-reads the eventual bill by ~40%.
  costCaption: {
    fontFamily: pwc.fontBody,
    fontSize: 11,
    color: pwc.grey500,
    marginTop: 2,
    maxWidth: 220,
    textAlign: "right" as const,
    lineHeight: 1.35,
  } as React.CSSProperties,
  pulsingDot: {
    width: 8,
    height: 8,
    borderRadius: "50%",
    background: pwc.orange400,
    animation: "pulse-subtle 1.5s ease-in-out infinite",
    display: "inline-block",
  } as React.CSSProperties,
};

export function TokenDashboard({ tokens, isRunning }: Props) {
  if (!tokens) {
    return <div style={styles.waiting}>Waiting for token data...</div>;
  }

  const metrics = [
    { label: "Prompt", value: fmt(tokens.prompt_tokens) },
    { label: "Completion", value: fmt(tokens.completion_tokens) },
    // Only show thinking tokens when the model actually used them
    ...(tokens.thinking_tokens > 0
      ? [{ label: "Thinking", value: fmt(tokens.thinking_tokens) }]
      : []),
    { label: "Cumulative", value: fmt(tokens.cumulative) },
  ];

  return (
    <div style={styles.container}>
      <div style={styles.row}>
        <div style={styles.metrics}>
          {metrics.map((m) => (
            <div key={m.label} style={styles.metric}>
              <div style={styles.metricLabel}>{m.label}</div>
              <div style={styles.metricValue}>{m.value}</div>
            </div>
          ))}
        </div>
        <div style={styles.costSection}>
          <div style={styles.costLabel}>
            Est. Cost
            {isRunning && <span style={styles.pulsingDot} />}
          </div>
          <div style={styles.costValue}>${tokens.cost_estimate.toFixed(4)}</div>
          <div style={styles.costCaption}>
            Extraction only — the pre-scan and AI review add to the final total
            on the run report.
          </div>
        </div>
      </div>
    </div>
  );
}
