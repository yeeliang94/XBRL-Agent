import { useCallback, useEffect, useState } from "react";
import { userMessage } from "../lib/errors";
import { pwc } from "../lib/theme";
import { fetchHomeStats, fetchRecentRuns, deleteDraftRuns } from "../lib/api";
import type { HomeStats } from "../lib/api";
import type { RunSummaryJson } from "../lib/types";
import { StatTiles } from "./StatTiles";
import { RecentRunsList } from "./RecentRunsList";
import { ConfirmDialog } from "./ConfirmDialog";

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
  // Draft-cleanup confirm gate (E3).
  const [confirmClearDrafts, setConfirmClearDrafts] = useState(false);
  const [clearingDrafts, setClearingDrafts] = useState(false);

  // One loader so both the mount effect and a post-cleanup refresh share the
  // same fetch. Not cancellable here (callers that need cancellation wrap it).
  const load = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const [s, runs] = await Promise.all([
        fetchHomeStats(),
        fetchRecentRuns(RECENT_LIMIT),
      ]);
      setStats(s);
      setRecent(runs);
    } catch (err) {
      // Quiet degradation: keep whatever we had, flag the error so the
      // children show placeholders. Never blocks the upload card.
      setError(userMessage(err));
    } finally {
      setIsLoading(false);
    }
  }, []);

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
        setError(userMessage(err));
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [active]);

  const handleClearDrafts = useCallback(async () => {
    setClearingDrafts(true);
    try {
      await deleteDraftRuns();
      setConfirmClearDrafts(false);
      await load(); // refresh counts + recents so the swept drafts disappear
    } catch (err) {
      setError(userMessage(err));
    } finally {
      setClearingDrafts(false);
    }
  }, [load]);

  return (
    <div style={styles.stack}>
      {/* Upload leads — it is the page's primary purpose. Rendering it first
          ALWAYS (active or not) keeps UploadPanel in a stable tree position
          so it never remounts and loses upload state. */}
      {children}

      {active && (
        <StatTiles
          // While loading (and no prior data) the counts are undefined → the
          // tiles render dashes. On error they also stay undefined.
          needsReview={error ? undefined : stats?.needsReview}
          active={error ? undefined : stats?.active}
          drafts={error ? undefined : stats?.drafts}
          completedThisMonth={error ? undefined : stats?.completedThisMonth}
          onClearDrafts={() => setConfirmClearDrafts(true)}
        />
      )}

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

      <ConfirmDialog
        isOpen={confirmClearDrafts}
        title="Clear abandoned drafts?"
        message={
          <>
            This permanently removes{" "}
            <strong>{stats?.drafts ?? 0} draft{(stats?.drafts ?? 0) === 1 ? "" : "s"}</strong>{" "}
            — uploads that were never started. Completed and in-progress runs
            are not affected, and the original PDFs on disk are kept.
          </>
        }
        confirmLabel="Clear drafts"
        danger
        busy={clearingDrafts}
        busyLabel="Clearing…"
        onConfirm={handleClearDrafts}
        onCancel={() => setConfirmClearDrafts(false)}
      />
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
