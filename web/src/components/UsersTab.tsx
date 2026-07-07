import { useState, useEffect, useCallback } from "react";
import { userMessage } from "../lib/errors";
import { pwc } from "../lib/theme";
import { ui, uiClass } from "../lib/uiStyles";
import {
  adminListUsers,
  adminAddUser,
  adminSetDisabled,
  adminResetPassword,
  adminSetAdmin,
  type AdminUser,
} from "../lib/api";

// ---------------------------------------------------------------------------
// UsersTab — admin user management (gotcha #7: inline styles). Lists accounts
// and exposes the same operations as the CLI: add, disable/enable, reset
// password, promote/demote. Every action is also enforced server-side; this UI
// is gated by the page (Users tab only shows for admins) but never relies on
// that for safety. The 409 last-admin-guard error is surfaced inline.
// ---------------------------------------------------------------------------

const MIN_LEN = 8;

const styles = {
  error: {
    fontFamily: pwc.fontBody,
    fontSize: 13,
    color: pwc.error,
    marginBottom: pwc.space.md,
  } as React.CSSProperties,
  table: {
    width: "100%",
    borderCollapse: "collapse" as const,
    fontFamily: pwc.fontBody,
    fontSize: 13,
  } as React.CSSProperties,
  th: {
    textAlign: "left" as const,
    padding: pwc.space.sm,
    borderBottom: `1px solid ${pwc.grey200}`,
    color: pwc.grey500,
    fontWeight: 500,
  } as React.CSSProperties,
  td: {
    padding: pwc.space.sm,
    borderBottom: `1px solid ${pwc.grey100}`,
    color: pwc.grey900,
    verticalAlign: "middle" as const,
  } as React.CSSProperties,
  actionBtn: {
    ...ui.buttonSecondary,
    ...ui.buttonSm,
    marginRight: pwc.space.xs,
  } as React.CSSProperties,
  addForm: {
    marginTop: pwc.space.xl,
    paddingTop: pwc.space.lg,
    borderTop: `1px solid ${pwc.grey200}`,
  } as React.CSSProperties,
  addRow: {
    display: "flex",
    gap: pwc.space.sm,
    alignItems: "center",
    flexWrap: "wrap" as const,
  } as React.CSSProperties,
  input: {
    padding: `${pwc.space.sm}px ${pwc.space.md}px`,
    border: `1px solid ${pwc.grey200}`,
    borderRadius: pwc.radius.md,
    fontFamily: pwc.fontBody,
    fontSize: 13,
    color: pwc.grey900,
    outline: "none",
  } as React.CSSProperties,
  heading: {
    fontFamily: pwc.fontHeading,
    fontWeight: 500,
    fontSize: 14,
    color: pwc.grey700,
    marginBottom: pwc.space.sm,
  } as React.CSSProperties,
  saveButton: { ...ui.buttonPrimary, ...ui.buttonSm } as React.CSSProperties,
  checkboxLabel: {
    display: "flex",
    alignItems: "center",
    gap: pwc.space.xs,
    fontFamily: pwc.fontBody,
    fontSize: 13,
    color: pwc.grey700,
    cursor: "pointer",
  } as React.CSSProperties,
};

