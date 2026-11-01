import { useState, useEffect, useCallback, useRef, useId } from "react";
import { createPortal } from "react-dom";
import { ApiError, userMessage } from "../lib/errors";
import { ds, tokens } from "../lib/theme";
import { ui, uiClass } from "../lib/uiStyles";
import { ConfirmDialog } from "./ConfirmDialog";
import { DisclosureChevron, Icon } from "./icons";
import { ContentCopy } from "./iconGlyphs";
import {
  adminListUsers,
  adminAddUser,
  adminSetDisabled,
  adminResetPassword,
  adminSetAdmin,
  adminAuditLog,
  type AdminUser,
  type AuditEvent,
} from "../lib/api";

// ---------------------------------------------------------------------------
// UsersTab — admin people management (gotcha #7: inline styles). A table of
// accounts whose rows open Manage person; Add person issues a one-time
// temporary password; the Audit log disclosure lists administrator actions.
// Every action is enforced server-side; the 409 last-admin guard is shown in
// the dialog that triggered it.
// ---------------------------------------------------------------------------

const SELF_REASON = "You can't change your own access.";
const NARROW_QUERY = "(max-width: 640px)";

const muted = tokens.color.text.secondary;

const styles = {
  header: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    gap: ds.space.md,
    marginBottom: ds.space.lg,
  } as React.CSSProperties,
  error: {
    fontFamily: ds.fontBody,
    fontSize: 13,
    color: ds.errorText,
    margin: `0 0 ${ds.space.md}px`,
  } as React.CSSProperties,
  table: {
    width: "100%",
    borderCollapse: "collapse" as const,
    fontFamily: ds.fontBody,
    fontSize: 14,
  } as React.CSSProperties,
  td: { ...ui.td, verticalAlign: "middle" as const } as React.CSSProperties,
  rowButton: {
    display: "block",
    width: "100%",
    padding: 0,
    border: "none",
    background: "transparent",
    textAlign: "left" as const,
    font: "inherit",
    color: "inherit",
    cursor: "pointer",
    overflowWrap: "anywhere" as const,
  } as React.CSSProperties,
  email: { ...ui.metadata, display: "block" } as React.CSSProperties,
  dialogMeta: { ...ui.metadata, margin: `${ds.space.xs}px 0 0`, overflowWrap: "anywhere" as const } as React.CSSProperties,
  field: {
    display: "flex",
    flexDirection: "column" as const,
    gap: ds.space.sm,
    marginTop: ds.space.lg,
  } as React.CSSProperties,
  fieldLabel: { ...ui.fieldLabel, color: tokens.color.text.primary } as React.CSSProperties,
  fieldError: { ...ui.metadata, color: ds.errorText, margin: 0 } as React.CSSProperties,
  srOnly: {
    position: "absolute" as const,
    width: 1,
    height: 1,
    overflow: "hidden",
    clip: "rect(0 0 0 0)",
    whiteSpace: "nowrap" as const,
  } as React.CSSProperties,
  /** Matches the shared `.app-btn-*:disabled` look for controls kept
   *  focusable with aria-disabled so their reason stays reachable. */
  locked: { opacity: 0.45, cursor: "not-allowed" } as React.CSSProperties,
  manageRow: {
    display: "grid",
    gridTemplateColumns: "88px minmax(0, 1fr)",
    alignItems: "center",
    gap: ds.space.md,
    marginTop: ds.space.lg,
  } as React.CSSProperties,
  passwordRow: { display: "flex", gap: ds.space.sm, alignItems: "center" } as React.CSSProperties,
  passwordInput: {
    ...ui.input,
    fontFamily: ds.fontMono,
    flex: 1,
    minWidth: 0,
  } as React.CSSProperties,
  auditList: { listStyle: "none", margin: `${ds.space.sm}px 0 0`, padding: 0 } as React.CSSProperties,
  auditItem: {
    display: "grid",
    gridTemplateColumns: "112px minmax(0, 1fr)",
    gap: ds.space.md,
    padding: `${ds.space.sm}px 0`,
    fontFamily: ds.fontBody,
    fontSize: 14,
    color: tokens.color.text.body,
    overflowWrap: "anywhere" as const,
  } as React.CSSProperties,
  disclosureButton: {
    display: "inline-flex",
    alignItems: "center",
    gap: ds.space.sm,
    padding: `${ds.space.sm}px 0`,
    background: "transparent",
    border: "none",
    cursor: "pointer",
    fontFamily: ds.fontHeading,
    fontSize: 14,
    fontWeight: ds.weight.medium,
    color: tokens.color.text.primary,
  } as React.CSSProperties,
};

