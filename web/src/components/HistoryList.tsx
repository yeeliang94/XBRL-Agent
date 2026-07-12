import { pwc, tokens } from "../lib/theme";
import { ui, uiClass } from "../lib/uiStyles";
import { runStatusDisplay } from "../lib/runStatus";
import { denominationLabel } from "../lib/vocabulary";
import type { RunSummaryJson } from "../lib/types";
import { Skeleton } from "./Skeleton";
import { StatusLabel } from "./StatusLabel";

// ---------------------------------------------------------------------------
// HistoryList — table of past runs. Stateless: parent owns the list and
// selection; this component only renders and forwards clicks.
//
// The "loading" and "error" states are rendered in-place (same container)
// so the page layout stays stable when the user types into the filters.
//
// Design-system adoption (plan CS4): Standard table density (40px rows),
// sentence-case headers, monochrome symbol-plus-text status, plain-text
// filing metadata, a visible per-row action, and a Score column that only
// renders when the loaded rows actually carry scores.
// ---------------------------------------------------------------------------

export interface HistoryListProps {
  runs: RunSummaryJson[];
  isLoading?: boolean;
  error?: string | null;
  selectedId?: number | null;
  onRunSelected: (runId: number) => void;
  /** PLAN-persistent-draft-uploads.md (Phase D): clicking a draft row
   *  should route the user back to `/run/{id}` so they can edit config
   *  and click Start, NOT open the inline RunDetailPage (which has
   *  nothing to render for an unstarted run). When omitted, drafts fall
   *  back to `onRunSelected` so legacy callers keep working. */
  onResumeDraft?: (runId: number) => void;
}

function formatDate(iso: string): { concise: string; exact: string } {
  // Keep it timezone-local so the user sees "their" time, not UTC.
  // If the date is unparseable we fall back to the raw string.
  const d = new Date(iso);
  if (isNaN(d.getTime())) return { concise: iso, exact: iso };
  return { concise: d.toLocaleDateString(), exact: d.toLocaleString() };
}

