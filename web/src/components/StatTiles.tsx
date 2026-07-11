import { pwc } from "../lib/theme";
import { ui, uiClass } from "../lib/uiStyles";
import { runStatusDisplay } from "../lib/runStatus";
import { AnimatedNumber } from "./AnimatedNumber";

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
  /** When provided AND drafts > 0, a "Clear" action shows on the drafts tile
   *  to sweep abandoned drafts (E3). Omitted → no action rendered. */
  onClearDrafts?: () => void;
}

/** Show the number (counting up when it changes), or a dash while it's
 *  missing (loading / fetch failed). AnimatedNumber shows the value instantly
 *  on first mount, so a freshly-loaded page never rolls up from zero. */
function Count({ n }: { n: number | undefined }) {
  if (n == null) return <span style={styles.value}>—</span>;
  return <AnimatedNumber value={n} style={styles.value} />;
}

export function StatTiles({
  total,
  drafts,
  completedThisMonth,
  lastStatus,
  onClearDrafts,
}: StatTilesProps) {
  const last = lastStatus ? runStatusDisplay(lastStatus) : null;
  const canClearDrafts = onClearDrafts != null && (drafts ?? 0) > 0;
  return (
    <div style={styles.grid}>
      <div style={styles.tile}>
        <Count n={total} />
        <span style={styles.label}>Total runs</span>
      </div>
      <div style={styles.tile}>
        <Count n={drafts} />
        <span style={styles.labelRow}>
          <span style={styles.label}>Unstarted drafts</span>
          {canClearDrafts && (
            <button
              type="button"
              className={uiClass.btnGhost}
              style={styles.clearLink}
              onClick={onClearDrafts}
              data-testid="clear-drafts"
            >
              Clear
            </button>
          )}
        </span>
      </div>
      <div style={styles.tile}>
        <Count n={completedThisMonth} />
        <span style={styles.label}>Completed this month</span>
      </div>
      {/* Visually detached from the three counters — it holds a status chip,
          not a number, so a subtle neutral surface marks it as a different
          KIND of tile rather than a broken counter (E4). */}
      <div style={styles.statusTile}>
        {last ? (
          <span style={{ ...ui.badge, alignSelf: "flex-start", borderColor: last.accent }}>
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
  // The status tile: same card, but a neutral grey surface so it reads as a
  // distinct "last run" panel next to the three white counter tiles (E4).
  statusTile: {
    ...ui.card,
    background: pwc.grey50,
    display: "flex",
    flexDirection: "column" as const,
    gap: pwc.space.xs,
    padding: pwc.space.lg,
    minWidth: 0,
  } as React.CSSProperties,
  labelRow: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    gap: pwc.space.sm,
  } as React.CSSProperties,
  clearLink: {
    ...ui.buttonGhost,
    padding: 0,
    minHeight: 0,
    fontSize: 12,
  } as React.CSSProperties,
  value: {
    fontFamily: pwc.fontHeading,
    // Regular weight on the large number — hierarchy from size, not boldness
    // (design system: two text weights, no Light 300).
    fontWeight: pwc.weight.regular,
    fontSize: 28,
    lineHeight: 1.1,
    color: pwc.grey900,
  } as React.CSSProperties,
  label: {
    fontFamily: pwc.fontBody,
    fontSize: 13,
    color: pwc.grey500,
  } as React.CSSProperties,
} as const;
