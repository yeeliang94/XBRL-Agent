import { useEffect, useRef } from "react";
import { pwc } from "../lib/theme";
import type { ToastState } from "../App";

// ---------------------------------------------------------------------------
// SuccessToast — a small transient banner rendered in the top-right corner
// when a run finishes. The parent owns the toast state; this component only
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

const TONE_STYLES: Record<ToastState["tone"], { background: string; border: string; color: string }> = {
  success: {
    background: "#F0FDF4",
    border: `1px solid #BBF7D0`,
    color: "#166534",
  },
  error: {
    background: "#FEF2F2",
    border: `1px solid #FECACA`,
    color: "#991B1B",
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
        style={{ ...styles.closeBtn, color: palette.color }}
      >
        &#10005;
      </button>
    </div>
  );
}

const styles = {
  toast: {
    position: "fixed" as const,
    top: pwc.space.lg,
    right: pwc.space.lg,
    zIndex: 1000,
    display: "flex",
    alignItems: "center",
    gap: pwc.space.md,
    padding: `${pwc.space.sm}px ${pwc.space.md}px`,
    borderRadius: pwc.radius.md,
    boxShadow: pwc.shadow.card,
    fontFamily: pwc.fontBody,
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
    fontWeight: 700,
    padding: 0,
    lineHeight: 1,
  } as React.CSSProperties,
};
