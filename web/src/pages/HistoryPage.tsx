import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, userMessage } from "../lib/errors";
import { pwc, tokens } from "../lib/theme";
import { ui, uiClass } from "../lib/uiStyles";
import { PageHeader } from "../components/PageHeader";
import { HistoryFilters } from "../components/HistoryFilters";
import { HistoryList } from "../components/HistoryList";
import { RunDetailPage } from "../components/RunDetailPage";
import type { RunTabKey } from "../components/RunDetailView";
import { fetchRuns, fetchRunDetail, deleteRun, forceAbortRun, restartRun } from "../lib/api";
import type { RunDetailJson, RunSummaryJson, RunsFilterParams } from "../lib/types";
import { TERMS } from "../lib/vocabulary";

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
const RUN_DETAIL_POLL_MS = 2_000;
const RUN_DETAIL_MAX_RETRIES = 5;
const RUN_DETAIL_MAX_RETRY_MS = 30_000;

function isRetryableDetailError(error: unknown): boolean {
  if (!(error instanceof ApiError) || error.status == null) return true;
  return error.status === 408 || error.status === 429 || error.status >= 500;
}

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
  /** Gates the run-detail "View Concepts" link on canonical mode
   *  (peer-review F6). Forwarded straight to RunDetailPage. */
  canonicalEnabled?: boolean;
  /** Initial run-detail tab. The `/concepts/{id}` alias passes "values" so
   *  the deep link lands on the values review. Forwarded to RunDetailPage. */
  initialRunTab?: RunTabKey;
}