function formatDuration(seconds: number | null): string {
  if (seconds == null) return "—";
  if (seconds < 60) return `${seconds}s`;
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}m ${s}s`;
}

function statusState(status: string): "inProgress" | "success" | "attention" | "failure" | "inactive" {
  switch (status) {
    case "running":
      return "inProgress";
    case "completed":
      return "success";
    case "completed_with_errors":
    case "correction_exhausted":
      return "attention";
    case "failed":
    case "aborted":
      return "failure";
    default:
      return "inactive";
  }
}

export function HistoryList({
  runs,
  isLoading,
  error,
  selectedId,
  onRunSelected,
  onResumeDraft,
}: HistoryListProps) {
  if (isLoading) {
    // Skeleton rows in the shape of the list (Phase 7) instead of a bare
    // "Loading…" line, so the layout doesn't jump when the runs arrive.
    return (
      <div style={styles.container} role="status" aria-label="Loading recent runs…">
        {Array.from({ length: 6 }).map((_, i) => (
          <div key={i} style={styles.skeletonRow}>
            <Skeleton width="40%" />
            <Skeleton width="14%" />
            <Skeleton width="12%" />
          </div>
        ))}
      </div>
    );
  }
  if (error) {
    return (
      <div style={styles.container}>
        <div style={styles.errorBanner}>{error}</div>
      </div>
    );
  }
  if (runs.length === 0) {
    return (
      <div style={styles.container}>
        <p style={styles.placeholder}>No runs match the current filters.</p>
      </div>
    );
  }

  // Score column (gold-standard eval, v16) only renders when the loaded
  // rows contain at least one graded run — an all-blank column is noise.
  const hasScores = runs.some((r) => r.eval_score != null);

  return (
    <div className="runs-table-wrap" style={styles.container}>
      {/* Compact sparkline of eval scores across the listed runs (oldest →
          newest) so improvement is visible at a glance. Only shown when ≥2
          runs were graded. */}
      <EvalSparkline runs={runs} />
      <table style={styles.table}>
        {/* Fixed column widths — without these the browser picks column
            widths from content, so a long filename could squash the
            timestamp/status columns. `table-layout: fixed` plus <col>
            widths makes the layout predictable regardless of content. */}
        <colgroup>
          <col style={{ width: hasScores ? "28%" : "32%" }} />
          <col style={{ width: "12%" }} />
          <col style={{ width: "16%" }} />
          <col style={{ width: "15%" }} />
          {hasScores && <col style={{ width: "8%" }} />}
          <col style={{ width: "10%" }} />
          <col style={{ width: hasScores ? "11%" : "15%" }} />
        </colgroup>
        <thead>
          <tr>
            <th style={styles.th}>Filename</th>
            <th style={styles.th}>When</th>
            <th style={styles.th}>Status</th>
            {/* The filter has a Standard control but the list had no matching
                column; this carries the run's standard + level (E2). */}
            <th style={styles.th}>Standard</th>
            {hasScores && (
              <th
                style={{ ...styles.th, textAlign: "right" }}
                title="Accuracy vs a benchmark's verified answers — blank unless a benchmark was attached"
              >
                Score
              </th>
            )}
            <th style={{ ...styles.th, textAlign: "right" }}>Duration</th>
            <th style={styles.th}>
              <span style={visuallyHidden}>Action</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {runs.map((run) => {
            const display = runStatusDisplay(run.status);
            const isSelected = selectedId === run.id;
            // Drafts route to /run/{id} via onResumeDraft so the user can
            // resume editing. Non-drafts open the inline detail panel via
            // onRunSelected (existing behaviour).
            const isDraft = run.status === "draft";
            const handleActivate = () => {
              if (isDraft && onResumeDraft) onResumeDraft(run.id);
              else onRunSelected(run.id);
            };
            // A real link target so the row supports middle-click /
            // open-in-new-tab / copy-link (a bare role="button" row can't) —
            // docs/PLAN-design-qa-fixes.md R4. Drafts resume at /run/{id};
            // finished runs open their detail at /history/{id}.
            const runHref = isDraft ? `/run/${run.id}` : `/history/${run.id}`;
            const date = formatDate(run.created_at);
            const actionLabel = isDraft
              ? "Continue setup"
              : run.status === "completed_with_errors" || run.status === "correction_exhausted"
              ? "Review"
              : "Open";
            // The filename is the semantic navigation link. The row click is
            // only a pointer convenience; keeping native row semantics avoids
            // corrupting the table header/column structure for screen readers.
            return (
              <tr
                key={run.id}
                onClick={handleActivate}
                className={uiClass.tableRow}
                style={isSelected ? styles.rowSelected : styles.row}
              >
                <td style={styles.tdFilename}>
                  {/* One flex line: the filename truncates, the denomination
                      tag stays beside it instead of wrapping onto its own
                      orphaned line under a long name (live-QA follow-up). */}
                  <div style={styles.filenameRow}>
                    <a
                      href={runHref}
                      style={styles.filename}
                      title={run.pdf_filename}
                      aria-current={isSelected ? "page" : undefined}
                      onClick={(e) => {
                        // Plain click → SPA activate (no full page load). Let
                        // modified clicks (new tab / window) fall through to the
                        // browser's native href handling.
                        e.stopPropagation();
                        if (e.metaKey || e.ctrlKey || e.shiftKey) return;
                        e.preventDefault();
                        handleActivate();
                      }}
                    >
                      {run.pdf_filename}
                    </a>
                    {/* Ordinary filing metadata is plain text, not a pill.
                        Non-default denomination only — "thousands" (RM '000)
                        is the common case and implied. */}
                    {run.denomination && run.denomination !== "thousands" && (
                      <span style={styles.inlineMeta}>
                        {denominationLabel(run.denomination)}
                      </span>
                    )}
                  </div>
                </td>
                <td style={styles.td}>
                  <span style={styles.dim} title={date.exact}>
                    {date.concise}
                  </span>
                </td>
                <td style={styles.td}>
                  <StatusLabel
                    state={statusState(run.status)}
                    symbol={display.symbol}
                    label={display.label}
                  />
                </td>
                <td style={styles.td}>
                  <span style={styles.dim}>
                    {(run.filing_standard ?? "mfrs").toUpperCase()}
                    {" · "}
                    {run.filing_level === "group" ? "Group" : "Company"}
                  </span>
                </td>
                {hasScores && (
                  <td style={{ ...styles.td, textAlign: "right" }}>
                    {run.eval_score != null ? (
                      <span
                        data-testid={`history-score-${run.id}`}
                        style={styles.scoreValue}
                        title={`Graded against benchmark ${run.benchmark_id}`}
                      >
                        {Math.round(run.eval_score * 100)}%
                      </span>
                    ) : (
                      <span
                        style={styles.dim}
                        title="Only scored when the run was graded against a benchmark"
                      >—</span>
                    )}
                  </td>
                )}
                <td style={{ ...styles.td, textAlign: "right" }}>
                  <span style={styles.dim}>{formatDuration(run.duration_seconds)}</span>
                </td>
                <td style={styles.td}>
                  {/* Visible action; delegates to the same activate path the
                      row uses — stopPropagation so it never fires twice. */}
                  <a
                    href={runHref}
                    style={styles.actionLink}
                    onClick={(e) => {
                      e.stopPropagation();
                      if (e.metaKey || e.ctrlKey || e.shiftKey) return;
                      e.preventDefault();
                      handleActivate();
                    }}
                  >
                    {actionLabel}
                  </a>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// ---------------------------------------------------------------------------
// EvalSparkline — a tiny inline-SVG trend of eval scores across the listed
// runs (gold-standard eval, v16). `runs` arrives newest-first; we reverse to
// chronological so the line reads left→right = oldest→newest. Only rendered
// when at least two runs were graded. (Charts keep functional colour — the
// monochrome rule applies to routine status, not chart marks.)
// ---------------------------------------------------------------------------

function EvalSparkline({ runs }: { runs: RunSummaryJson[] }) {
  const scored = runs
    .filter((r) => r.eval_score != null)
    .slice()
    .reverse() as Array<RunSummaryJson & { eval_score: number }>;
  if (scored.length < 2) return null;

  const W = 160;
  const H = 32;
  const PAD = 3;
  const n = scored.length;
  // Scores are already in [0, 1]; map directly to the vertical axis (1 = top).
  const points = scored.map((r, i) => {
    const x = PAD + (i * (W - 2 * PAD)) / (n - 1);
    const y = PAD + (1 - r.eval_score) * (H - 2 * PAD);
    return { x, y, score: r.eval_score };
  });
  const path = points.map((p, i) => `${i === 0 ? "M" : "L"}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(" ");
  const last = points[points.length - 1];

  return (
    <div data-testid="history-eval-sparkline" style={styles.sparklineWrap}>
      <span style={styles.sparklineLabel}>Eval trend</span>
      <svg width={W} height={H} role="img" aria-label="Eval score trend">
        <path d={path} fill="none" stroke={pwc.orange500} strokeWidth={1.5} />
        <circle cx={last.x} cy={last.y} r={2.5} fill={pwc.orange500} />
      </svg>
      <span style={styles.sparklineValue}>{Math.round(last.score * 100)}%</span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Styles
// ---------------------------------------------------------------------------

const visuallyHidden: React.CSSProperties = {
  position: "absolute",
  width: 1,
  height: 1,
  padding: 0,
  margin: -1,
  overflow: "hidden",
  clip: "rect(0 0 0 0)",
  whiteSpace: "nowrap",
  border: 0,
};

const rowBase: React.CSSProperties = {
  cursor: "pointer",
  // Calm fade-in as the list renders. Rows are keyed by run.id, so selecting a
  // row (row → rowSelected) keeps the same node and the same animation string,
  // meaning it never re-plays; only genuinely new rows (pagination) animate.
  animation: `fade-in ${pwc.motion.duration.base} ${pwc.motion.easing}`,
};

const styles = {
  container: {
    ...ui.card,
    overflow: "hidden",
  } as React.CSSProperties,
  table: {
    width: "100%",
    borderCollapse: "collapse" as const,
    // Fixed layout honors <colgroup> widths and enables ellipsis truncation
    // on long content (filenames, model IDs). Without this the browser
    // auto-sizes columns from content and the truncation never kicks in.
    tableLayout: "fixed" as const,
    fontSize: 14,
    fontFamily: pwc.fontBody,
  } as React.CSSProperties,
  // Standard density (≈40px rows) via the shared table roles.
  th: {
    ...ui.th,
  } as React.CSSProperties,
  td: {
    ...ui.td,
    borderBottom: `1px solid ${pwc.grey100}`,
    verticalAlign: "middle" as const,
    overflow: "hidden",
  } as React.CSSProperties,
  // Gold-standard eval score — mono for numeric alignment; readable ink.
  scoreValue: {
    fontFamily: pwc.fontMono,
    fontSize: 13,
    fontWeight: pwc.weight.medium,
    color: pwc.grey900,
  } as React.CSSProperties,
  sparklineWrap: {
    display: "flex",
    alignItems: "center",
    gap: pwc.space.sm,
    padding: `${pwc.space.md}px ${pwc.space.lg}px`,
    borderBottom: `1px solid ${pwc.grey100}`,
    background: pwc.grey50,
  } as React.CSSProperties,
  sparklineLabel: {
    ...ui.microLabel,
    textTransform: "uppercase" as const,
  } as React.CSSProperties,
  sparklineValue: {
    fontFamily: pwc.fontMono,
    fontSize: 13,
    fontWeight: pwc.weight.medium,
    color: pwc.grey900,
  } as React.CSSProperties,
  // Filename cell gets a left "selection rail" via border-left on the
  // row-selected variant below. Keeping padding identical to other cells
  // so the rail doesn't shift content when a row becomes active.
  tdFilename: {
    ...ui.td,
    borderBottom: `1px solid ${pwc.grey100}`,
    verticalAlign: "middle" as const,
    overflow: "hidden",
  } as React.CSSProperties,
  row: { ...rowBase } as React.CSSProperties,
  // Selection: a filled tint plus a thick orange left rail so the user can
  // see at a glance which row spawned the currently-open detail. (Selection
  // is an active-state indicator — allowed orange, unlike routine status.)
  rowSelected: {
    ...rowBase,
    background: pwc.orange100,
    boxShadow: `inset 3px 0 0 0 ${pwc.orange500}`,
  } as React.CSSProperties,
  // Filename + optional denomination tag share one truncating flex line.
  filenameRow: {
    display: "flex",
    alignItems: "baseline",
    gap: pwc.space.sm,
    minWidth: 0,
  } as React.CSSProperties,
  filename: {
    fontFamily: pwc.fontBody,
    fontWeight: pwc.weight.medium,
    color: pwc.grey900,
    // Rendered as an <a> for middle-click / open-in-new-tab support, but it
    // should read as a filename, not a blue underlined link.
    textDecoration: "none",
    // Single-line truncation: long filenames like
    // "Audited Financial Statements for the FYE 31 December 2022.pdf"
    // used to wrap onto 5+ lines in the old narrow pane. Title attribute
    // exposes the full name on hover. flex 0-1-auto lets the name shrink
    // while the denomination tag keeps its width.
    flex: "0 1 auto",
    minWidth: 0,
    whiteSpace: "nowrap" as const,
    overflow: "hidden",
    textOverflow: "ellipsis",
  } as React.CSSProperties,
  dim: {
    color: pwc.grey700,
    fontSize: 13,
    whiteSpace: "nowrap" as const,
  } as React.CSSProperties,
  inlineMeta: {
    fontFamily: pwc.fontBody,
    fontSize: 12,
    color: pwc.grey700,
    flexShrink: 0,
    whiteSpace: "nowrap" as const,
  } as React.CSSProperties,
  actionLink: {
    fontFamily: pwc.fontBody,
    fontSize: 13,
    fontWeight: pwc.weight.medium,
    color: tokens.color.action.primary,
    textDecoration: "none",
    whiteSpace: "nowrap" as const,
    padding: "4px 0",
    display: "inline-block",
  } as React.CSSProperties,
  placeholder: {
    padding: pwc.space.xl,
    textAlign: "center" as const,
    color: pwc.grey700,
    fontFamily: pwc.fontBody,
    fontSize: 14,
    margin: 0,
  } as React.CSSProperties,
  skeletonRow: {
    display: "flex",
    gap: pwc.space.xl,
    alignItems: "center",
    padding: `${pwc.space.lg}px ${pwc.space.xl}px`,
    borderBottom: `1px solid ${pwc.grey100}`,
  } as React.CSSProperties,
  errorBanner: {
    padding: pwc.space.lg,
    background: pwc.white,
    color: pwc.grey800,
    fontFamily: pwc.fontBody,
    fontSize: 14,
    borderBottom: `1px solid ${pwc.grey200}`,
    borderLeft: `3px solid ${pwc.error}`,
  } as React.CSSProperties,
} as const;