// --- Wording helpers -------------------------------------------------------

function nameOf(u: Pick<AdminUser, "display_name" | "email">): string {
  return u.display_name?.trim() || u.email;
}

function statusOf(u: AdminUser): string {
  if (u.disabled) return "Disabled";
  if (!u.last_sign_in_at) return "Not signed in yet";
  if (u.must_change_password) return "Waiting for new password";
  return "Active";
}

function startOfDay(d: Date): number {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "3 Sep"; the year is added only when it is not the current year. */
function shortDate(d: Date, now: Date): string {
  const base = `${d.getDate()} ${MONTHS[d.getMonth()]}`;
  return d.getFullYear() === now.getFullYear() ? base : `${base} ${d.getFullYear()}`;
}

/** "Today", "Yesterday", "N days ago" (up to 6), then "3 Sep"; "—" when never. */
function lastSignedIn(iso: string | null, now: Date = new Date()): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  const days = Math.round((startOfDay(now) - startOfDay(d)) / 86_400_000);
  if (days <= 0) return "Today";
  if (days === 1) return "Yesterday";
  if (days <= 6) return `${days} days ago`;
  return shortDate(d, now);
}

function auditTime(iso: string, now: Date = new Date()): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  if (startOfDay(d) === startOfDay(now)) {
    return `Today ${d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" })}`;
  }
  return shortDate(d, now);
}

// Lower case: the status sits mid-sentence ("marked issue 38 resolved").
const ISSUE_STATUS: Record<string, string> = { new: "new", in_progress: "in progress", resolved: "resolved" };

function auditSentence(e: AuditEvent): string {
  const actor = e.actor_name?.trim() || e.actor_email;
  const target = e.target_name?.trim() || e.target;
  const detail = e.detail ?? {};
  switch (e.action) {
    case "user_added":
      return `${actor} added ${target}`;
    case "user_disabled":
      return `${actor} disabled ${target}`;
    case "user_enabled":
      return `${actor} enabled ${target}`;
    case "password_reset":
      return `${actor} reset ${target}'s password`;
    case "role_changed":
      return detail.is_admin
        ? `${actor} made ${target} an administrator`
        : `${actor} removed administrator role from ${target}`;
    case "issue_updated": {
      const from = detail.from as string | undefined;
      const to = detail.to as string | undefined;
      if (to && to !== from) return `${actor} marked issue ${e.target} ${ISSUE_STATUS[to] ?? to}`;
      return `${actor} updated issue ${e.target}`;
    }
    case "issue_deleted":
      return `${actor} deleted issue ${e.target}`;
    default:
      return `${actor} changed ${target}`;
  }
}

function useNarrow(): boolean {
  const getQuery = () =>
    typeof window !== "undefined" && typeof window.matchMedia === "function"
      ? window.matchMedia(NARROW_QUERY)
      : null;
  const [narrow, setNarrow] = useState(() => getQuery()?.matches ?? false);
  useEffect(() => {
    const query = getQuery();
    if (!query) return;
    const onChange = () => setNarrow(query.matches);
    query.addEventListener?.("change", onChange);
    return () => query.removeEventListener?.("change", onChange);
  }, []);
  return narrow;
}