export function HistoryPage({ selectedId: selectedIdProp, onSelectRun, onResumeDraft, canonicalEnabled = false, initialRunTab }: HistoryPageProps = {}) {
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
        const msg = userMessage(err);
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
      const msg = userMessage(err);
      // Dedicated pagination-error state — the page-level `error` would
      // blank the already-loaded rows via HistoryList's error early-return.
      setLoadMoreError(msg);
    } finally {
      setIsLoadingMore(false);
    }
  }, [runs.length, filters]);

  // Fetch detail whenever `selectedId` changes. Running runs self-schedule one
  // follow-up at a time so a dropped live stream can resume from durable state
  // without overlapping requests or launching duplicate work.
  useEffect(() => {
    if (selectedId == null) {
      setDetail(null);
      setDetailError(null);
      return;
    }
    let cancelled = false;
    let pollTimer: ReturnType<typeof setTimeout> | null = null;
    let consecutiveFailures = 0;
    setIsDetailLoading(true);
    setDetailError(null);

    const schedulePoll = (delayMs = RUN_DETAIL_POLL_MS) => {
      if (pollTimer !== null) clearTimeout(pollTimer);
      pollTimer = setTimeout(() => {
        pollTimer = null;
        void loadDetail(false);
      }, delayMs);
    };

    const loadDetail = async (initial: boolean) => {
      try {
        const next = await fetchRunDetail(selectedId);
        if (cancelled) return;
        consecutiveFailures = 0;
        setDetail(next);
        setDetailError(null);
        if (next.status === "running") schedulePoll();
      } catch (err: unknown) {
        if (cancelled) return;
        setDetailError(userMessage(err));
        if (initial) setDetail(null);
        if (isRetryableDetailError(err) && consecutiveFailures < RUN_DETAIL_MAX_RETRIES) {
          consecutiveFailures += 1;
          const retryDelay = Math.min(
            RUN_DETAIL_POLL_MS * 2 ** (consecutiveFailures - 1),
            RUN_DETAIL_MAX_RETRY_MS,
          );
          schedulePoll(retryDelay);
        }
      } finally {
        if (!cancelled && initial) setIsDetailLoading(false);
      }
    };

    void loadDetail(true);
    return () => {
      cancelled = true;
      if (pollTimer !== null) clearTimeout(pollTimer);
    };
  }, [selectedId]);

  const handleRunSelected = useCallback((id: number) => {
    setSelectedId(id);
  }, [setSelectedId]);

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
      const msg = userMessage(err);
      setDetailError(msg);
    }
  }, [setSelectedId]);

  // Force-abort a wedged `running` run opened from History (UX-QA #2). The
  // backend flips a dead row to `aborted`; we then reload both the list and the
  // open detail so Delete/Download become usable without a page refresh.
  const handleForceAbort = useCallback(async (runId: number) => {
    try {
      await forceAbortRun(runId);
      setRefetchKey((k) => k + 1);
      const fresh = await fetchRunDetail(runId);
      setDetail(fresh);
    } catch (err) {
      setDetailError(userMessage(err));
    }
  }, []);

  const handleRestart = useCallback(async (runId: number) => {
    try {
      const draft = await restartRun(runId);
      onResumeDraft?.(draft.run_id);
    } catch (err) {
      setDetailError(userMessage(err));
    }
  }, [onResumeDraft]);

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

  // Split drafts into their own collapsed section so they don't bury the real
  // runs (E2). When the user explicitly filters by status, keep the single
  // list (the filter already scopes what they see).
  const statusFilterActive = !!filters.status;
  const draftRuns = statusFilterActive
    ? []
    : visibleRuns.filter((r) => r.status === "draft");
  const mainRuns = statusFilterActive
    ? visibleRuns
    : visibleRuns.filter((r) => r.status !== "draft");

  // When a run is selected, the detail page takes over the whole container
  // instead of floating a modal over the list. The list's scroll position
  // is preserved by React retaining the parent's DOM when we toggle the
  // branch — no manual sessionStorage dance needed for the common case.
  if (selectedId != null) {
    return (
      <div style={styles.detailContainer}>
        <RunDetailPage
          detail={detail}
          isLoading={isDetailLoading}
          error={detailError}
          canonicalEnabled={canonicalEnabled}
          initialTab={initialRunTab}
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
          onDelete={handleDelete}
          onResumeDraft={onResumeDraft}
          onForceAbort={handleForceAbort}
          onRestart={handleRestart}
        />
      </div>
    );
  }

  return (
    <div style={styles.container}>
      <PageHeader title={TERMS.runs} />
      <HistoryFilters value={filters} onChange={setFilters} />
      {!isLoading && !error && (
        // A plain result count so the list isn't an unbounded wall of rows
        // with no sense of scale (E2).
        <p role="status" style={styles.resultCount} data-testid="history-result-count">
          {total === 0
            ? "No runs yet"
            : runs.length < total
            ? `Showing ${runs.length} of ${total} runs`
            : `${total} run${total === 1 ? "" : "s"}`}
        </p>
      )}
      <HistoryList
        runs={mainRuns}
        isLoading={isLoading}
        error={error}
        selectedId={selectedId}
        onRunSelected={handleRunSelected}
        onResumeDraft={onResumeDraft}
      />
      {/* Drafts sit in their own collapsed section so unstarted uploads don't
          bury the real runs (E2). Skipped when the user explicitly filters by
          status — then everything shows in the main list above. */}
      {draftRuns.length > 0 && (
        <details style={styles.draftsSection} data-testid="drafts-section">
          <summary style={styles.draftsSummary}>
            Drafts — not started ({draftRuns.length})
          </summary>
          <HistoryList
            runs={draftRuns}
            selectedId={selectedId}
            onRunSelected={handleRunSelected}
            onResumeDraft={onResumeDraft}
          />
        </details>
      )}
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
          className={uiClass.btnGhost}
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
  // Wide-list mode: the Runs table earns 1440px; the selected run detail
  // (detailContainer) stays unconstrained workspace width.
  container: {
    ...ui.pageWide,
    display: "flex",
    flexDirection: "column" as const,
    gap: pwc.space.lg,
  } as React.CSSProperties,
  detailContainer: {
    display: "flex",
    flexDirection: "column" as const,
    gap: pwc.space.lg,
  } as React.CSSProperties,
  loadMoreBtn: {
    ...ui.buttonQuiet,
    marginTop: 0,
    width: "100%",
    color: tokens.color.action.primary,
  } as React.CSSProperties,
  loadMoreError: {
    marginTop: pwc.space.sm,
    padding: `${pwc.space.sm}px ${pwc.space.md}px`,
    background: pwc.orange50,
    color: pwc.grey800,
    border: "none",
    borderRadius: pwc.radius.md,
    fontFamily: pwc.fontBody,
    fontSize: 13,
  } as React.CSSProperties,
  resultCount: {
    margin: `0 0 -${pwc.space.sm}px`,
    color: pwc.grey700,
    fontFamily: pwc.fontBody,
    fontSize: 13,
  } as React.CSSProperties,
  draftsSection: {
    marginTop: 0,
  } as React.CSSProperties,
  draftsSummary: {
    cursor: "pointer",
    padding: `${pwc.space.sm}px 0`,
    color: pwc.grey700,
    fontFamily: pwc.fontBody,
    fontSize: 13,
    fontWeight: pwc.weight.medium,
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
