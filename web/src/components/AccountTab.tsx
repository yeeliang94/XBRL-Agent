import { useState, useCallback, useRef } from "react";
import { userMessage } from "../lib/errors";
import { pwc } from "../lib/theme";
import { ui, uiClass } from "../lib/uiStyles";
import { changePassword } from "../lib/api";

// ---------------------------------------------------------------------------
// AccountTab — self-service "change my own password" (gotcha #7: inline styles).
// Requires the current password (re-auth); the server enforces the 8-char
// minimum, and we mirror it client-side for a faster error. The new password
// must be typed twice to catch typos before it's committed.
// ---------------------------------------------------------------------------

const MIN_LEN = 8;

const styles = {
  fieldGroup: { marginBottom: pwc.space.lg } as React.CSSProperties,
  label: {
    fontFamily: pwc.fontHeading,
    fontWeight: 500,
    fontSize: 14,
    color: pwc.grey700,
    display: "block",
    marginBottom: pwc.space.xs,
  } as React.CSSProperties,
  // Adopt the shared input primitive (Phase 6 layout normalization: 11/16
  // padding, 44px min-height, grey300 border) instead of the off-spec local one.
  input: {
    ...ui.input,
    width: "100%",
    boxSizing: "border-box" as const,
  } as React.CSSProperties,
  helperText: {
    fontFamily: pwc.fontBody,
    fontSize: 13,
    color: pwc.grey700,
    marginTop: pwc.space.xs,
  } as React.CSSProperties,
  errorText: {
    fontFamily: pwc.fontBody,
    fontSize: 13,
    color: pwc.errorText,
    marginTop: pwc.space.sm,
  } as React.CSSProperties,
  successText: {
    fontFamily: pwc.fontBody,
    fontSize: 13,
    color: pwc.successText,
    marginTop: pwc.space.sm,
  } as React.CSSProperties,
  actions: {
    display: "flex",
    alignItems: "center",
    justifyContent: "flex-end",
    gap: pwc.space.md,
    marginTop: pwc.space.xl,
    paddingTop: pwc.space.lg,
    borderTop: `1px solid ${pwc.grey200}`,
  } as React.CSSProperties,
  saveButton: { ...ui.buttonPrimary, ...ui.buttonSm } as React.CSSProperties,
};

export function AccountTab() {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);
  const nextRef = useRef<HTMLInputElement>(null);

  // Client-side gate — mirrors the server so the user gets an instant error,
  // but the server is the real authority (it re-checks both).
  const clientError = (): string | null => {
    if (next.length < MIN_LEN) return `New password must be at least ${MIN_LEN} characters.`;
    if (next !== confirm) return "New password and confirmation do not match.";
    return null;
  };

  const handleSave = useCallback(async () => {
    setError(null);
    setSuccess(false);
    const ce = clientError();
    if (ce) {
      setError(ce);
      nextRef.current?.focus();
      return;
    }
    setSaving(true);
    try {
      await changePassword(current, next);
      setSuccess(true);
      setCurrent("");
      setNext("");
      setConfirm("");
    } catch (e) {
      setError(userMessage(e));
    } finally {
      setSaving(false);
    }
    // clientError reads current state; deps cover the inputs it touches.
  }, [current, next, confirm]);

  return (
    <div>
      <p style={styles.helperText}>
        Change the password for your signed-in account. Your current password is required.
      </p>
      <div style={styles.fieldGroup}>
        <label style={styles.label}>Current password</label>
        <input
          id="account-current-password"
          type="password"
          value={current}
          onChange={(e) => setCurrent(e.target.value)}
          autoComplete="current-password"
          aria-label="Current password"
          style={styles.input}
        />
      </div>

      <div style={styles.fieldGroup}>
        <label style={styles.label}>New password</label>
        <input
          id="account-new-password"
          ref={nextRef}
          type="password"
          value={next}
          onChange={(e) => setNext(e.target.value)}
          autoComplete="new-password"
          aria-label="New password"
          aria-describedby="account-password-requirements account-password-error"
          style={styles.input}
        />
        <p id="account-password-requirements" style={styles.helperText}>At least {MIN_LEN} characters.</p>
      </div>

      <div style={styles.fieldGroup}>
        <label style={styles.label}>Confirm new password</label>
        <input
          id="account-confirm-password"
          type="password"
          value={confirm}
          onChange={(e) => setConfirm(e.target.value)}
          autoComplete="new-password"
          aria-label="Confirm new password"
          style={styles.input}
        />
      </div>

      {error && <p id="account-password-error" style={styles.errorText} role="alert">{error}</p>}
      {success && <p style={styles.successText} role="status" aria-live="polite">Password changed.</p>}

      <div style={styles.actions}>
        <button
          onClick={handleSave}
          disabled={saving}
          className={uiClass.btnPrimary}
          style={styles.saveButton}
        >
          {saving ? "Changing…" : "Change password"}
        </button>
      </div>
    </div>
  );
}