// --- Dialog shell ----------------------------------------------------------

const FOCUSABLE = 'button:not([disabled]), input:not([disabled]), [href], [tabindex]:not([tabindex="-1"])';

/** Modal with ConfirmDialog's behaviour: focus the first control on open,
 *  trap Tab, Escape closes (unless busy or a confirmation sits on top), and
 *  focus returns to the trigger on close. */
function DialogShell({
  title,
  onClose,
  suspended,
  children,
}: {
  title: string;
  onClose: () => void;
  /** True while a ConfirmDialog is stacked above, or an action is running. */
  suspended: boolean;
  children: React.ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const titleId = useId();
  const onCloseRef = useRef(onClose);
  const suspendedRef = useRef(suspended);
  onCloseRef.current = onClose;
  suspendedRef.current = suspended;

  useEffect(() => {
    const previouslyFocused = document.activeElement as HTMLElement | null;
    ref.current?.querySelector<HTMLElement>(FOCUSABLE)?.focus();
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !suspendedRef.current) onCloseRef.current();
    };
    window.addEventListener("keydown", handler);
    return () => {
      window.removeEventListener("keydown", handler);
      previouslyFocused?.focus?.();
    };
  }, []);

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key !== "Tab" || !ref.current) return;
    const items = Array.from(ref.current.querySelectorAll<HTMLElement>(FOCUSABLE));
    if (items.length === 0) return;
    const first = items[0];
    const last = items[items.length - 1];
    const active = document.activeElement;
    if (e.shiftKey && (active === first || !ref.current.contains(active))) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && (active === last || !ref.current.contains(active))) {
      e.preventDefault();
      first.focus();
    }
  };

  // Rendered at the document root so the Settings section's entrance
  // animation (a transform) cannot confine the full-screen scrim.
  return createPortal(
    <div
      style={{ ...ui.scrim, padding: ds.space.lg }}
      className="app-dialog-scrim-enter"
      onClick={(e) => {
        if (e.target === e.currentTarget && !suspended) onClose();
      }}
    >
      <div
        ref={ref}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        style={{ ...ui.dialog, maxWidth: 480 }}
        className="app-dialog-enter"
        onKeyDown={onKeyDown}
      >
        <h2 id={titleId} style={{ ...ui.dialogTitle, overflowWrap: "anywhere" }}>{title}</h2>
        {children}
      </div>
    </div>,
    document.body,
  );
}

/** Two-option User | Administrator segmented control. `lockedReason` keeps
 *  the buttons focusable (aria-disabled) and exposes the reason by tooltip
 *  and aria-describedby; `disabled` is the transient busy state. */
function RoleControl({
  isAdmin,
  onChange,
  lockedReason,
  lockedReasonId,
  disabled = false,
  labelId,
}: {
  isAdmin: boolean;
  onChange: (isAdmin: boolean) => void;
  lockedReason?: string;
  lockedReasonId?: string;
  disabled?: boolean;
  labelId: string;
}) {
  const locked = !!lockedReason;
  return (
    <span style={{ display: "inline-flex" }}>
      <div role="group" aria-labelledby={labelId} className="segmented-control-group" style={ui.segmentGroup}>
        {([false, true] as const).map((admin) => {
          const active = isAdmin === admin;
          return (
            <button
              key={String(admin)}
              type="button"
              className="segmented-control-button"
              aria-pressed={active}
              aria-disabled={locked ? true : undefined}
              aria-describedby={locked ? lockedReasonId : undefined}
              data-tooltip={lockedReason}
              disabled={disabled}
              onClick={() => { if (!locked && !active) onChange(admin); }}
              style={{
                ...ui.segmentButton,
                ...(active ? ui.segmentButtonActive : {}),
                ...(locked ? styles.locked : disabled ? { cursor: "not-allowed" } : {}),
              }}
            >
              {admin ? "Administrator" : "User"}
            </button>
          );
        })}
      </div>
    </span>
  );
}

