import { useEffect, useRef } from "react";
import { pwc } from "../lib/theme";
import { ui, uiClass } from "../lib/uiStyles";

// ---------------------------------------------------------------------------
// ConfirmDialog — one shared confirm modal replacing the scattered
// window.confirm() calls (Phase 5/6). Every destructive action confirms the
// same way: a headline, a plain sentence saying WHAT WILL BE LOST, and a
// Cancel / Confirm pair. Matches the canonical modal geometry (SettingsModal):
// centred scrim, 24px padding, radius lg, modal shadow, zIndex 50.
//
// Presentation only — the caller owns the action and its busy state. Passing
// `busy` swaps the confirm label for a progress label and blocks double-clicks.
// ---------------------------------------------------------------------------

interface Props {
  isOpen: boolean;
  /** Short question, e.g. "Delete this run?" */
  title: string;
  /** Plain sentence describing the consequence — what the user loses. */
  message: React.ReactNode;
  /** Confirm-button label. Defaults to "Confirm". */
  confirmLabel?: string;
  /** Label shown on the confirm button while the action runs. */
  busyLabel?: string;
  cancelLabel?: string;
  /** Destructive actions colour the confirm button red (default true). */
  danger?: boolean;
  /** True while the confirmed action is in flight. */
  busy?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

const styles = {
  overlay: {
    position: "fixed" as const,
    inset: 0,
    zIndex: 50,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    padding: pwc.space.lg,
    background: "rgba(0,0,0,0.4)",
    // Backdrop fades in alongside the dialog scaling in.
    animation: `dialog-in ${pwc.motion.duration.fast} ${pwc.motion.easing}`,
  } as React.CSSProperties,
  modal: {
    background: pwc.white,
    borderRadius: pwc.radius.lg,
    boxShadow: pwc.shadow.modal,
    width: "100%",
    maxWidth: 440,
    padding: pwc.space.xl,
    // Scale 97%→100% + fade (motion tokens; reduced-motion zeroes it globally).
    animation: `dialog-in ${pwc.motion.duration.fast} ${pwc.motion.easing}`,
  } as React.CSSProperties,
  heading: {
    fontFamily: pwc.fontHeading,
    fontWeight: pwc.weight.semibold,
    fontSize: 18,
    color: pwc.grey900,
    margin: 0,
    marginBottom: pwc.space.md,
  } as React.CSSProperties,
  body: {
    fontFamily: pwc.fontBody,
    fontSize: 15,
    lineHeight: 1.55,
    color: pwc.grey800,
    margin: 0,
    marginBottom: pwc.space.xl,
  } as React.CSSProperties,
  actions: {
    display: "flex",
    justifyContent: "flex-end",
    gap: pwc.space.sm,
  } as React.CSSProperties,
};

export function ConfirmDialog({
  isOpen,
  title,
  message,
  confirmLabel = "Confirm",
  busyLabel,
  cancelLabel = "Cancel",
  danger = true,
  busy = false,
  onConfirm,
  onCancel,
}: Props) {
  const confirmRef = useRef<HTMLButtonElement>(null);

  // Escape cancels (while open and not mid-action). Focus the confirm button
  // on open so a keyboard user can act without reaching for the mouse.
  useEffect(() => {
    if (!isOpen) return;
    confirmRef.current?.focus();
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !busy) onCancel();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [isOpen, busy, onCancel]);

  if (!isOpen) return null;

  return (
    <div
      style={styles.overlay}
      onClick={(e) => {
        // Click-outside cancels, but never while the action is running.
        if (e.target === e.currentTarget && !busy) onCancel();
      }}
      role="dialog"
      aria-modal="true"
      aria-label={title}
    >
      <div style={styles.modal}>
        <h2 style={styles.heading}>{title}</h2>
        <p style={styles.body}>{message}</p>
        <div style={styles.actions}>
          <button
            type="button"
            className={uiClass.btnSecondary}
            style={{ ...ui.buttonSecondary, ...ui.buttonSm }}
            onClick={onCancel}
            disabled={busy}
          >
            {cancelLabel}
          </button>
          <button
            ref={confirmRef}
            type="button"
            className={danger ? uiClass.btnDanger : uiClass.btnPrimary}
            style={{ ...(danger ? ui.buttonDanger : ui.buttonPrimary), ...ui.buttonSm }}
            onClick={onConfirm}
            disabled={busy}
          >
            {busy && busyLabel ? busyLabel : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
