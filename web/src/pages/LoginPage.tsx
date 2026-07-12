import { useState } from "react";
import type { CSSProperties, FormEvent } from "react";
import { pwc, tokens } from "../lib/theme";
import { ui, uiClass } from "../lib/uiStyles";
import { loginPassword } from "../lib/api";

// PLAN auth Phase 1.4 — the email + password login form. SSO (Microsoft) is a
// later phase; until then password is the only method. Inline styles + theme
// tokens throughout (gotcha #7: no Tailwind).

const styles = {
  page: {
    minHeight: "100vh",
    background: pwc.grey50,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    padding: pwc.space.xl,
  } as CSSProperties,
  card: {
    ...ui.card,
    width: "100%",
    maxWidth: tokens.layout.auth,
    padding: pwc.space.xxl,
    display: "flex",
    flexDirection: "column",
    gap: pwc.space.lg,
  } as CSSProperties,
  title: {
    fontFamily: pwc.fontHeading,
    fontWeight: pwc.weight.semibold,
    fontSize: 22,
    color: pwc.grey900,
    margin: 0,
  } as CSSProperties,
  subtitle: {
    fontFamily: pwc.fontBody,
    fontSize: 14,
    color: pwc.grey700,
    margin: 0,
  } as CSSProperties,
  field: {
    display: "flex",
    flexDirection: "column",
    gap: pwc.space.xs,
  } as CSSProperties,
  error: {
    ...ui.alertError,
    fontSize: 14,
  } as CSSProperties,
} as const;

export interface LoginPageProps {
  /** Called after a successful login so the shell can re-check /api/auth/me. */
  onAuthenticated: () => void;
}

export function LoginPage({ onAuthenticated }: LoginPageProps) {
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
        <div>
          <h1 style={styles.title}>XBRL Agent</h1>
          <p style={styles.subtitle}>Sign in to continue</p>
        </div>

        {error && (
          <div style={styles.error} role="alert">
            <span aria-hidden="true" style={ui.alertIcon(pwc.error)}>✕</span>
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
