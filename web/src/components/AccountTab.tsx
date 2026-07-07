import { useState, useCallback } from "react";
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
  input: {
    width: "100%",
    padding: `${pwc.space.sm}px ${pwc.space.md}px`,
    border: `1px solid ${pwc.grey200}`,
    borderRadius: pwc.radius.md,
    fontFamily: pwc.fontBody,
    fontSize: 14,
    color: pwc.grey900,
    outline: "none",
    boxSizing: "border-box" as const,
  } as React.CSSProperties,
  helperText: {
    fontFamily: pwc.fontBody,
    fontSize: 13,
    color: pwc.grey500,
    marginTop: pwc.space.xs,
  } as React.CSSProperties,
  errorText: {
    fontFamily: pwc.fontBody,
    fontSize: 13,
    color: pwc.error,
    marginTop: pwc.space.sm,
  } as React.CSSProperties,
  successText: {
    fontFamily: pwc.fontBody,
    fontSize: 13,
    color: pwc.success,
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
      <div style={styles.fieldGroup}>
        <label style={styles.label}>Current password</label>
        <input
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
          type="password"
          value={next}
          onChange={(e) => setNext(e.target.value)}
          autoComplete="new-password"
          aria-label="New password"
          style={styles.input}
        />
        <p style={styles.helperText}>At least {MIN_LEN} characters.</p>
      </div>

      <div style={styles.fieldGroup}>
        <label style={styles.label}>Confirm new password</label>
        <input
          type="password"
          value={confirm}
          onChange={(e) => setConfirm(e.target.value)}
          autoComplete="new-password"
          aria-label="Confirm new password"
          style={styles.input}
        />
      </div>

      {error && <p style={styles.errorText}>{error}</p>}
      {success && <p style={styles.successText}>Password changed.</p>}

      <div style={styles.actions}>
        <button
          onClick={handleSave}
          disabled={saving}
          className={uiClass.btnPrimary}
          style={styles.saveButton}
        >
          {saving ? "Saving..." : "Change password"}
        </button>
      </div>
    </div>
  );
}
