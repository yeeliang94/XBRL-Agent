import { pwc, tokens } from "../lib/theme";
import { ui, uiClass } from "../lib/uiStyles";
import { STATUS_SYMBOLS } from "../lib/runStatus";
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
// Design-system adoption (plan CS3): quiet flat ui.statTile metrics, not
// four elevated cards; actionable counts (Needs review, drafts) lead the
// row and carry the neutral ! / – symbols — never decorative colour.
// The four/two/one column breakpoints live on the `stat-tiles` class.
// ---------------------------------------------------------------------------

export interface StatTilesProps {
  needsReview?: number;
  active?: number;
  drafts?: number;
  completedThisMonth?: number;
  /** When provided AND drafts > 0, a "Clear drafts" action shows on the
   *  drafts tile to sweep abandoned drafts (E3). Omitted → no action. */
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
  needsReview,
  active,
  drafts,
  completedThisMonth,
  onClearDrafts,
}: StatTilesProps) {
  const canClearDrafts = onClearDrafts != null && (drafts ?? 0) > 0;
  return (
    <div className="stat-tiles" style={styles.grid}>
      {/* Actionable work leads: review queue first, unstarted drafts second. */}
      <div style={styles.tile}>
        <Count n={needsReview} />
        <span style={styles.label}>
          <span aria-hidden="true" style={styles.symbol}>{STATUS_SYMBOLS.attention}</span>
          Needs review
        </span>
      </div>
      <div style={styles.tile}>
        <Count n={drafts} />
        <span style={styles.labelRow}>
          <span style={styles.label}>
            <span aria-hidden="true" style={styles.symbol}>{STATUS_SYMBOLS.inactive}</span>
            Not started
          </span>
          {canClearDrafts && (
            <button
              type="button"
              className={uiClass.btnQuiet}
              style={styles.clearLink}
              onClick={onClearDrafts}
              data-testid="clear-drafts"
            >
              Clear drafts
            </button>
          )}
        </span>
      </div>
      <div style={styles.tile}>
        <Count n={active} />
        <span style={styles.label}>Active runs</span>
      </div>
      <div style={styles.tile}>
        <Count n={completedThisMonth} />
        <span style={styles.label}>Completed this month</span>
      </div>
    </div>
  );
}

const styles = {
  // Column counts come from the `stat-tiles` class (index.css): four columns
  // on desktop, two on tablet, one on narrow screens — an intentional
  // composition instead of auto-fit guesswork.
  grid: {
    gap: pwc.space.md,
  } as React.CSSProperties,
  tile: {
    ...ui.statTile,
    display: "flex",
    flexDirection: "column" as const,
    gap: pwc.space.xs,
    minWidth: 0,
  } as React.CSSProperties,
  labelRow: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    gap: pwc.space.sm,
  } as React.CSSProperties,
  clearLink: {
    ...ui.buttonQuiet,
    padding: "2px 6px",
    minHeight: 24,
    fontSize: 12,
    color: tokens.color.action.primary,
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
    color: tokens.color.text.secondary,
    display: "inline-flex",
    alignItems: "center",
    gap: 6,
  } as React.CSSProperties,
  symbol: {
    ...ui.statusSymbol,
    width: "auto",
  } as React.CSSProperties,
} as const;
