import { pwc } from "../lib/theme";
import { ui, uiClass } from "../lib/uiStyles";
import { RunDetailView } from "./RunDetailView";
import type { RunDetailViewProps } from "./RunDetailView";
import type { RunDetailJson } from "../lib/types";

// ---------------------------------------------------------------------------
// RunDetailPage — full-page run detail, routed at /history/<id>. Replaces
// the earlier RunDetailModal so reviewers get the whole main-content width
// for the Notes Review editor + cross-check table instead of the clipped
// 920px modal. The parent (HistoryPage) decides when to mount it based on
// the app-level selectedRunId state.
//
// The chrome here is intentionally minimal: a top bar with a Back button
// on the left and the embedded RunDetailView body (which carries the run
// number in its kicker — it is deliberately not repeated up here). No overlay, no Escape handler — a page dismissal is
// the Back button (which prefers history.back() so browser Back behaves
// naturally and the list scroll position is preserved by React + the
// browser).
// ---------------------------------------------------------------------------

export interface RunDetailPageProps {
  detail: RunDetailJson | null;
  isLoading: boolean;
  error: string | null;
  /** Called when the user clicks Back. Parent is responsible for clearing
   *  selectedRunId (and, on modern browsers, calling history.back() so the
   *  list view restores its scroll position). */
  onBack: () => void;
  onDownload: (runId: number) => void;
  onDelete: (runId: number) => void;
  /** Return an unstarted run to its editable extraction setup. */
  onResumeDraft?: (runId: number) => void;
  /** Forwarded to RunDetailView — rescue a run wedged in `running` status
   *  (UX-QA #2). Optional; when absent the abort control is not shown. */
  onForceAbort?: (runId: number) => void;
  /** Forwarded to the embedded RunDetailView so Notes Review can wire its
   *  Regenerate button. Optional for callers that don't use the notes
   *  subsystem. */
  onRegenerateNotes?: (runId: number) => void;
  /** Forwarded to RunDetailView to gate the "View Concepts" link on
   *  canonical mode (peer-review F6). */
  canonicalEnabled?: boolean;
  /** Forwarded to RunDetailView — initial tab (the `/concepts/{id}` alias
   *  passes "values"). */
  initialTab?: RunDetailViewProps["initialTab"];
}

export function RunDetailPage({
  detail,
  isLoading,
  error,
  onBack,
  onDownload,
  onDelete,
  onResumeDraft,
  onForceAbort,
  onRegenerateNotes,
  canonicalEnabled = false,
  initialTab,
}: RunDetailPageProps) {
  return (
    <div style={styles.root}>
      <header style={styles.topBar}>
        <button
          type="button"
          onClick={onBack}
          className={uiClass.btnGhost}
          style={styles.backButton}
          aria-label="Back to runs"
        >
          {/* Unicode left arrow — keeps the button self-contained without
              pulling in an SVG icon and matches the inline-style rule in
              CLAUDE.md gotcha #7. */}
          ← Back to runs
        </button>
        {/* The run number is NOT repeated here — the detail view's kicker
            ("RUN {id}") already names the run, and two copies of the same
            label an inch apart read as clutter (run-168 design critique). */}
      </header>

      {isLoading && <p style={styles.state}>Loading run details…</p>}

      {error && !isLoading && (
        <div style={styles.errorBanner} role="alert">{error}</div>
      )}

      {detail && !isLoading && !error && (
        <RunDetailView
          detail={detail}
          onDownload={onDownload}
          onDelete={onDelete}
          onResumeDraft={onResumeDraft}
          onForceAbort={onForceAbort}
          onRegenerateNotes={onRegenerateNotes}
          canonicalEnabled={canonicalEnabled}
          initialTab={initialTab}
        />
      )}
    </div>
  );
}

const styles = {
  root: {
    display: "flex",
    flexDirection: "column" as const,
    gap: pwc.space.lg,
  } as React.CSSProperties,
  topBar: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    gap: pwc.space.md,
    paddingBottom: pwc.space.sm,
    borderBottom: `1px solid ${pwc.grey200}`,
  } as React.CSSProperties,
  backButton: {
    ...ui.buttonGhost,
    ...ui.buttonSm,
  } as React.CSSProperties,
  state: {
    textAlign: "center" as const,
    color: pwc.grey500,
    fontFamily: pwc.fontBody,
    fontSize: 14,
    padding: pwc.space.xl,
    margin: 0,
  } as React.CSSProperties,
  errorBanner: {
    padding: pwc.space.lg,
    background: pwc.white,
    color: pwc.grey800,
    borderRadius: pwc.radius.md,
    border: `1px solid ${pwc.grey200}`,
    borderLeft: `3px solid ${pwc.error}`,
    fontFamily: pwc.fontBody,
    fontSize: 14,
  } as React.CSSProperties,
} as const;
