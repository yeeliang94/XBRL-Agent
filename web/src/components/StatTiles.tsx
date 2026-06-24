import { pwc } from "../lib/theme";
import { ui } from "../lib/uiStyles";
import { runStatusDisplay } from "../lib/runStatus";

// ---------------------------------------------------------------------------
// StatTiles — the four headline counts on the homepage "home base" column
// (PLAN-homepage-redesign.md). Stateless: the parent (HomeHero) owns the
// data fetch and passes plain values in.
//
// Each count is `number | undefined`. `undefined` means "not loaded yet or
// the fetch failed" and renders as a dash, so a slow/broken stats call
// degrades to a quiet placeholder instead of blocking the column.
//
// `lastStatus` reuses runStatusDisplay() so the "Last run" tile reads with
// the exact same label + colour as the History list.
// ---------------------------------------------------------------------------

export interface StatTilesProps {
  total?: number;
  drafts?: number;
  completedThisMonth?: number;
  /** Status string of the most-recent run, or null when there are none. */
  lastStatus?: string | null;
}

/** Show the number, or a dash while it's missing (loading / fetch failed). */
function fmtCount(n: number | undefined): string {
  return n == null ? "—" : n.toLocaleString();
}

export function StatTiles({ total, drafts, completedThisMonth, lastStatus }: StatTilesProps) {
  const last = lastStatus ? runStatusDisplay(lastStatus) : null;
  return (
    <div style={styles.grid}>
      <div style={styles.tile}>
        <span style={styles.value}>{fmtCount(total)}</span>
        <span style={styles.label}>Total runs</span>
      </div>
      <div style={styles.tile}>
        <span style={styles.value}>{fmtCount(drafts)}</span>
        <span style={styles.label}>Drafts in progress</span>
      </div>
      <div style={styles.tile}>
        <span style={styles.value}>{fmtCount(completedThisMonth)}</span>
        <span style={styles.label}>Completed this month</span>
      </div>
      <div style={styles.tile}>
        {last ? (
          <span style={{ ...styles.badge, borderColor: last.accent }}>
            <span aria-hidden="true" style={ui.badgeDot(last.accent)} />
            {last.label}
          </span>
        ) : (
          // null lastStatus (no runs) or undefined (still loading) both show
          // the neutral dash — the empty/loading state of this tile.
          <span style={styles.value}>—</span>
        )}
        <span style={styles.label}>Last run status</span>
      </div>
    </div>
  );
}

const styles = {
  // Full-width band across the top of the homepage: four tiles in a row.
  // auto-fit + a minmax floor keeps them on one line on a normal desktop
  // (the container caps at ~1120px) while wrapping gracefully if the viewport
  // gets narrow rather than crushing the labels.
  grid: {
    display: "grid",
    gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))",
    gap: pwc.space.md,
  } as React.CSSProperties,
  tile: {
    ...ui.card,
    display: "flex",
    flexDirection: "column" as const,
    gap: pwc.space.xs,
    padding: pwc.space.lg,
    minWidth: 0,
  } as React.CSSProperties,
  value: {
    fontFamily: pwc.fontHeading,
    // Light weight on the large number matches the design system's heading
    // treatment (hierarchy from size, not boldness).
    fontWeight: pwc.weight.light,
    fontSize: 28,
    lineHeight: 1.1,
    color: pwc.grey900,
  } as React.CSSProperties,
  label: {
    fontFamily: pwc.fontBody,
    fontSize: 13,
    color: pwc.grey500,
  } as React.CSSProperties,
  badge: {
    alignSelf: "flex-start" as const,
    display: "inline-block",
    padding: `2px ${pwc.space.sm}px`,
    borderRadius: pwc.radius.lg,
    fontSize: 13,
    fontWeight: pwc.weight.medium,
    lineHeight: 1.6,
  } as React.CSSProperties,
} as const;