/** One-time temporary password panel (shared by Add person and Reset). */
function TemporaryPassword({ password, onDone }: { password: string; onDone: () => void }) {
  const [copied, setCopied] = useState(false);
  const [copyFailed, setCopyFailed] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const inputId = useId();
  const helperId = useId();
  useEffect(() => {
    if (!copied) return;
    const t = window.setTimeout(() => setCopied(false), 2000);
    return () => window.clearTimeout(t);
  }, [copied]);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(password);
      setCopyFailed(false);
      setCopied(true);
    } catch {
      setCopied(false);
      setCopyFailed(true);
      inputRef.current?.focus();
      inputRef.current?.select();
    }
  };
  return (
    <>
      <div style={styles.field}>
        <label htmlFor={inputId} style={styles.fieldLabel}>Temporary password</label>
        <div style={styles.passwordRow}>
          <input
            ref={inputRef}
            id={inputId}
            readOnly
            value={password}
            aria-describedby={helperId}
            style={styles.passwordInput}
            onFocus={(e) => e.currentTarget.select()}
          />
          <button type="button" className={uiClass.btnSecondary} style={ui.buttonSecondary} onClick={copy}>
            <Icon glyph={ContentCopy} size={16} />
            {copied ? "Copied" : "Copy"}
          </button>
        </div>
        {copyFailed && (
          <p role="alert" style={styles.fieldError}>Couldn't copy. Select the password and copy it.</p>
        )}
        <p id={helperId} style={{ ...ui.metadata, margin: 0 }}>Shown once. They choose their own at first sign-in.</p>
      </div>
      <div style={ui.dialogActionBar}>
        <button type="button" className={uiClass.btnPrimary} style={ui.buttonPrimary} onClick={onDone}>
          Done
        </button>
      </div>
    </>
  );
}

// --- Add person --------------------------------------------------------------

function AddPersonDialog({ onClose, onAdded }: { onClose: () => void; onAdded: () => void }) {
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [isAdmin, setIsAdmin] = useState(false);
  const [busy, setBusy] = useState(false);
  const [emailError, setEmailError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<{ label: string; password: string } | null>(null);
  const emailId = useId();
  const nameId = useId();
  const roleId = useId();
  const emailErrorId = useId();

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setEmailError(null);
    setError(null);
    const trimmed = email.trim();
    if (!trimmed) {
      setEmailError("Enter an email.");
      return;
    }
    setBusy(true);
    try {
      const res = await adminAddUser({ email: trimmed, display_name: name.trim(), is_admin: isAdmin });
      setResult({ label: nameOf(res.user ?? { email: trimmed, display_name: name.trim() }), password: res.temporary_password });
      onAdded();
    } catch (err) {
      const status = err instanceof ApiError ? err.status : undefined;
      if (status === 409 || status === 422) setEmailError(userMessage(err));
      else setError(userMessage(err));
    } finally {
      setBusy(false);
    }
  };

  if (result) {
    return (
      <DialogShell key="done" title={`${result.label} can now sign in`} onClose={onClose} suspended={false}>
        <TemporaryPassword password={result.password} onDone={onClose} />
      </DialogShell>
    );
  }

  return (
    <DialogShell key="form" title="Add person" onClose={onClose} suspended={busy}>
      <form onSubmit={submit} noValidate>
        <div style={styles.field}>
          <label htmlFor={emailId} style={styles.fieldLabel}>Email</label>
          <input
            id={emailId}
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            autoComplete="off"
            aria-invalid={emailError ? true : undefined}
            aria-describedby={emailError ? emailErrorId : undefined}
            style={ui.input}
          />
          {emailError && <p id={emailErrorId} role="alert" style={styles.fieldError}>{emailError}</p>}
        </div>
        <div style={styles.field}>
          <label htmlFor={nameId} style={styles.fieldLabel}>Name</label>
          <input
            id={nameId}
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            autoComplete="off"
            style={ui.input}
          />
        </div>
        <div style={styles.field}>
          <span id={roleId} style={styles.fieldLabel}>Role</span>
          <RoleControl isAdmin={isAdmin} onChange={setIsAdmin} labelId={roleId} />
        </div>
        {error && <p role="alert" style={{ ...styles.error, margin: `${ds.space.lg}px 0 0` }}>{error}</p>}
        <div style={ui.dialogActionBar}>
          <button type="button" className={uiClass.btnQuiet} style={ui.buttonQuiet} onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button type="submit" className={uiClass.btnPrimary} style={ui.buttonPrimary} disabled={busy}>
            {busy ? "Adding person…" : "Add person"}
          </button>
        </div>
      </form>
    </DialogShell>
  );
}

