import { useEffect, useRef } from "react";
import { ds } from "../lib/theme";
import type { ToastState } from "../lib/appReducer";
import { CloseIcon } from "./icons";

// ---------------------------------------------------------------------------
// SuccessToast — a small transient banner rendered at the bottom centre
// when a run finishes. Page actions sit top-right and sticky Save bars put
// their button bottom-right, so the centre covers no primary action. The parent owns the toast state; this component only
// renders and schedules auto-dismiss.
//
// Why auto-dismiss at 4s? Long enough for users to read a one-liner, short
// enough that it doesn't stack with follow-up runs. Users can also close it
// manually via the ✕ button, which fires onDismiss immediately.
// ---------------------------------------------------------------------------

export interface SuccessToastProps {
  toast: ToastState | null;
  onDismiss: () => void;
}

const AUTO_DISMISS_MS = 4000;

// Floating toast: the close icon and explicit message carry tone. No accent
// edge is added to the elevated surface.
const TONE_STYLES: Record<
  ToastState["tone"],
  { background: string; border: string; color: string; accent: string }
> = {
  success: {
    background: ds.white,
    border: `1px solid ${ds.grey200}`,
    color: ds.grey800,
    accent: ds.success,
  },
  error: {
    background: ds.white,
    border: `1px solid ${ds.grey200}`,
    color: ds.grey800,
    accent: ds.error,
  },
};

export function SuccessToast({ toast, onDismiss }: SuccessToastProps) {
  // Route the callback through a ref so the auto-dismiss effect does NOT
  // depend on `onDismiss` identity. App.tsx passes a new inline arrow every
  // render; depending on that identity would reset the 4 s countdown on any
  // unrelated App re-render (e.g. an SSE event landing after run_complete)
  // and the toast could hang around forever. This pattern keeps the timer
  // stable while still invoking the *latest* dismiss callback when it fires.
  const dismissRef = useRef(onDismiss);
  useEffect(() => {
    dismissRef.current = onDismiss;
  }, [onDismiss]);

  // Schedule an auto-dismiss whenever a new toast message appears. The
  // timeout is cleared on unmount or when the toast itself changes so we
  // never have a stale timer firing against an old toast.
  useEffect(() => {
    if (!toast) return;
    const id = window.setTimeout(() => {
      dismissRef.current();
    }, AUTO_DISMISS_MS);
    return () => window.clearTimeout(id);
  }, [toast]);

  if (!toast) return null;

  const palette = TONE_STYLES[toast.tone];

  return (
    <div
      role="status"
      aria-live="polite"
      className="app-toast-enter"
      style={{
        ...styles.toast,
        background: palette.background,
        border: palette.border,
        color: palette.color,
      }}
    >
      <span style={styles.message}>{toast.message}</span>
      <button
        type="button"
        onClick={onDismiss}
        aria-label="Dismiss notification"
        data-tooltip="Dismiss notification"
        style={{ ...styles.closeBtn, color: palette.accent }}
      >
        <CloseIcon />
      </button>
    </div>
  );
}

const styles = {
  toast: {
    position: "fixed" as const,
    bottom: ds.space.lg,
    left: 0,
    right: 0,
    margin: "0 auto",
    width: "max-content",
    maxWidth: `calc(100vw - ${ds.space.lg * 2}px)`,
    zIndex: 1000,
    display: "flex",
    alignItems: "center",
    gap: ds.space.md,
    padding: `${ds.space.sm}px ${ds.space.md}px`,
    borderRadius: ds.radius.md,
    boxShadow: ds.shadow.elevated,
    fontFamily: ds.fontBody,
    fontSize: 14,
    minWidth: 260,
  } as React.CSSProperties,
  message: {
    flex: 1,
    fontWeight: 500,
  } as React.CSSProperties,
  closeBtn: {
    background: "none",
    border: "none",
    cursor: "pointer",
    fontSize: 14,
    fontWeight: ds.weight.medium,
    padding: 0,
    lineHeight: 1,
  } as React.CSSProperties,
};
