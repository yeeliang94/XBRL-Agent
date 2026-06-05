import { useEffect, useState } from "react";
import { pwc } from "../lib/theme";
import { ui } from "../lib/uiStyles";
import { fetchRunEval } from "../lib/api";
import type { EvalScoreJson } from "../lib/types";

// ---------------------------------------------------------------------------
// EvalTab — gold-standard eval scorecard (v16). Lazy-mounted only when the
// Eval tab is active, so it fetches the run's scorecard on demand if the
// run-detail payload didn't already carry it. Inline styles + pwc tokens.
// ---------------------------------------------------------------------------

export interface EvalTabProps {
  runId: number;
  // The scorecard already embedded in the run-detail payload, if any. When
  // null we fetch /api/runs/{id}/eval (e.g. a run graded after the detail was
  // first loaded).
  initialScore?: EvalScoreJson | null;
}

function pct(score: number): string {
  return `${Math.round(score * 100)}%`;
}

export function EvalTab({ runId, initialScore = null }: EvalTabProps) {
  const [score, setScore] = useState<EvalScoreJson | null>(initialScore);
  const [loaded, setLoaded] = useState<boolean>(initialScore != null);

  useEffect(() => {
    if (initialScore != null) return;
    let cancelled = false;
    fetchRunEval(runId)
      .then((s) => {
        if (!cancelled) {
          setScore(s);
          setLoaded(true);
        }
      })
      .catch(() => {
        if (!cancelled) setLoaded(true);
      });
    return () => {
      cancelled = true;
    };
  }, [runId, initialScore]);

  if (!loaded) {
    return <p style={styles.muted}>Loading score…</p>;
  }
  if (score == null) {
    return (
      <p data-testid="eval-no-score" style={styles.muted}>
        This run hasn't been graded yet.
      </p>
    );
  }

  // Flag line — only the non-zero signals, so a clean run reads "0 issues".
  const flags: string[] = [];
  if (score.scale_mismatch > 0) flags.push(`${score.scale_mismatch} scale mismatch${score.scale_mismatch === 1 ? "" : "es"}`);
  if (score.missing_cells > 0) flags.push(`${score.missing_cells} missing`);
  if (score.mismatch_cells > 0) flags.push(`${score.mismatch_cells} mismatch${score.mismatch_cells === 1 ? "" : "es"}`);
  if (score.extra_cells > 0) flags.push(`${score.extra_cells} extra`);

  return (
    <div data-testid="eval-scorecard" style={styles.wrap}>
      <div style={styles.card}>
        <div data-testid="eval-headline" style={styles.headline}>
          {pct(score.score)}
        </div>
        <div style={styles.fraction}>
          {score.matched_cells} / {score.gold_cells} gold cells matched
        </div>
      </div>
      <div data-testid="eval-flags" style={styles.flags}>
        {flags.length === 0 ? "No issues — every gold cell matched." : flags.join(" · ")}
      </div>
      <div style={styles.detailGrid}>
        <Metric label="Matched" value={score.matched_cells} />
        <Metric label="Missing" value={score.missing_cells} />
        <Metric label="Mismatched" value={score.mismatch_cells} />
        <Metric label="Scale mismatches" value={score.scale_mismatch} />
        <Metric label="Extras (warning)" value={score.extra_cells} tone="warning" />
        <Metric label="Gold cells" value={score.gold_cells} />
      </div>
      <p style={styles.note}>
        Score = matched / gold cells. Extras (the run filled a cell the gold
        left blank) are surfaced as a warning and are NOT in the denominator.
      </p>
    </div>
  );
}

function Metric({
  label,
  value,
  tone = "neutral",
}: {
  label: string;
  value: number;
  tone?: "neutral" | "warning";
}) {
  return (
    <div style={styles.metric}>
      <span
        style={{
          ...styles.metricValue,
          color: tone === "warning" && value > 0 ? pwc.orange700 : pwc.grey900,
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
    gap: pwc.space.lg,
  } as React.CSSProperties,
  card: {
    ...ui.card,
    padding: pwc.space.xxl,
    textAlign: "center" as const,
  } as React.CSSProperties,
  headline: {
    fontFamily: pwc.fontMono,
    fontSize: 56,
    fontWeight: pwc.weight.light,
    color: pwc.grey900,
    lineHeight: 1,
  } as React.CSSProperties,
  fraction: {
    marginTop: pwc.space.sm,
    color: pwc.grey700,
    fontSize: 14,
  } as React.CSSProperties,
  flags: {
    fontFamily: pwc.fontBody,
    fontSize: 14,
    color: pwc.grey800,
  } as React.CSSProperties,
  detailGrid: {
    display: "grid",
    gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))",
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
