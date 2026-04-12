import { useEffect } from "react";
import { pwc } from "../lib/theme";
import { RunDetailView } from "./RunDetailView";
import type { RunDetailJson } from "../lib/types";

// ---------------------------------------------------------------------------
// RunDetailModal — overlay that hosts RunDetailView.
//
// The History page used to render the detail inline next to the list, but
// two flex panes couldn't both fit in the 912px main content area, and the
// 6-column cross-check table got clipped in the narrow pane. A modal gives
// the list back its full width AND gives the detail ~900px to render all
// columns without truncation.
//
// The shell follows the same conventions as SettingsModal: fixed overlay,
// Escape to close, click-outside to close, X button in the header. Keeps
// all four affordances so a user doesn't have to guess how to dismiss it.
// ---------------------------------------------------------------------------

export interface RunDetailModalProps {
  isOpen: boolean;
  onClose: () => void;
  detail: RunDetailJson | null;
  isLoading: boolean;
  error: string | null;
  onDownload: (runId: number) => void;
  onDelete: (runId: number) => void;
}

export function RunDetailModal({
  isOpen,
  onClose,
  detail,
  isLoading,
  error,
  onDownload,
  onDelete,
}: RunDetailModalProps) {
  useEffect(() => {
    if (!isOpen) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [isOpen, onClose]);

  if (!isOpen) return null;

  return (
    <div
      style={styles.overlay}
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
      role="presentation"
    >
      <div
        style={styles.modal}
        role="dialog"
        aria-modal="true"
        aria-label="Run details"
      >
        <button
          type="button"
          onClick={onClose}
          style={styles.closeButton}
          aria-label="Close run details"
        >
          ×
        </button>
        <div style={styles.scrollArea}>
          {isLoading && <p style={styles.state}>Loading run details…</p>}
          {error && !isLoading && (
            <div style={styles.errorBanner}>{error}</div>
          )}
          {detail && !isLoading && !error && (
            // onDelete is passed through directly — DO NOT close the modal
            // here. The parent's delete handler is async; closing eagerly
            // unmounts the modal before the await resolves, which silently
            // hides any failure error the parent tries to surface via the
            // `error` prop. The parent closes on success by clearing
            // selectedId, and leaves the modal open on failure so the
            // error banner becomes visible.
            <RunDetailView
              detail={detail}
              onDownload={onDownload}
              onDelete={onDelete}
            />
          )}
        </div>
      </div>
    </div>
  );
}

const styles = {
  overlay: {
    position: "fixed" as const,
    inset: 0,
    zIndex: 50,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    background: "rgba(0,0,0,0.5)",
    padding: pwc.space.xl,
  } as React.CSSProperties,
  modal: {
    position: "relative" as const,
    background: pwc.white,
    borderRadius: pwc.radius.lg,
    boxShadow: pwc.shadow.modal,
    width: "100%",
    maxWidth: 920,
    maxHeight: "90vh",
    display: "flex",
    flexDirection: "column" as const,
    overflow: "hidden",
  } as React.CSSProperties,
  closeButton: {
    position: "absolute" as const,
    top: pwc.space.md,
    right: pwc.space.md,
    width: 32,
    height: 32,
    border: "none",
    background: "transparent",
    color: pwc.grey700,
    fontSize: 24,
    lineHeight: 1,
    cursor: "pointer",
    borderRadius: pwc.radius.sm,
    zIndex: 1,
  } as React.CSSProperties,
  scrollArea: {
    overflowY: "auto" as const,
    padding: pwc.space.lg,
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
    background: "#FEF2F2",
    color: "#B91C1C",
    borderRadius: pwc.radius.md,
    border: "1px solid #FECACA",
    fontFamily: pwc.fontBody,
    fontSize: 14,
  } as React.CSSProperties,
} as const;
