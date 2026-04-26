import { useCallback, useEffect, useRef, useState } from "react";
import { pwc } from "../lib/theme";
import { HistoryFilters } from "../components/HistoryFilters";
import { HistoryList } from "../components/HistoryList";
import { RunDetailPage } from "../components/RunDetailPage";
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

export interface HistoryPageProps {
  /** Which run's full-page detail is open, or null for the list view.
   *  Supplied by App when the URL is driving state; omitted for legacy
   *  callers that let HistoryPage manage its own selection internally. */
  selectedId?: number | null;
  /** Called when the user clicks a row (id) or Back (null). Paired with
   *  `selectedId` — both are either provided together (controlled mode)
   *  or both omitted (uncontrolled, internal-state mode). */
  onSelectRun?: (runId: number | null) => void;
  /** PLAN-persistent-draft-uploads.md (Phase D): clicking a draft row
   *  routes to `/run/{id}` instead of opening the inline RunDetailPage.
   *  App passes a handler that dispatches SET_VIEW + SET_CURRENT_RUN_ID. */
  onResumeDraft?: (runId: number) => void;
}

export function HistoryPage({ selectedId: selectedIdProp, onSelectRun, onResumeDraft }: HistoryPageProps = {}) {
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

  // Controlled mode: parent (App) owns selection so the URL can round-trip.
  // Uncontrolled mode: we keep local state, used by standalone tests and
  // any legacy caller. The `?? null` guard treats `undefined` prop as "not
  // controlled" so a caller passing `selectedId={undefined}` doesn't flip
  // the component into a broken in-between state.
  const [internalSelectedId, setInternalSelectedId] = useState<number | null>(null);
  const isControlled = selectedIdProp !== undefined;
  const selectedId = isControlled ? (selectedIdProp ?? null) : internalSelectedId;
  const setSelectedId = useCallback(
    (id: number | null) => {
      if (isControlled) onSelectRun?.(id);
      else setInternalSelectedId(id);
    },
    [isControlled, onSelectRun],
  );
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

  // Step 12: Regenerate notes — the NotesReviewTab already showed the
  // confirm dialog when any cells were user-edited, so by the time this
  // handler fires the user has opted in to clobbering their edits.
  //
  // Peer-review [HIGH] #1: earlier this redirected to `?session=...#notes`,
  // a URL no code consumed. The regenerate now POSTs to the
  // `/api/runs/{id}/rerun-notes` endpoint (which reads the run's stored
  // session + config server-side and kicks off a notes-only
  // run_multi_agent_stream). We consume the SSE stream just enough to
  // know when it's done, then refresh the run detail so the editor
  // picks up the fresh notes cells.
  const [regenStatus, setRegenStatus] = useState<
    "idle" | "running" | "succeeded" | "failed"
  >("idle");
  const handleRegenerateNotes = useCallback(
    async (targetRunId: number) => {
      setRegenStatus("running");
      try {
        const resp = await fetch(`/api/runs/${targetRunId}/rerun-notes`, {
          method: "POST",
        });
        if (!resp.ok || !resp.body) {
          const msg = await resp.text().catch(() => "");
          setRegenStatus("failed");
          console.error(
            `[regenerate-notes] ${resp.status}: ${msg || "empty body"}`,
          );
          return;
        }
        // Consume the SSE stream until `run_complete` arrives. We don't
        // render per-event progress in History — this is a "show a
        // spinner, refresh when done" flow. ExtractPage remains the
        // live-streaming surface for in-progress runs.
        //
        // The regenerate creates a NEW run_id server-side (every
        // run_multi_agent_stream invocation inserts a fresh runs row).
        // We parse it out of the stream so we can navigate the detail
        // page to the new run when the stream finishes — otherwise
        // the page would keep displaying the old run's notes cells
        // indefinitely, even though the new run is the fresh one.
        const reader = resp.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        let completed = false;
        let newRunId: number | null = null;
        while (!completed) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          // Opportunistically snag the run_id from any SSE `data:`
          // line that carries it. Both the `status: starting` and
          // `run_complete` events include it now. Cheap substring
          // extraction avoids parsing every frame.
          if (newRunId === null) {
            const match = buffer.match(/"run_id"\s*:\s*(\d+)/);
            if (match) newRunId = parseInt(match[1], 10);
          }
          if (buffer.includes("event: run_complete")) {
            completed = true;
          }
        }
        setRegenStatus("succeeded");
        // Navigate to the newly-created run so the editor shows the
        // regenerated content. The pushState/popstate plumbing in
        // App.tsx picks this up via the `selectedRunId` state. Fall
        // back to refreshing the current detail if the stream didn't
        // surface a run_id (legacy backend).
        if (newRunId !== null && newRunId !== targetRunId) {
          window.history.pushState({}, "", `/history/${newRunId}`);
          // Trigger the App-level popstate handler so URL → state
          // round-trips uniformly (ensures document.title updates too).
          window.dispatchEvent(new PopStateEvent("popstate"));
        } else {
          try {
            const fresh = await fetchRunDetail(targetRunId);
            setDetail(fresh);
          } catch {
            /* leave stale detail — user can refresh manually */
          }
        }
      } catch (err) {
        setRegenStatus("failed");
        console.error("[regenerate-notes] network error:", err);
      }
    },
    [],
  );

  // Client-side filing-standard filter. The server doesn't filter on this
  // today (per the plan: launch volumes are low and the JSON1 predicate
  // isn't guaranteed across SQLite builds). We still paginate server-side,
  // so on a mostly-MFRS history an MPERS-only filter may show fewer rows
  // than the Load-more counter suggests — that's acceptable for launch
  // but surfaced to the operator via `filterNote` below (peer-review I5).
  const standardFilterActive = !!filters.standard;
  const visibleRuns = standardFilterActive
    ? runs.filter((r) => (r.filing_standard ?? "mfrs") === filters.standard)
    : runs;
  const filterNote = standardFilterActive && runs.length > 0
    ? `Showing ${visibleRuns.length} of ${runs.length} loaded run${runs.length === 1 ? "" : "s"} (${filters.standard!.toUpperCase()}). Load more to scan earlier rows.`
    : null;

  // When a run is selected, the detail page takes over the whole container
  // instead of floating a modal over the list. The list's scroll position
  // is preserved by React retaining the parent's DOM when we toggle the
  // branch — no manual sessionStorage dance needed for the common case.
  if (selectedId != null) {
    return (
      <div style={styles.container}>
        {regenStatus === "running" && (
          <div role="status" style={styles.regenBanner}>
            Regenerating notes — this usually takes 30-60 seconds. You
            can keep this tab open; the editor will refresh automatically
            when it finishes.
          </div>
        )}
        {regenStatus === "failed" && (
          <div role="alert" style={styles.regenBannerError}>
            Regenerate failed. Check the server logs, then try again.
          </div>
        )}
        <RunDetailPage
          detail={detail}
          isLoading={isDetailLoading}
          error={detailError}
          onBack={() => {
            // Always clear selection directly — window.history.back()
            // is *not* a safe shortcut, because history.length > 1
            // only means the tab has prior browser history, not that
            // the previous entry is ours. A user who pastes
            // /history/<id> into a tab they were already using for
            // another site would get sent out of the app by back().
            // Clearing selectedRunId here flows through App's URL
            // effect and pushes /history; browser Back after that
            // still works as expected.
            setSelectedId(null);
          }}
          onDownload={handleDownload}
          onDelete={handleDelete}
          onRegenerateNotes={handleRegenerateNotes}
        />
      </div>
    );
  }

  return (
    <div style={styles.container}>
      <h2 style={styles.heading}>Run history</h2>
      <HistoryFilters value={filters} onChange={setFilters} />
      <HistoryList
        runs={visibleRuns}
        isLoading={isLoading}
        error={error}
        selectedId={selectedId}
        onRunSelected={handleRunSelected}
        onResumeDraft={onResumeDraft}
      />
      {filterNote && (
        // Client-side filter transparency: this footnote explains why
        // Load-more can show "n remaining" while the visible list is
        // shorter — filtered-out rows are still counted in `total`.
        <p role="note" style={styles.filterNote}>{filterNote}</p>
      )}
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
  regenBanner: {
    padding: `${pwc.space.sm}px ${pwc.space.md}px`,
    background: "#fef3c7",
    border: "1px solid #f59e0b",
    borderRadius: pwc.radius.md,
    color: "#78350f",
    fontSize: 13,
  } as React.CSSProperties,
  regenBannerError: {
    padding: `${pwc.space.sm}px ${pwc.space.md}px`,
    background: pwc.errorBg,
    border: `1px solid ${pwc.errorBorder}`,
    borderRadius: pwc.radius.md,
    color: pwc.errorText,
    fontSize: 13,
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
    background: pwc.errorBg,
    color: pwc.errorTextAlt,
    border: `1px solid ${pwc.errorBorder}`,
    borderRadius: pwc.radius.md,
    fontFamily: pwc.fontBody,
    fontSize: 13,
  } as React.CSSProperties,
  filterNote: {
    marginTop: pwc.space.xs,
    marginBottom: 0,
    padding: 0,
    color: pwc.grey500,
    fontFamily: pwc.fontBody,
    fontSize: 12,
    fontStyle: "italic" as const,
  } as React.CSSProperties,
} as const;
