import { pwc, tokens } from "../lib/theme";
import { denominationLabel } from "../lib/vocabulary";
import { ui, uiClass } from "../lib/uiStyles";
import { runStatusDisplay } from "../lib/runStatus";
import { StatusLabel } from "./StatusLabel";
import type { RunSummaryJson } from "../lib/types";

// ---------------------------------------------------------------------------
// RecentRunsList — the "pick up where you left off" work queue on the
// homepage (PLAN-homepage-redesign.md). Stateless: the parent owns fetching
// and passes the rows in.
//
// Drafts route back to /run/{id} (onResumeDraft) so the user can finish
// configuring and start; everything else opens the run's detail in History
// (onOpenRun). This mirrors HistoryList's draft-vs-non-draft split so the two
// surfaces behave identically.
//
// Design-system adoption (plan CS3): a divided list, not nested cards —
// filename, filing profile, monochrome symbol-plus-text status, concise
// date, and a visible action per row.
// ---------------------------------------------------------------------------

export interface RecentRunsListProps {
  runs: RunSummaryJson[];
  isLoading?: boolean;
  error?: string | null;
  onResumeDraft: (runId: number) => void;
  onOpenRun: (runId: number) => void;
  onViewAll: () => void;
}

function formatDate(iso: string): { concise: string; exact: string } {
  // Local time, matching HistoryList. Fall back to the raw string if the
  // backend ever sends something unparseable.
  const d = new Date(iso);
  if (isNaN(d.getTime())) return { concise: iso, exact: iso };
  return { concise: d.toLocaleDateString(), exact: d.toLocaleString() };
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

export function RecentRunsList({
  runs,
  isLoading,
  error,
  onResumeDraft,
  onOpenRun,
  onViewAll,
}: RecentRunsListProps) {
  return (
    <section style={styles.panel} aria-label="Recent runs">
      <div style={styles.header}>
        <span style={styles.heading}>Recent runs</span>
        {/* "View all" always available — even with zero runs it's a valid
            jump to the (empty) History page, and it disappears only while
            the first load is still in flight to avoid a flash. */}
        {!isLoading && (
          <button type="button" onClick={onViewAll} style={styles.viewAll}>
            View all →
          </button>
        )}
      </div>

      {isLoading ? (
        <p style={styles.placeholder}>Loading recent runs…</p>
      ) : error ? (
        // Non-blocking: a failed recents fetch shows a quiet message; the
        // upload card beside it stays fully usable.
        <p style={styles.placeholder}>Couldn't load recent runs.</p>
      ) : runs.length === 0 ? (
        <p style={styles.placeholder}>No runs yet — upload a PDF to get started.</p>
      ) : (
        <div style={styles.list}>
          {runs.map((run) => {
            const display = runStatusDisplay(run.status);
            const isDraft = run.status === "draft";
            const activate = () =>
              isDraft ? onResumeDraft(run.id) : onOpenRun(run.id);
            const filingProfile = [
              run.filing_standard?.toUpperCase(),
              run.filing_level ? run.filing_level.charAt(0).toUpperCase() + run.filing_level.slice(1) : null,
              run.denomination ? denominationLabel(run.denomination) : null,
            ].filter(Boolean).join(" · ");
            const date = formatDate(run.created_at);
            const actionLabel = isDraft
              ? "Continue setup"
              : run.status === "completed_with_errors" || run.status === "correction_exhausted"
              ? "Review"
              : "Open";
            return (
              <button
                type="button"
                key={run.id}
                onClick={activate}
                className={`recent-run-row ${uiClass.tableRow}`}
                style={styles.row}
              >
                <span style={styles.filename} title={run.pdf_filename}>
                  {run.pdf_filename}
                </span>
                <StatusLabel
                  state={statusState(run.status)}
                  symbol={display.symbol}
                  label={display.label}
                />
                {filingProfile && <span style={styles.profile}>{filingProfile}</span>}
                <span style={styles.date} title={date.exact}>
                  {date.concise}
                </span>
                <span style={styles.action}>{actionLabel}</span>
              </button>
            );
          })}
        </div>
      )}
    </section>
  );
}

const styles = {
  panel: {
    display: "flex",
    flexDirection: "column" as const,
    gap: pwc.space.sm,
  } as React.CSSProperties,
  header: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    paddingBottom: pwc.space.sm,
  } as React.CSSProperties,
  heading: {
    ...ui.subsectionTitle,
    fontSize: 15,
  } as React.CSSProperties,
  viewAll: {
    fontFamily: pwc.fontBody,
    fontSize: 13,
    color: tokens.color.action.primary,
    background: "none",
    border: "none",
    cursor: "pointer",
    padding: "4px 6px",
    minHeight: 24,
  } as React.CSSProperties,
  // Divided work queue: hairline rules between rows, no nested cards.
  list: {
    display: "flex",
    flexDirection: "column" as const,
    borderTop: `1px solid ${tokens.color.border.subtle}`,
  } as React.CSSProperties,
  row: {
    width: "100%",
    textAlign: "left" as const,
    fontFamily: pwc.fontBody,
    display: "flex",
    alignItems: "center",
    gap: pwc.space.lg,
    padding: `10px ${pwc.space.xs}px`,
    border: "none",
    borderBottom: `1px solid ${tokens.color.border.subtle}`,
    background: "transparent",
    cursor: "pointer",
    transition: `background ${pwc.motion.duration.fast} ${pwc.motion.easing}`,
  } as React.CSSProperties,
  filename: {
    fontFamily: pwc.fontBody,
    fontWeight: pwc.weight.medium,
    fontSize: 14,
    color: pwc.grey900,
    // Long statement filenames truncate rather than wrapping the row to
    // several lines; full name is on the title attribute.
    whiteSpace: "nowrap" as const,
    overflow: "hidden",
    textOverflow: "ellipsis",
    minWidth: 0,
    flex: "1 1 240px",
  } as React.CSSProperties,
  profile: {
    fontFamily: pwc.fontBody,
    fontSize: 12,
    color: tokens.color.text.secondary,
    whiteSpace: "nowrap" as const,
  } as React.CSSProperties,
  date: {
    fontFamily: pwc.fontBody,
    fontSize: 12,
    color: tokens.color.text.secondary,
    whiteSpace: "nowrap" as const,
  } as React.CSSProperties,
  action: {
    fontFamily: pwc.fontBody,
    fontSize: 13,
    fontWeight: pwc.weight.medium,
    color: tokens.color.action.primary,
    flexShrink: 0,
  } as React.CSSProperties,
  placeholder: {
    margin: 0,
    fontFamily: pwc.fontBody,
    fontSize: 13,
    color: tokens.color.text.secondary,
  } as React.CSSProperties,
} as const;
