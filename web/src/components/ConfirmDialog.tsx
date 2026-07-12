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

// Shared dialog/scrim primitives (design-system Elevation: dialogs are the
// modal-shadow exception). Animations ride the motion tokens; reduced-motion
// zeroes them globally.
const styles = {
  overlay: {
    ...ui.scrim,
    // Backdrop fades in alongside the dialog scaling in.
    animation: `dialog-in ${pwc.motion.duration.fast} ${pwc.motion.easing}`,
  } as React.CSSProperties,
  modal: {
    ...ui.dialog,
    maxWidth: 440,
    // Scale 97%→100% + fade (motion tokens; reduced-motion zeroes it globally).
    animation: `dialog-in ${pwc.motion.duration.fast} ${pwc.motion.easing}`,
  } as React.CSSProperties,
  heading: {
    ...ui.dialogTitle,
    marginBottom: pwc.space.md,
  } as React.CSSProperties,
  body: {
    ...ui.bodyText,
    margin: 0,
    marginBottom: pwc.space.xl,
  } as React.CSSProperties,
  actions: {
    ...ui.dialogActionBar,
    marginTop: 0,
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
  const cancelRef = useRef<HTMLButtonElement>(null);
  // Keep the latest onCancel/busy in refs so the Escape listener never has to
  // list them as effect deps — otherwise a new inline onCancel identity on
  // every parent re-render would re-run the focus effect and steal focus back
  // to Confirm (unusable during a streaming run that re-renders continuously).
  const onCancelRef = useRef(onCancel);
  const busyRef = useRef(busy);
  onCancelRef.current = onCancel;
  busyRef.current = busy;

  // Focus the confirm button on open (keyboard users can act without the
  // mouse) and restore focus to the trigger element on close. Keyed on
  // [isOpen] ONLY so re-renders while open never re-steal focus.
  useEffect(() => {
    if (!isOpen) return;
    const previouslyFocused = document.activeElement as HTMLElement | null;
    confirmRef.current?.focus();
    return () => previouslyFocused?.focus?.();
  }, [isOpen]);

  // Escape cancels (while open and not mid-action). Separate effect reading
  // refs, so it registers once per open and never re-fires the focus effect.
  useEffect(() => {
    if (!isOpen) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !busyRef.current) onCancelRef.current();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [isOpen]);

  if (!isOpen) return null;

  // Trap Tab between the two buttons so focus can't escape an aria-modal
  // dialog to the background. Only the confirm button is disabled while busy;
  // Cancel stays reachable, so the trap targets whichever buttons are live.
  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key !== "Tab") return;
    const focusables = [cancelRef.current, confirmRef.current].filter(
      (b): b is HTMLButtonElement => !!b && !b.disabled,
    );
    if (focusables.length === 0) return;
    const first = focusables[0];
    const last = focusables[focusables.length - 1];
    const active = document.activeElement;
    if (e.shiftKey && active === first) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && active === last) {
      e.preventDefault();
      first.focus();
    }
  };

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
      aria-describedby="confirm-dialog-message"
      onKeyDown={onKeyDown}
    >
      <div style={styles.modal}>
        <h2 style={styles.heading}>{title}</h2>
        <p id="confirm-dialog-message" style={styles.body}>{message}</p>
        <div style={styles.actions}>
          <button
            ref={cancelRef}
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
