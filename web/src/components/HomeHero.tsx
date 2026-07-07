import { useEffect, useState } from "react";
import { userMessage } from "../lib/errors";
import { pwc } from "../lib/theme";
import { fetchHomeStats, fetchRecentRuns } from "../lib/api";
import type { HomeStats } from "../lib/api";
import type { RunSummaryJson } from "../lib/types";
import { StatTiles } from "./StatTiles";
import { RecentRunsList } from "./RecentRunsList";

// ---------------------------------------------------------------------------
// HomeHero — the homepage landing layout (PLAN-homepage-redesign.md).
//
// Single column, three stacked sections in the empty landing state:
//   1. Stat tiles — high-level counts across the top.
//   2. Upload — the drop zone (passed in as children).
//   3. Recent runs — history pane at the bottom.
//
// The upload card (children) is rendered in a stable tree position so it
// never remounts when the page leaves the empty state (`active` → false),
// preserving its internal upload state. When inactive, only the upload card
// shows — the stats + history sections collapse away.
//
// HomeHero owns the stats + recents fetch. Any failure degrades to
// placeholders in the child components rather than blocking the upload card.
// ---------------------------------------------------------------------------

export interface HomeHeroProps {
  /** Show the stats + history sections. False once an upload exists or a run
   *  starts, collapsing the layout to just the upload card. */
  active: boolean;
  /** Resume an unfinished draft → routes to /run/{id}. */
  onResumeDraft: (runId: number) => void;
  /** Open a finished run's detail in History. */
  onOpenRun: (runId: number) => void;
  /** Jump to the full History list. */
  onViewAllRuns: () => void;
  /** The upload card (middle section). */
  children: React.ReactNode;
}

const RECENT_LIMIT = 5;

export function HomeHero({
  active,
  onResumeDraft,
  onOpenRun,
  onViewAllRuns,
  children,
}: HomeHeroProps) {
  const [stats, setStats] = useState<HomeStats | null>(null);
  const [recent, setRecent] = useState<RunSummaryJson[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Fetch the home data when the sections become active. We refetch each time
  // they re-activate (e.g. user uploads, then resets back to the empty page)
  // so the counts stay fresh after a run completes. The cancelled flag guards
  // against a late response landing after the sections have gone away.
  useEffect(() => {
    if (!active) return;
    let cancelled = false;
    setIsLoading(true);
    setError(null);
    Promise.all([fetchHomeStats(), fetchRecentRuns(RECENT_LIMIT)])
      .then(([s, runs]) => {
        if (cancelled) return;
        setStats(s);
        setRecent(runs);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        // Quiet degradation: keep whatever we had, flag the error so the
        // children show placeholders. Never blocks the upload card.
        setError(userMessage(err));
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [active]);

  // Last-run status is derived from the recent list rather than a dedicated
  // fetch — the newest run is simply the first row.
  const lastStatus = recent.length > 0 ? recent[0].status : null;

  return (
    <div style={styles.stack}>
      {active && (
        <StatTiles
          // While loading (and no prior data) the counts are undefined → the
          // tiles render dashes. On error they also stay undefined.
          total={error ? undefined : stats?.total}
          drafts={error ? undefined : stats?.drafts}
          completedThisMonth={error ? undefined : stats?.completedThisMonth}
          // null while loading so the badge doesn't flash a stale status;
          // resolves to the real status (or null for no-runs) once loaded.
          lastStatus={isLoading || error ? null : lastStatus}
        />
      )}

      {children}

      {active && (
        <RecentRunsList
          runs={recent}
          isLoading={isLoading && recent.length === 0}
          error={error}
          onResumeDraft={onResumeDraft}
          onOpenRun={onOpenRun}
          onViewAll={onViewAllRuns}
        />
      )}
    </div>
  );
}

const styles = {
  // Single-column stack: stats band → upload → history pane. Generous gap so
  // the three sections read as distinct bands rather than a cramped list.
  stack: {
    display: "flex",
    flexDirection: "column" as const,
    gap: pwc.space.xl,
  } as React.CSSProperties,
} as const;
