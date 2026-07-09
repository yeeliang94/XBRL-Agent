import { useState, useEffect, useCallback } from "react";
import { userMessage } from "../lib/errors";
import { pwc } from "../lib/theme";
import { ui, uiClass } from "../lib/uiStyles";
import { ConfirmDialog } from "./ConfirmDialog";
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
  // Shared input primitive (Phase 6 layout normalization) instead of the
  // off-spec local one.
  input: {
    ...ui.input,
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
  // Visible field labels for the add-user form (UX-QA #8) — stacked above each
  // input so the field is named even after the placeholder disappears.
  fieldLabel: {
    display: "flex",
    flexDirection: "column" as const,
    gap: 2,
    fontFamily: pwc.fontHeading,
    fontSize: 11,
    color: pwc.grey500,
    textTransform: "uppercase" as const,
    letterSpacing: "0.04em",
  } as React.CSSProperties,
};

interface UsersTabProps {
  /** Signed-in admin's email. The self-destructive actions (Disable, Revoke
   *  admin) are hidden on this user's own row so the UI can't offer a
   *  self-lockout, even though the server's last-admin guard is the real
   *  backstop (UX-QA #13). */
  currentEmail?: string;
}

export function UsersTab({ currentEmail }: UsersTabProps = {}) {
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
  // One shared confirm dialog for the one-click account actions (disable/
  // enable, make/revoke admin) — they change who can sign in or administer the
  // tool, so they confirm like every other consequential action.
  const [pending, setPending] = useState<
    { title: string; message: string; confirmLabel: string; act: () => Promise<unknown> } | null
  >(null);

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
          {users.map((u) => {
            // Don't offer the signed-in admin controls that could lock them
            // out of their own account (UX-QA #13).
            const isSelf = currentEmail != null && u.email === currentEmail;
            return (
            <tr key={u.email}>
              <td style={styles.td}>{u.email}</td>
              <td style={styles.td}>{u.display_name}</td>
              <td style={styles.td}>{u.disabled ? "disabled" : "active"}</td>
              <td style={styles.td}>{u.is_admin ? "admin" : "user"}</td>
              <td style={styles.td}>
                {!isSelf && (
                  <button
                    className={uiClass.btnSecondary}
                    style={styles.actionBtn}
                    disabled={busy}
                    onClick={() =>
                      setPending({
                        title: u.disabled ? `Enable ${u.email}?` : `Disable ${u.email}?`,
                        message: u.disabled
                          ? "This account will be able to sign in again."
                          : "This account will no longer be able to sign in. Any active sessions end.",
                        confirmLabel: u.disabled ? "Enable" : "Disable",
                        act: () => adminSetDisabled(u.email, !u.disabled),
                      })
                    }
                  >
                    {u.disabled ? "Enable" : "Disable"}
                  </button>
                )}
                {!isSelf && (
                  <button
                    className={uiClass.btnSecondary}
                    style={styles.actionBtn}
                    disabled={busy}
                    onClick={() =>
                      setPending({
                        title: u.is_admin ? `Revoke admin from ${u.email}?` : `Make ${u.email} an admin?`,
                        message: u.is_admin
                          ? "This account will lose access to settings and user management."
                          : "This account will be able to change shared settings and manage other users.",
                        confirmLabel: u.is_admin ? "Revoke admin" : "Make admin",
                        act: () => adminSetAdmin(u.email, !u.is_admin),
                      })
                    }
                  >
                    {u.is_admin ? "Revoke admin" : "Make admin"}
                  </button>
                )}
                {resetTarget === u.email ? (
                  <span style={{ display: "inline-flex", gap: pwc.space.xs, alignItems: "center" }}>
                    <input
                      type="password"
                      value={resetValue}
                      onChange={(e) => setResetValue(e.target.value)}
                      placeholder="New password"
                      aria-label={`New password for ${u.email}`}
                      autoComplete="new-password"
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
            );
          })}
        </tbody>
      </table>

      <div style={styles.addForm}>
        <p style={styles.heading}>Add user</p>
        {/* autoComplete guards (UX-QA #8): an email field directly above a
            password field triggers the browser's login-form heuristic, which
            autofilled the admin's own email into Name and their saved password
            into Password. `off` / `new-password` defeat it — the same guard
            GeneralSettingsForm already uses. Visible <label>s so the fields are
            named even when placeholders vanish on input. */}
        <div style={styles.addRow}>
          <label style={styles.fieldLabel}>
            Email
            <input
              type="email"
              value={newEmail}
              onChange={(e) => setNewEmail(e.target.value)}
              placeholder="email@firm.com"
              aria-label="New user email"
              autoComplete="off"
              style={styles.input}
            />
          </label>
          <label style={styles.fieldLabel}>
            Name
            <input
              type="text"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              placeholder="Display name"
              aria-label="New user display name"
              autoComplete="off"
              style={styles.input}
            />
          </label>
          <label style={styles.fieldLabel}>
            Password (min 8 characters)
            <input
              type="password"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              placeholder="Password"
              aria-label="New user password"
              autoComplete="new-password"
              style={styles.input}
            />
          </label>
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

      <ConfirmDialog
        isOpen={pending !== null}
        title={pending?.title ?? ""}
        message={pending?.message ?? ""}
        confirmLabel={pending?.confirmLabel ?? "Confirm"}
        busy={busy}
        onConfirm={() => {
          const p = pending;
          setPending(null);
          if (p) void run(p.act);
        }}
        onCancel={() => setPending(null)}
      />
    </div>
  );
}
