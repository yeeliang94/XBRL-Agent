import { useState } from "react";
import type { CSSProperties, FormEvent } from "react";
import { ds, tokens } from "../lib/theme";
import { ui, uiClass } from "../lib/uiStyles";
import { changePassword, loginPassword, logout } from "../lib/api";
import { ErrorCircle } from "../components/iconGlyphs";

// PLAN auth Phase 1.4 — the email + password login form. SSO (Microsoft) is a
// later phase; until then password is the only method. Inline styles + theme
// tokens throughout (gotcha #7: no Tailwind).

const styles = {
  page: {
    minHeight: "100vh",
    background: ds.grey50,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    padding: ds.space.xl,
  } as CSSProperties,
  card: {
    ...ui.card,
    width: "100%",
    maxWidth: tokens.layout.auth,
    padding: ds.space.xxl,
    display: "flex",
    flexDirection: "column",
    gap: ds.space.lg,
  } as CSSProperties,
  title: {
    fontFamily: ds.fontHeading,
    fontWeight: ds.weight.semibold,
    fontSize: 28,
    color: ds.grey900,
    margin: 0,
  } as CSSProperties,
  subtitle: {
    ...ui.sectionTitle,
    margin: `${ds.space.sm}px 0 0`,
  } as CSSProperties,
  helper: {
    ...ui.metadata,
    margin: 0,
  } as CSSProperties,
  field: {
    display: "flex",
    flexDirection: "column",
    gap: ds.space.sm,
  } as CSSProperties,
  error: {
    ...ui.alertError,
    fontSize: 14,
  } as CSSProperties,
  fieldError: {
    margin: 0,
    fontSize: 13,
    color: ds.errorText,
  } as CSSProperties,
  notice: {
    ...ui.bodyText,
    margin: 0,
  } as CSSProperties,
} as const;

export interface LoginPageProps {
  /** Called after a successful login so the shell can re-check /api/auth/me. */
  onAuthenticated: () => void;
  /** Shown once when an open session ended (expired, reset or disabled). */
  signedOut?: boolean;
}

export function LoginPage({ onAuthenticated, signedOut = false }: LoginPageProps) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      const result = await loginPassword(email, password);
      if (result.ok) {
        onAuthenticated();
        return;
      }
      // 429 is the lockout signal; everything else is the generic credential
      // error (the backend deliberately doesn't distinguish wrong-password from
      // unknown-email).
      setError(
        result.status === 429
          ? "Too many attempts. Please wait a few minutes and try again."
          : result.detail,
      );
    } catch {
      setError("Could not reach the server. Check your connection and retry.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div style={styles.page}>
      <form style={styles.card} onSubmit={handleSubmit}>
        <h1 style={styles.title}>XBRL Agent</h1>

        {signedOut && !error && <p role="status" style={styles.notice}>You were signed out. Sign in again to continue.</p>}

        {error && (
          <div style={styles.error} role="alert">
            <span aria-hidden="true" style={ui.alertIcon(ds.error)}><ErrorCircle size={20} /></span>
            <span>{error}</span>
          </div>
        )}

        <div style={styles.field}>
          <label htmlFor="login-email" style={ui.fieldLabel}>
            Email
          </label>
          <input
            id="login-email"
            type="email"
            autoComplete="username"
            required
            style={ui.input}
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </div>

        <div style={styles.field}>
          <label htmlFor="login-password" style={ui.fieldLabel}>
            Password
          </label>
          <input
            id="login-password"
            type="password"
            autoComplete="current-password"
            required
            style={ui.input}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </div>

        <button
          type="submit"
          className={uiClass.btnPrimary}
          style={{ ...ui.buttonPrimary, ...ui.buttonLg }}
          disabled={submitting}
        >
          {submitting ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </div>
  );
}

export interface ChoosePasswordPageProps {
  /** Called once the new password is saved so the shell can re-check /api/auth/me. */
  onChanged: () => void;
  /** Signed in to the wrong account on a shared machine: leave without changing it. */
  onLogout: () => void;
}

/** First sign-in after an admin adds or resets an account: the temporary
 *  password must be replaced before anything else is available. The server
 *  does not ask for the temporary password again on this path. */
export function ChoosePasswordPage({ onChanged, onLogout }: ChoosePasswordPageProps) {
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [fieldError, setFieldError] = useState<{ field: "password" | "confirm"; message: string } | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (submitting) return;
    if (password.length < 8) {
      setFieldError({ field: "password", message: "Use at least 8 characters." });
      return;
    }
    if (password !== confirm) {
      setFieldError({ field: "confirm", message: "The passwords don't match." });
      return;
    }
    setFieldError(null);
    setSubmitting(true);
    setError(null);
    try {
      await changePassword("", password);
      onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save the password. Try again.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div style={styles.page}>
      <form style={styles.card} onSubmit={handleSubmit} noValidate>
        <div>
          <h1 style={styles.title}>XBRL Agent</h1>
          <h2 style={styles.subtitle}>Choose a new password</h2>
        </div>

        {error && (
          <div style={styles.error} role="alert">
            <span aria-hidden="true" style={ui.alertIcon(ds.error)}><ErrorCircle size={20} /></span>
            <span>{error}</span>
          </div>
        )}

        <div style={styles.field}>
          <label htmlFor="new-password" style={ui.fieldLabel}>New password</label>
          <input id="new-password" type="password" autoComplete="new-password" required
            style={ui.input} value={password} onChange={(e) => { setPassword(e.target.value); setFieldError(null); }}
            aria-invalid={fieldError?.field === "password" || undefined}
            aria-describedby={fieldError?.field === "password" ? "new-password-error" : "password-rule"} />
          {fieldError?.field === "password"
            ? <p id="new-password-error" role="alert" style={styles.fieldError}>{fieldError.message}</p>
            : <p id="password-rule" style={styles.helper}>At least 8 characters</p>}
        </div>

        <div style={styles.field}>
          <label htmlFor="confirm-password" style={ui.fieldLabel}>Confirm password</label>
          <input id="confirm-password" type="password" autoComplete="new-password" required
            style={ui.input} value={confirm} onChange={(e) => { setConfirm(e.target.value); setFieldError(null); }}
            aria-invalid={fieldError?.field === "confirm" || undefined}
            aria-describedby={fieldError?.field === "confirm" ? "confirm-password-error" : undefined} />
          {fieldError?.field === "confirm" && <p id="confirm-password-error" role="alert" style={styles.fieldError}>{fieldError.message}</p>}
        </div>

        <button
          type="submit"
          className={uiClass.btnPrimary}
          style={{ ...ui.buttonPrimary, ...ui.buttonLg }}
          disabled={submitting}
        >
          {submitting ? "Saving…" : "Save password"}
        </button>
        <button type="button" className={uiClass.btnQuiet} style={{ ...ui.buttonQuiet, alignSelf: "center" }}
          onClick={() => { void logout().finally(onLogout); }}>
          Log out
        </button>
      </form>
    </div>
  );
}
