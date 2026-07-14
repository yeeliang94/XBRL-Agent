import { useEffect, useState } from "react";
import { pwc } from "../lib/theme";
import { ui, uiClass } from "../lib/uiStyles";
import { fetchRunEval, fetchReviewerLift, reGradeRun } from "../lib/api";
import type { EvalScoreJson, EvalTaxonomy, ReviewerLiftJson } from "../lib/types";

// Plain-language labels for each diagnosed failure mode (docs/PLAN-evals-workspace.md).
// Ordered most-actionable first, matching the grader's priority.
const TAXONOMY_LABELS: { key: keyof EvalTaxonomy; label: string; hint: string }[] = [
  { key: "period_swap", label: "Year swapped", hint: "current & prior-year values transposed" },
  { key: "scope_swap", label: "Column swapped", hint: "group & company values transposed" },
  { key: "sign_flip", label: "Sign flipped", hint: "right number, wrong +/− direction" },
  { key: "scale", label: "Scale error", hint: "off by a factor of ~1,000 (thousands misread)" },
  { key: "misplaced", label: "Wrong row", hint: "right number landed on a different line" },
  { key: "false_not_disclosed", label: "Wrongly skipped", hint: "we said 'not in the document' but it was" },
  { key: "unaddressed", label: "Not reached", hint: "we never dealt with this line" },
  { key: "plain_wrong", label: "Other wrong", hint: "a wrong value with no clearer pattern" },
];

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
  const [lift, setLift] = useState<ReviewerLiftJson | null>(null);
  const [reGrading, setReGrading] = useState(false);

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

  // Reviewer contribution (Step 12) — computed since E5, now reachable.
  useEffect(() => {
    let cancelled = false;
    setLift(null); // never show the previous run's lift under a new run
    fetchReviewerLift(runId)
      .then((l) => {
        if (!cancelled) setLift(l);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [runId]);

  const handleReGrade = () => {
    setReGrading(true);
    reGradeRun(runId)
      .then((r) => setScore({ ...r.score, gold_stale: false }))
      .catch(() => {})
      .finally(() => setReGrading(false));
  };

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
      {score.gold_stale === true && (
        <div data-testid="eval-gold-stale" style={styles.staleBanner}>
          <span>
            The reference answers for this benchmark were edited after this
            score was recorded — the score below may no longer reflect them.
          </span>
          <button
            data-testid="eval-re-grade"
            className={uiClass.btnSecondary}
            style={ui.buttonSecondary}
            disabled={reGrading}
            onClick={handleReGrade}
          >
            {reGrading ? "Re-grading…" : "Re-grade with current answers"}
          </button>
        </div>
      )}
      <div style={styles.card}>
        <div data-testid="eval-headline" style={styles.headline}>
          {pct(score.score)}
        </div>
        <div style={styles.fraction}>
          {score.matched_cells} / {score.gold_cells} gold cells matched
        </div>
        {lift?.available && (
          <div data-testid="eval-reviewer-lift" style={styles.lift}>
            Reviewer pass: {lift.lift_slots! >= 0 ? "+" : ""}
            {lift.lift_slots} cell{Math.abs(lift.lift_slots!) === 1 ? "" : "s"}
            {" "}({lift.pre_accuracy != null ? pct(lift.pre_accuracy) : "—"} before
            {" → "}{lift.final_accuracy != null ? pct(lift.final_accuracy) : "—"} after)
          </div>
        )}
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
      {renderTaxonomy(score.taxonomy)}
      {renderPerStatement(score.per_statement)}
      <p style={styles.note}>
        Score = matched / gold cells. Extras (the run filled a cell the gold
        left blank) are surfaced as a warning and are NOT in the denominator.
      </p>
    </div>
  );
}

// The diagnosis breakdown — only shown when the scorecard carries a taxonomy
// (a run graded after v30) AND at least one failure was diagnosed. A perfect
// run or a legacy scorecard renders nothing here.
function renderTaxonomy(taxonomy: EvalTaxonomy | null | undefined) {
  if (!taxonomy) return null;
  const rows = TAXONOMY_LABELS.filter((t) => (taxonomy[t.key] ?? 0) > 0);
  if (rows.length === 0) return null;
  return (
    <div data-testid="eval-taxonomy" style={styles.section}>
      <div style={styles.sectionTitle}>What went wrong</div>
      <div style={styles.detailGrid}>
        {rows.map((t) => (
          <div key={t.key} style={styles.metric} title={t.hint}>
            <span style={styles.metricValue}>{taxonomy[t.key]}</span>
            <span style={styles.metricLabel}>{t.label}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

// Per-statement accuracy — so a change can be traced to one statement.
function renderPerStatement(
  perStatement: Record<string, { gold_cells: number; matched: number }> | null | undefined,
) {
  if (!perStatement) return null;
  const entries = Object.entries(perStatement).sort(([a], [b]) => a.localeCompare(b));
  if (entries.length === 0) return null;
  return (
    <div data-testid="eval-per-statement" style={styles.section}>
      <div style={styles.sectionTitle}>Accuracy by statement</div>
      <div style={styles.detailGrid}>
        {entries.map(([stmt, b]) => (
          <div key={stmt} style={styles.metric}>
            <span style={styles.metricValue}>
              {b.gold_cells > 0 ? `${Math.round((b.matched / b.gold_cells) * 100)}%` : "—"}
            </span>
            <span style={styles.metricLabel}>
              {stmt} ({b.matched}/{b.gold_cells})
            </span>
          </div>
        ))}
      </div>
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
    // Brought down from an off-scale 56px to the display size the rest of the
    // app uses for hero numbers (layout normalization, Phase 8).
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
  lift: {
    marginTop: pwc.space.sm,
    color: pwc.grey500,
    fontSize: 13,
  } as React.CSSProperties,
  staleBanner: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    gap: pwc.space.lg,
    border: `1px solid ${pwc.orange700}`,
    background: pwc.orange50,
    color: pwc.grey900,
    fontSize: 13,
    padding: pwc.space.lg,
  } as React.CSSProperties,
  flags: {
    fontFamily: pwc.fontBody,
    fontSize: 14,
    color: pwc.grey800,
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
