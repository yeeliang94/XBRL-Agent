import { pwc } from "../lib/theme";
import { RunDetailView } from "./RunDetailView";
import type { RunDetailJson } from "../lib/types";

// ---------------------------------------------------------------------------
// RunDetailPage — full-page run detail, routed at /history/<id>. Replaces
// the earlier RunDetailModal so reviewers get the whole main-content width
// for the Notes Review editor + cross-check table instead of the clipped
// 920px modal. The parent (HistoryPage) decides when to mount it based on
// the app-level selectedRunId state.
//
// The chrome here is intentionally minimal: a top bar with a Back button
// on the left, the run title on the right (if loaded), and the embedded
// RunDetailView body. No overlay, no Escape handler — a page dismissal is
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
  /** Forwarded to the embedded RunDetailView so Notes Review can wire its
   *  Regenerate button. Optional for callers that don't use the notes
   *  subsystem. */
  onRegenerateNotes?: (runId: number) => void;
}

export function RunDetailPage({
  detail,
  isLoading,
  error,
  onBack,
  onDownload,
  onDelete,
  onRegenerateNotes,
}: RunDetailPageProps) {
  return (
    <div style={styles.root}>
      <header style={styles.topBar}>
        <button
          type="button"
          onClick={onBack}
          style={styles.backButton}
          aria-label="Back to history"
        >
          {/* Unicode left arrow — keeps the button self-contained without
              pulling in an SVG icon and matches the inline-style rule in
              CLAUDE.md gotcha #7. */}
          ← Back to history
        </button>
        {detail && (
          <span style={styles.runTitle} aria-live="polite">
            Run #{detail.id}
          </span>
        )}
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
          onRegenerateNotes={onRegenerateNotes}
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
    padding: `${pwc.space.xs}px ${pwc.space.md}px`,
    fontFamily: pwc.fontHeading,
    fontSize: 13,
    fontWeight: 600,
    color: pwc.orange500,
    background: pwc.white,
    border: `1px solid ${pwc.orange500}`,
    borderRadius: pwc.radius.sm,
    cursor: "pointer",
  } as React.CSSProperties,
  runTitle: {
    fontFamily: pwc.fontMono,
    fontSize: 13,
    color: pwc.grey700,
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
    background: pwc.errorBg,
    color: pwc.errorTextAlt,
    borderRadius: pwc.radius.md,
    border: `1px solid ${pwc.errorBorder}`,
    fontFamily: pwc.fontBody,
    fontSize: 14,
  } as React.CSSProperties,
} as const;
