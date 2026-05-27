import { pwc } from "../lib/theme";
import { ui } from "../lib/uiStyles";
import { runStatusDisplay } from "../lib/runStatus";
import type { RunSummaryJson } from "../lib/types";

// ---------------------------------------------------------------------------
// RecentRunsList — the "pick up where you left off" panel on the homepage
// home-base column (PLAN-homepage-redesign.md). Stateless: the parent owns
// fetching and passes the rows in.
//
// Drafts route back to /run/{id} (onResumeDraft) so the user can finish
// configuring and start; everything else opens the run's detail in History
// (onOpenRun). This mirrors HistoryList's draft-vs-non-draft split so the two
// surfaces behave identically. Status badges reuse runStatusDisplay() for the
// same reason.
// ---------------------------------------------------------------------------

export interface RecentRunsListProps {
  runs: RunSummaryJson[];
  isLoading?: boolean;
  error?: string | null;
  onResumeDraft: (runId: number) => void;
  onOpenRun: (runId: number) => void;
  onViewAll: () => void;
}

function formatDate(iso: string): string {
  // Local time, matching HistoryList. Fall back to the raw string if the
  // backend ever sends something unparseable.
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  return d.toLocaleString();
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
    <div style={styles.panel}>
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
            return (
              <div
                key={run.id}
                role="button"
                tabIndex={0}
                onClick={activate}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    activate();
                  }
                }}
                style={styles.card}
              >
                <div style={styles.cardTop}>
                  <span style={styles.filename} title={run.pdf_filename}>
                    {run.pdf_filename}
                  </span>
                  <span style={styles.action}>{isDraft ? "Resume" : "View"}</span>
                </div>
                <div style={styles.cardMeta}>
                  <span
                    style={{ ...styles.badge, color: display.color, background: display.bg }}
                  >
                    {display.label}
                  </span>
                  <span style={styles.date}>{formatDate(run.created_at)}</span>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

const styles = {
  panel: {
    ...ui.card,
    display: "flex",
    flexDirection: "column" as const,
    padding: pwc.space.lg,
    gap: pwc.space.md,
  } as React.CSSProperties,
  header: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
  } as React.CSSProperties,
  heading: {
    fontFamily: pwc.fontHeading,
    fontSize: 14,
    fontWeight: pwc.weight.semibold,
    color: pwc.grey900,
  } as React.CSSProperties,
  viewAll: {
    fontFamily: pwc.fontBody,
    fontSize: 13,
    color: pwc.orange500,
    background: "none",
    border: "none",
    cursor: "pointer",
    padding: 0,
  } as React.CSSProperties,
  list: {
    display: "flex",
    flexDirection: "column" as const,
    gap: pwc.space.sm,
  } as React.CSSProperties,
  card: {
    display: "flex",
    flexDirection: "column" as const,
    gap: pwc.space.xs,
    padding: `${pwc.space.sm}px ${pwc.space.md}px`,
    border: `1px solid ${pwc.grey200}`,
    borderRadius: pwc.radius.md,
    background: pwc.white,
    cursor: "pointer",
    transition: "background 120ms ease",
  } as React.CSSProperties,
  cardTop: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    gap: pwc.space.sm,
  } as React.CSSProperties,
  filename: {
    fontFamily: pwc.fontBody,
    fontWeight: pwc.weight.medium,
    fontSize: 14,
    color: pwc.grey900,
    // Long statement filenames truncate rather than wrapping the card to
    // several lines; full name is on the title attribute.
    whiteSpace: "nowrap" as const,
    overflow: "hidden",
    textOverflow: "ellipsis",
    minWidth: 0,
  } as React.CSSProperties,
  action: {
    fontFamily: pwc.fontBody,
    fontSize: 13,
    color: pwc.orange500,
    flexShrink: 0,
  } as React.CSSProperties,
  cardMeta: {
    display: "flex",
    alignItems: "center",
    gap: pwc.space.sm,
  } as React.CSSProperties,
  badge: {
    display: "inline-block",
    padding: `1px ${pwc.space.sm}px`,
    borderRadius: pwc.radius.lg,
    fontSize: 12,
    fontWeight: pwc.weight.medium,
    lineHeight: 1.5,
  } as React.CSSProperties,
  date: {
    fontFamily: pwc.fontBody,
    fontSize: 12,
    color: pwc.grey500,
    whiteSpace: "nowrap" as const,
    overflow: "hidden",
    textOverflow: "ellipsis",
  } as React.CSSProperties,
  placeholder: {
    margin: 0,
    fontFamily: pwc.fontBody,
    fontSize: 13,
    color: pwc.grey500,
  } as React.CSSProperties,
} as const;
