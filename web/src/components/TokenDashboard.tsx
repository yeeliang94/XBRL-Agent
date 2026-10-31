import type { TokenData } from "../lib/types";
import { ds } from "../lib/theme";
import { AnimatedNumber } from "./AnimatedNumber";

interface Props {
  tokens: TokenData | null;
  isRunning: boolean;
  embedded?: boolean;
}

const styles = {
  container: {
    background: "transparent",
    borderRadius: 0,
    border: "none",
    boxShadow: "none",
    padding: `${ds.space.lg}px 0`,
  } as React.CSSProperties,
  waiting: {
    background: ds.grey50,
    borderRadius: 0,
    border: "none",
    padding: ds.space.lg,
    textAlign: "center" as const,
    fontFamily: ds.fontBody,
    fontSize: 14,
    color: ds.grey500,
  } as React.CSSProperties,
  row: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    gap: ds.space.lg,
    flexWrap: "wrap" as const,
  } as React.CSSProperties,
  metrics: {
    display: "flex",
    gap: ds.space.xl,
    flexWrap: "wrap" as const,
  } as React.CSSProperties,
  metric: {
    textAlign: "center" as const,
  } as React.CSSProperties,
  metricLabel: {
    fontFamily: ds.fontHeading,
    fontSize: 12,
    color: ds.grey500,
  } as React.CSSProperties,
  metricValue: {
    fontFamily: ds.fontMono,
    fontSize: 16,
    fontWeight: ds.weight.regular,
    color: ds.grey900,
  } as React.CSSProperties,
  costSection: {
    textAlign: "right" as const,
    display: "flex",
    flexDirection: "column" as const,
    alignItems: "flex-end",
  } as React.CSSProperties,
  costLabel: {
    fontFamily: ds.fontHeading,
    fontSize: 12,
    color: ds.grey500,
    display: "flex",
    alignItems: "center",
    gap: ds.space.xs,
  } as React.CSSProperties,
  costValue: {
    fontFamily: ds.fontMono,
    fontSize: 16,
    fontWeight: ds.weight.regular,
    color: ds.success,
  } as React.CSSProperties,
  // Usage arrives at model-response boundaries rather than per streamed word.
  costCaption: {
    fontFamily: ds.fontBody,
    fontSize: 12,
    color: ds.grey500,
    marginTop: 2,
    maxWidth: 220,
    textAlign: "right" as const,
    lineHeight: 1.35,
  } as React.CSSProperties,
  pulsingDot: {
    width: 8,
    height: 8,
    borderRadius: "50%",
    background: ds.orange400,
    display: "inline-block",
  } as React.CSSProperties,
};

export function TokenDashboard({ tokens, isRunning, embedded = false }: Props) {
  if (!tokens) {
    return (
      <div style={embedded ? { ...styles.waiting, border: "none", padding: ds.space.md } : styles.waiting}>
        Waiting for token data...
      </div>
    );
  }

  // Raw numeric values so the live counters COUNT UP as SSE token events land,
  // rather than snapping. AnimatedNumber shows the value instantly on first
  // paint, so opening a finished run never rolls up from zero.
  const metrics = [
    { label: "Prompt", value: tokens.prompt_tokens },
    { label: "Completion", value: tokens.completion_tokens },
    // Only show thinking tokens when the model actually used them
    ...(tokens.thinking_tokens > 0
      ? [{ label: "Thinking", value: tokens.thinking_tokens }]
      : []),
    { label: "Cumulative", value: tokens.cumulative },
  ];

  return (
    <div style={embedded ? { ...styles.container, border: "none", boxShadow: "none", padding: ds.space.lg } : styles.container}>
      <div style={styles.row}>
        <div style={styles.metrics}>
          {metrics.map((m) => (
            <div key={m.label} style={styles.metric}>
              <div style={styles.metricLabel}>{m.label}</div>
              <AnimatedNumber value={m.value} style={styles.metricValue} />
            </div>
          ))}
        </div>
        <div style={styles.costSection}>
          <div style={styles.costLabel}>
            Est. Cost
            {isRunning && <span style={styles.pulsingDot} />}
          </div>
          <AnimatedNumber
            value={tokens.cost_estimate}
            integer={false}
            format={(n) => `$${n.toFixed(4)}`}
            style={styles.costValue}
          />
          <div style={styles.costCaption}>
            Updates after each model response, including document scan and AI review.
          </div>
        </div>
      </div>
    </div>
  );
}
