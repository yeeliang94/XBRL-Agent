import type { ReactNode } from "react";
import { pwc } from "../lib/theme";
import { ui } from "../lib/uiStyles";
import type { CrossCheckResult } from "../lib/types";
import type { CoverageNavRow } from "./NotesCoverageNav";

/**
 * "Needs attention" queue for the review workspace (docs/PLAN-review-workspace.md
 * Phase 4). One place that answers "am I done?" by uniting the three feeds a
 * reviewer would otherwise chase across separate panels/tabs:
 *
 *   1. failing / warning cross-checks   → jump to the target cell
 *   2. unresolved notes coverage gaps   → jump to the note (or its PDF pages)
 *   3. open reconciliation conflicts     → the existing ReconciliationQueue,
 *                                          passed in verbatim (not rewritten)
 *
 * The header count spans all three. When nothing is outstanding it renders a
 * quiet all-clear line instead of an empty box. Inline styles + theme tokens
 * only (gotcha #7).
 */

interface Props {
  /** Cross-checks that failed or warned (the caller filters). */
  failingChecks: CrossCheckResult[];
  /** Navigate to a check's target cell. */
  onSelectCheck: (sheet: string, row: number) => void;
  /** Unresolved notes coverage gaps (missing / suspected gap). */
  coverageGaps: CoverageNavRow[];
  /** Navigate to a coverage note. */
  onSelectNote: (row: CoverageNavRow) => void;
  /** Open reconciliation conflict count — for the header total. */
  openConflicts: number;
  /** The existing ReconciliationQueue, rendered as the conflicts section so its
   *  resolve/dismiss actions are reused, not reimplemented. */
  reconciliation: ReactNode;
}

export function NeedsAttentionPanel({
  failingChecks,
  onSelectCheck,
  coverageGaps,
  onSelectNote,
  openConflicts,
  reconciliation,
}: Props) {
  const total = failingChecks.length + coverageGaps.length + openConflicts;

  if (total === 0) {
    return (
      <div data-testid="needs-attention-clear" style={styles.clear}>
        <span aria-hidden="true" style={ui.badgeDot(pwc.success)} />
        All clear — nothing needs your attention.
      </div>
    );
  }

  return (
    <div data-testid="needs-attention" style={styles.panel}>
      <div style={styles.header} data-testid="needs-attention-count">
        Needs attention ({total})
      </div>

      {failingChecks.length > 0 && (
        <ul style={styles.list} aria-label="Checks that need attention">
          {failingChecks.map((c, i) => {
            const canJump = !!c.target_sheet && c.target_row != null;
            const dot = c.status === "warning" ? pwc.warning : pwc.error;
            return (
              <li key={`chk-${c.name}-${i}`}>
                <button
                  type="button"
                  style={{ ...styles.item, cursor: canJump ? "pointer" : "default" }}
                  disabled={!canJump}
                  onClick={() =>
                    canJump && onSelectCheck(c.target_sheet as string, c.target_row as number)
                  }
                  data-testid={`attention-check-${i}`}
                >
                  <span aria-hidden="true" style={ui.badgeDot(dot)} />
                  <span style={styles.itemText}>
                    {c.message || c.name}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      )}

      {coverageGaps.length > 0 && (
        <ul style={styles.list} aria-label="Notes that need attention">
          {coverageGaps.map((row) => (
            <li key={`gap-${row.note_num}`}>
              <button
                type="button"
                style={{ ...styles.item, cursor: "pointer" }}
                onClick={() => onSelectNote(row)}
                data-testid={`attention-note-${row.note_num}`}
              >
                <span aria-hidden="true" style={ui.badgeDot(pwc.error)} />
                <span style={styles.itemText}>
                  Note {row.note_num}
                  {row.title ? `: ${row.title}` : ""} — not placed
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}

      {openConflicts > 0 && (
        <div style={styles.conflicts} data-testid="attention-conflicts">
          <div style={styles.subHead}>Figures to reconcile ({openConflicts})</div>
          {reconciliation}
        </div>
      )}
    </div>
  );
}

const styles = {
  panel: {
    display: "flex",
    flexDirection: "column" as const,
    gap: pwc.space.sm,
  } as const,
  clear: {
    display: "flex",
    alignItems: "center",
    gap: pwc.space.sm,
    color: pwc.grey500,
    fontSize: 13,
  } as const,
  header: {
    fontFamily: pwc.fontHeading,
    fontWeight: 600,
    fontSize: 13,
    color: pwc.grey900,
  } as const,
  list: {
    listStyle: "none",
    margin: 0,
    padding: 0,
    display: "flex",
    flexDirection: "column" as const,
    gap: 2,
  } as const,
  item: {
    display: "flex",
    alignItems: "center",
    gap: pwc.space.sm,
    width: "100%",
    background: "transparent",
    border: "none",
    borderRadius: pwc.radius.sm,
    padding: `${pwc.space.xs}px ${pwc.space.sm}px`,
    textAlign: "left" as const,
    fontFamily: pwc.fontBody,
    fontSize: 13,
    color: pwc.grey800,
  } as const,
  itemText: {
    overflow: "hidden",
    textOverflow: "ellipsis",
  } as const,
  conflicts: {
    marginTop: pwc.space.xs,
  } as const,
  subHead: {
    fontSize: 12,
    fontWeight: 600,
    color: pwc.grey500,
    marginBottom: pwc.space.xs,
  } as const,
};
