import { useCallback, useEffect, useRef, useState } from "react";
import { pwc } from "../lib/theme";
import { HistoryFilters } from "../components/HistoryFilters";
import { HistoryList } from "../components/HistoryList";
import { RunDetailModal } from "../components/RunDetailModal";
import { fetchRuns, fetchRunDetail, deleteRun, downloadFilledUrl } from "../lib/api";
import type { RunDetailJson, RunSummaryJson, RunsFilterParams } from "../lib/types";

// ---------------------------------------------------------------------------
// HistoryPage — top-level view for browsing past extraction runs.
//
// Owns:
//   - `filters`      — current filter state, refetches on change
//   - `runs`         — the currently-visible list page
//   - `selectedId`   — which run's detail panel is open (if any)
//   - `detail`       — the hydrated detail payload for `selectedId`
//
// The list and detail are two separate backend calls so we can page through
// summaries quickly without loading heavy per-run data until the user asks.
// ---------------------------------------------------------------------------

// Page size for History list — matches backend default of 50. Kept as a
// module constant so the test and the production code use the same number.
const PAGE_SIZE = 50;

export function HistoryPage() {
  const [filters, setFilters] = useState<RunsFilterParams>({});
  const [runs, setRuns] = useState<RunSummaryJson[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Pagination state. `total` is the server's count of all rows matching
  // the current filters; `runs.length` is how many we've actually loaded.
  // The "Load more" button is shown when those two diverge. We don't track
  // a separate "current offset" — handleLoadMore derives the next offset
  // from `runs.length`, which keeps the two states in sync automatically.
  const [total, setTotal] = useState(0);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  // Pagination errors are kept separate from the page-level `error` so a
  // failed Load more doesn't hide the rows the user has already loaded.
  // Initial-load failures still use `error` and blank the table.
  const [loadMoreError, setLoadMoreError] = useState<string | null>(null);

  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [detail, setDetail] = useState<RunDetailJson | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [isDetailLoading, setIsDetailLoading] = useState(false);

  // `refetchKey` is bumped after destructive operations (delete) to force a
  // fresh list load without touching the filter state.
  const [refetchKey, setRefetchKey] = useState(0);

  // Filter changes implicitly reset pagination because the first-page
  // effect below replaces (not appends) `runs` whenever filtersKey changes.
  // Serializing filters into a key keeps the effect dependency stable
  // across re-renders that don't actually mutate filter values.
  const filtersKey = JSON.stringify(filters);

  // Mirror of `filtersKey` accessible from callbacks that outlive a render.
  // `handleLoadMore` uses this to detect that filters have changed during
  // its in-flight request and discard the stale response — without it, the
  // load-more append would contaminate the newly-filtered list with rows
  // from the previous filter set.
  const filtersKeyRef = useRef(filtersKey);
  useEffect(() => {
    filtersKeyRef.current = filtersKey;
  }, [filtersKey]);

  // Fetch the FIRST page whenever filters or refetchKey change. Subsequent
  // pages are loaded by `handleLoadMore` below, which appends rather than
  // replacing — so we deliberately split the two flows. The cancelled flag
  // guards against an out-of-order response from a stale query overwriting
  // a newer result.
  useEffect(() => {
    let cancelled = false;
    setIsLoading(true);
    setError(null);
    // Filter/refetch change implicitly retries pagination from page one,
    // so any lingering Load more error from the previous filter set is
    // no longer relevant — clear it so the user doesn't see a stale
    // banner under fresh results.
    setLoadMoreError(null);
    fetchRuns({ ...filters, limit: PAGE_SIZE, offset: 0 })
      .then((res) => {
        if (cancelled) return;
        setRuns(res.runs);
        setTotal(res.total);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        const msg = err instanceof Error ? err.message : "Failed to load run history";
        setError(msg);
        setRuns([]);
        setTotal(0);
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [filtersKey, refetchKey]);

  // Append the next page. Uses the current `runs.length` as the offset so
  // we naturally chain pages without tracking a separate page counter.
  //
  // Stale-response guard: we snapshot filtersKey at call time and bail
  // out in the then-branch if filters have changed since. Without this,
  // rapidly typing in the search box while a load-more is in flight used
  // to append rows from the OLD filter set onto the newly-filtered list.
  const handleLoadMore = useCallback(async () => {
    const snapshotFiltersKey = filtersKeyRef.current;
    setIsLoadingMore(true);
    setLoadMoreError(null);
    try {
      const nextOffset = runs.length;
      const res = await fetchRuns({ ...filters, limit: PAGE_SIZE, offset: nextOffset });
      // Filters changed mid-flight → the first-page effect has already
      // replaced `runs`, so appending these rows would corrupt the view.
      // The first-page effect owns the fresh list; drop this response.
      if (filtersKeyRef.current !== snapshotFiltersKey) {
        return;
      }
      setRuns((prev) => [...prev, ...res.runs]);
      setTotal(res.total);
    } catch (err) {
      if (filtersKeyRef.current !== snapshotFiltersKey) {
        return;
      }
      const msg = err instanceof Error ? err.message : "Failed to load more runs";
      // Dedicated pagination-error state — the page-level `error` would
      // blank the already-loaded rows via HistoryList's error early-return.
      setLoadMoreError(msg);
    } finally {
      setIsLoadingMore(false);
    }
  }, [runs.length, filters]);

  // Fetch detail whenever `selectedId` changes.
  useEffect(() => {
    if (selectedId == null) {
      setDetail(null);
      setDetailError(null);
      return;
    }
    let cancelled = false;
    setIsDetailLoading(true);
    setDetailError(null);
    fetchRunDetail(selectedId)
      .then((d) => {
        if (cancelled) return;
        setDetail(d);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        const msg = err instanceof Error ? err.message : "Failed to load run detail";
        setDetailError(msg);
        setDetail(null);
      })
      .finally(() => {
        if (!cancelled) setIsDetailLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedId]);

  const handleRunSelected = useCallback((id: number) => {
    setSelectedId(id);
  }, []);

  // Delete from the detail panel: hit the API, clear the selection, and
  // bump `refetchKey` so the list reloads without the deleted row. If the
  // API call fails we surface the error in the detail panel and leave the
  // list alone — safer than optimistically removing a row that's still on
  // the server.
  const handleDelete = useCallback(async (runId: number) => {
    try {
      await deleteRun(runId);
      setSelectedId(null);
      setRefetchKey((k) => k + 1);
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Delete failed";
      setDetailError(msg);
    }
  }, []);

  // Download: navigate the top-level window to the streaming endpoint. The
  // browser treats this as an xlsx attachment (via Content-Disposition on the
  // FileResponse) so no <a> dance is needed.
  const handleDownload = useCallback((runId: number) => {
    window.location.href = downloadFilledUrl(runId);
  }, []);

  return (
    <div style={styles.container}>
      <h2 style={styles.heading}>Run history</h2>
      <HistoryFilters value={filters} onChange={setFilters} />
      <HistoryList
        runs={runs}
        isLoading={isLoading}
        error={error}
        selectedId={selectedId}
        onRunSelected={handleRunSelected}
      />
      {/* Pagination control — only shown when more rows exist on the
          server than we've loaded so far. Suppressed during the very
          first load so users don't see a "Load more" flash before the
          first page even arrives. */}
      {!isLoading && runs.length < total && (
        <button
          type="button"
          onClick={handleLoadMore}
          disabled={isLoadingMore}
          style={styles.loadMoreBtn}
        >
          {isLoadingMore
            ? "Loading…"
            : `Load more (${total - runs.length} remaining)`}
        </button>
      )}
      {/* Pagination error — inline, non-destructive. Shown directly below
          the Load more button so the retry affordance stays visible and
          the already-loaded rows remain in view. */}
      {loadMoreError && (
        <div role="alert" style={styles.loadMoreError}>
          {loadMoreError}
        </div>
      )}
      {/* Detail lives in a modal rather than a side pane. The list keeps
          the full main-content width, and the modal gives the 6-column
          cross-check table enough room to render without clipping. */}
      <RunDetailModal
        isOpen={selectedId != null}
        onClose={() => setSelectedId(null)}
        detail={detail}
        isLoading={isDetailLoading}
        error={detailError}
        onDownload={handleDownload}
        onDelete={handleDelete}
      />
    </div>
  );
}

const styles = {
  container: {
    display: "flex",
    flexDirection: "column" as const,
    gap: pwc.space.lg,
  } as React.CSSProperties,
  heading: {
    fontFamily: pwc.fontHeading,
    fontSize: 20,
    fontWeight: 600,
    color: pwc.grey900,
    margin: 0,
  } as React.CSSProperties,
  loadMoreBtn: {
    marginTop: pwc.space.md,
    padding: `${pwc.space.sm}px ${pwc.space.lg}px`,
    fontFamily: pwc.fontHeading,
    fontSize: 13,
    fontWeight: 600,
    color: pwc.orange500,
    background: pwc.white,
    border: `1px solid ${pwc.orange500}`,
    borderRadius: pwc.radius.md,
    cursor: "pointer",
    width: "100%",
  } as React.CSSProperties,
  loadMoreError: {
    marginTop: pwc.space.sm,
    padding: `${pwc.space.sm}px ${pwc.space.md}px`,
    background: "#FEF2F2",
    color: "#B91C1C",
    border: "1px solid #FECACA",
    borderRadius: pwc.radius.md,
    fontFamily: pwc.fontBody,
    fontSize: 13,
  } as React.CSSProperties,
} as const;