export function UsersTab() {
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Add-user form
  const [newEmail, setNewEmail] = useState("");
  const [newName, setNewName] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [newIsAdmin, setNewIsAdmin] = useState(false);

  // Inline reset-password target (the row currently being reset, by email)
  const [resetTarget, setResetTarget] = useState<string | null>(null);
  const [resetValue, setResetValue] = useState("");

  const reload = useCallback(async () => {
    setError(null);
    try {
      setUsers(await adminListUsers());
    } catch (e) {
      setError(userMessage(e));
    }
  }, []);

  useEffect(() => { reload(); }, [reload]);

  // Run an action then refresh; funnel every error (incl. the 409 last-admin
  // guard) to the shared inline banner.
  const run = useCallback(async (fn: () => Promise<unknown>) => {
    setError(null);
    setBusy(true);
    try {
      await fn();
      await reload();
    } catch (e) {
      setError(userMessage(e));
    } finally {
      setBusy(false);
    }
  }, [reload]);

  const handleAdd = useCallback(async () => {
    if (newPassword.length < MIN_LEN) {
      setError(`Password must be at least ${MIN_LEN} characters.`);
      return;
    }
    await run(async () => {
      await adminAddUser({
        email: newEmail,
        display_name: newName,
        password: newPassword,
        is_admin: newIsAdmin,
      });
      setNewEmail("");
      setNewName("");
      setNewPassword("");
      setNewIsAdmin(false);
    });
  }, [newEmail, newName, newPassword, newIsAdmin, run]);

  const handleResetSubmit = useCallback(async (email: string) => {
    if (resetValue.length < MIN_LEN) {
      setError(`Password must be at least ${MIN_LEN} characters.`);
      return;
    }
    await run(async () => {
      await adminResetPassword(email, resetValue);
      setResetTarget(null);
      setResetValue("");
    });
  }, [resetValue, run]);

  return (
    <div>
      {error && <p style={styles.error} role="alert">{error}</p>}

      <table style={styles.table}>
        <thead>
          <tr>
            <th style={styles.th}>Email</th>
            <th style={styles.th}>Name</th>
            <th style={styles.th}>Status</th>
            <th style={styles.th}>Role</th>
            <th style={styles.th}>Actions</th>
          </tr>
        </thead>
        <tbody>
          {users.map((u) => (
            <tr key={u.email}>
              <td style={styles.td}>{u.email}</td>
              <td style={styles.td}>{u.display_name}</td>
              <td style={styles.td}>{u.disabled ? "disabled" : "active"}</td>
              <td style={styles.td}>{u.is_admin ? "admin" : "user"}</td>
              <td style={styles.td}>
                <button
                  className={uiClass.btnSecondary}
                  style={styles.actionBtn}
                  disabled={busy}
                  onClick={() => run(() => adminSetDisabled(u.email, !u.disabled))}
                >
                  {u.disabled ? "Enable" : "Disable"}
                </button>
                <button
                  className={uiClass.btnSecondary}
                  style={styles.actionBtn}
                  disabled={busy}
                  onClick={() => run(() => adminSetAdmin(u.email, !u.is_admin))}
                >
                  {u.is_admin ? "Revoke admin" : "Make admin"}
                </button>
                {resetTarget === u.email ? (
                  <span style={{ display: "inline-flex", gap: pwc.space.xs, alignItems: "center" }}>
                    <input
                      type="password"
                      value={resetValue}
                      onChange={(e) => setResetValue(e.target.value)}
                      placeholder="New password"
                      aria-label={`New password for ${u.email}`}
                      style={styles.input}
                    />
                    <button
                      className={uiClass.btnPrimary}
                      style={styles.saveButton}
                      disabled={busy}
                      onClick={() => handleResetSubmit(u.email)}
                    >
                      Set
                    </button>
                  </span>
                ) : (
                  <button
                    className={uiClass.btnSecondary}
                    style={styles.actionBtn}
                    disabled={busy}
                    onClick={() => { setResetTarget(u.email); setResetValue(""); setError(null); }}
                  >
                    Reset password
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <div style={styles.addForm}>
        <p style={styles.heading}>Add user</p>
        <div style={styles.addRow}>
          <input
            type="email"
            value={newEmail}
            onChange={(e) => setNewEmail(e.target.value)}
            placeholder="email@firm.com"
            aria-label="New user email"
            style={styles.input}
          />
          <input
            type="text"
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            placeholder="Display name"
            aria-label="New user display name"
            style={styles.input}
          />
          <input
            type="password"
            value={newPassword}
            onChange={(e) => setNewPassword(e.target.value)}
            placeholder="Password"
            aria-label="New user password"
            style={styles.input}
          />
          <label style={styles.checkboxLabel}>
            <input
              type="checkbox"
              checked={newIsAdmin}
              onChange={(e) => setNewIsAdmin(e.target.checked)}
            />
            Admin
          </label>
          <button
            className={uiClass.btnPrimary}
            style={styles.saveButton}
            disabled={busy}
            onClick={handleAdd}
          >
            Add user
          </button>
        </div>
      </div>
    </div>
  );
}