// --- Manage person -----------------------------------------------------------

interface PendingConfirm {
  title: string;
  message: string;
  confirmLabel: string;
  busyLabel: string;
  danger: boolean;
  act: () => Promise<void>;
}

function ManagePersonDialog({
  user,
  isSelf,
  onClose,
  onChanged,
}: {
  user: AdminUser;
  isSelf: boolean;
  onClose: () => void;
  onChanged: () => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState<PendingConfirm | null>(null);
  const [password, setPassword] = useState<string | null>(null);
  const roleId = useId();
  const selfReasonId = useId();
  const name = nameOf(user);
  const selfReason = isSelf ? SELF_REASON : undefined;

  const run = async (fn: () => Promise<void>) => {
    setError(null);
    setBusy(true);
    try {
      await fn();
      await onChanged();
    } catch (e) {
      setError(userMessage(e));
    } finally {
      setBusy(false);
      setPending(null);
    }
  };

  if (password) {
    return (
      <DialogShell key="password" title={`${name} can now sign in`} onClose={onClose} suspended={false}>
        <TemporaryPassword password={password} onDone={onClose} />
      </DialogShell>
    );
  }

  const askRole = (makeAdmin: boolean) =>
    setPending(makeAdmin
      ? {
          title: `Make ${name} an administrator?`,
          message: "They can manage people and shared settings.",
          confirmLabel: "Make administrator",
          busyLabel: "Saving…",
          danger: false,
          act: async () => { await adminSetAdmin(user.email, true); },
        }
      : {
          title: `Remove administrator role from ${name}?`,
          message: "They can no longer manage people or shared settings.",
          confirmLabel: "Remove administrator role",
          busyLabel: "Saving…",
          danger: true,
          act: async () => { await adminSetAdmin(user.email, false); },
        });

  const askReset = () =>
    setPending({
      title: `Reset ${name}'s password?`,
      message: "They are signed out now.",
      confirmLabel: "Reset password",
      busyLabel: "Resetting…",
      danger: false,
      act: async () => {
        const res = await adminResetPassword(user.email);
        setPassword(res.temporary_password);
      },
    });

  const askDisable = () =>
    setPending({
      title: `Disable ${name}?`,
      message: "They are signed out now and cannot sign in.",
      confirmLabel: "Disable account",
      busyLabel: "Disabling…",
      danger: true,
      act: async () => { await adminSetDisabled(user.email, true); },
    });

  return (
    <>
      <DialogShell key="manage" title={name} onClose={onClose} suspended={busy || pending !== null}>
        <p style={styles.dialogMeta}>
          {user.email} · Last signed in {lastSignedIn(user.last_sign_in_at).replace(/^(Today|Yesterday)$/, (m) => m.toLowerCase())}
        </p>
        <div style={styles.manageRow}>
          <span id={roleId} style={styles.fieldLabel}>Role</span>
          <RoleControl
            isAdmin={user.is_admin}
            onChange={askRole}
            lockedReason={selfReason}
            lockedReasonId={selfReasonId}
            disabled={busy}
            labelId={roleId}
          />
        </div>
        <div style={styles.manageRow}>
          <span style={styles.fieldLabel}>Password</span>
          <span>
            <button type="button" className={uiClass.btnSecondary} style={ui.buttonSecondary} disabled={busy} onClick={askReset}>
              Reset password
            </button>
          </span>
        </div>
        <div style={styles.manageRow}>
          <span style={styles.fieldLabel}>Access</span>
          <span style={{ justifySelf: "start" }}>
            {user.disabled ? (
              <button
                type="button"
                className={uiClass.btnSecondary}
                style={{ ...ui.buttonSecondary, ...(isSelf ? styles.locked : {}) }}
                disabled={busy}
                aria-disabled={isSelf ? true : undefined}
                aria-describedby={isSelf ? selfReasonId : undefined}
                data-tooltip={selfReason}
                onClick={() => { if (!isSelf) void run(async () => { await adminSetDisabled(user.email, false); }); }}
              >
                Enable account
              </button>
            ) : (
              <button
                type="button"
                className={uiClass.btnDanger}
                style={{ ...ui.buttonDanger, ...(isSelf ? styles.locked : {}) }}
                disabled={busy}
                aria-disabled={isSelf ? true : undefined}
                aria-describedby={isSelf ? selfReasonId : undefined}
                data-tooltip={selfReason}
                onClick={() => { if (!isSelf) askDisable(); }}
              >
                Disable account
              </button>
            )}
          </span>
        </div>
        {isSelf && <span id={selfReasonId} style={styles.srOnly}>{SELF_REASON}</span>}
        {error && <p role="alert" style={{ ...styles.error, margin: `${ds.space.lg}px 0 0` }}>{error}</p>}
        <div style={ui.dialogActionBar}>
          <button type="button" className={uiClass.btnSecondary} style={ui.buttonSecondary} onClick={onClose} disabled={busy}>
            Close
          </button>
        </div>
      </DialogShell>
      <ConfirmDialog
        isOpen={pending !== null}
        title={pending?.title ?? ""}
        message={pending?.message ?? ""}
        confirmLabel={pending?.confirmLabel ?? "Confirm"}
        busyLabel={pending?.busyLabel}
        danger={pending?.danger ?? true}
        busy={busy}
        onConfirm={() => { if (pending) void run(pending.act); }}
        onCancel={() => setPending(null)}
      />
    </>
  );
}

// --- Audit log -----------------------------------------------------------------

function AuditLog() {
  const [open, setOpen] = useState(false);
  const [events, setEvents] = useState<AuditEvent[] | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const bodyId = useId();

  const load = useCallback(async (before?: number) => {
    setLoading(true);
    setError(null);
    try {
      const res = await adminAuditLog(before);
      setEvents((prev) => (before && prev ? [...prev, ...res.events] : res.events));
      setHasMore(res.has_more);
    } catch (e) {
      setError(userMessage(e));
    } finally {
      setLoading(false);
    }
  }, []);

  const toggle = () => {
    const next = !open;
    setOpen(next);
    if (next && events === null && !loading) void load();
  };

  return (
    <div style={{ marginTop: ds.space.xl }}>
      <button type="button" aria-expanded={open} aria-controls={bodyId} onClick={toggle} style={styles.disclosureButton}>
        Audit log
        <DisclosureChevron open={open} />
      </button>
      {open && (
        <div id={bodyId} className="app-disclosure-content">
          {error && <p role="alert" style={{ ...styles.error, marginTop: ds.space.sm }}>{error}</p>}
          {events === null && loading && <p style={{ ...ui.metadata, margin: `${ds.space.sm}px 0 0` }}>Loading…</p>}
          {events !== null && events.length === 0 && (
            <p style={{ ...ui.bodyText, margin: `${ds.space.sm}px 0 0` }}>No changes recorded yet.</p>
          )}
          {events !== null && events.length > 0 && (
            <ul style={styles.auditList}>
              {events.map((e) => (
                <li key={e.id} style={styles.auditItem}>
                  <time dateTime={e.at} style={ui.metadata}>{auditTime(e.at)}</time>
                  <span>{auditSentence(e)}</span>
                </li>
              ))}
            </ul>
          )}
          {hasMore && events && events.length > 0 && (
            <button
              type="button"
              className={uiClass.btnSecondary}
              style={{ ...ui.buttonSecondary, marginTop: ds.space.sm }}
              disabled={loading}
              onClick={() => load(events[events.length - 1].id)}
            >
              {loading ? "Loading…" : "Show more"}
            </button>
          )}
        </div>
      )}
    </div>
  );
}

// --- Users section ---------------------------------------------------------------

interface UsersTabProps {
  /** Signed-in admin's email. Their own row marks "(You)" and its Role and
   *  Disable controls are locked (aria-disabled, reason on hover and focus); the server's last-admin guard
   *  remains the real backstop. */
  currentEmail?: string;
}

export function UsersTab({ currentEmail }: UsersTabProps = {}) {
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [managing, setManaging] = useState<string | null>(null);
  const narrow = useNarrow();

  const reload = useCallback(async () => {
    try {
      setUsers(await adminListUsers());
      setError(null);
    } catch (e) {
      setError(userMessage(e));
    }
  }, []);

  useEffect(() => { void reload(); }, [reload]);

  const managed = managing ? users.find((u) => u.email === managing) ?? null : null;

  return (
    <div>
      <div style={styles.header}>
        <h2 style={ui.sectionTitle}>Users</h2>
        <button type="button" className={uiClass.btnPrimary} style={ui.buttonPrimary} onClick={() => setAdding(true)}>
          Add person
        </button>
      </div>

      {error && <p style={styles.error} role="alert">{error}</p>}

      <div className="settings-users-table-wrap">
        <table style={styles.table}>
          <thead>
            <tr>
              <th style={ui.th}>Name</th>
              {!narrow && <th style={ui.th}>Role</th>}
              <th style={ui.th}>Status</th>
              {!narrow && <th style={ui.th}>Last signed in</th>}
            </tr>
          </thead>
          <tbody>
            {users.map((u) => {
              const isSelf = currentEmail != null && u.email === currentEmail;
              const name = nameOf(u);
              const cellColor = u.disabled ? muted : tokens.color.text.primary;
              const td = { ...styles.td, color: cellColor };
              return (
                <tr
                  key={u.email}
                  className={uiClass.tableRow}
                  style={{ cursor: "pointer" }}
                  onClick={() => setManaging(u.email)}
                >
                  <td style={td}>
                    {/* The row is the click target; this button gives it
                        keyboard access (Enter / Space) and a name. */}
                    <button type="button" aria-label={`Manage ${name}`} style={styles.rowButton}>
                      {u.display_name?.trim() ? u.display_name : u.email}
                      {isSelf && <span style={{ color: muted }}> (You)</span>}
                      {u.display_name?.trim() && <span style={styles.email}>{u.email}</span>}
                    </button>
                  </td>
                  {!narrow && <td style={td}>{u.is_admin ? "Administrator" : "User"}</td>}
                  <td style={td}>{statusOf(u)}</td>
                  {!narrow && <td style={td}>{lastSignedIn(u.last_sign_in_at)}</td>}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <AuditLog />

      {adding && <AddPersonDialog onClose={() => setAdding(false)} onAdded={() => void reload()} />}
      {managed && (
        <ManagePersonDialog
          key={managed.email}
          user={managed}
          isSelf={currentEmail != null && managed.email === currentEmail}
          onClose={() => setManaging(null)}
          onChanged={reload}
        />
      )}
    </div>
  );
}
