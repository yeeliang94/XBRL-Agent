import { useEffect, useRef, useState, useCallback } from "react";
import { userMessage } from "../lib/errors";
import type { SettingsResponse } from "../lib/types";
import { pwc } from "../lib/theme";
import { ui, uiClass } from "../lib/uiStyles";
import {
  parseThemeOptions,
  type ClipboardFormatOptions,
} from "../lib/clipboardFormat";
import { ClipboardFormatControls } from "./ClipboardFormatControls";

// ---------------------------------------------------------------------------
// GeneralSettingsForm — the model / proxy / API-key + run-defaults form.
//
// This is the body that used to live inside SettingsModal. It was lifted out so
// the same form can render BOTH inside the (legacy) modal overlay AND as the
// "General" tab of the consolidated Settings page (gotcha #7: inline styles).
// The form owns its own load + save + test-connection logic; the host only
// supplies the API helpers and an optional Cancel handler.
// ---------------------------------------------------------------------------

interface Props {
  getSettings: () => Promise<SettingsResponse & { auto_review?: boolean; spot_check?: boolean; spot_check_mode?: string; entity_memory?: boolean; notes_table_style?: Partial<ClipboardFormatOptions> }>;
  saveSettings: (body: Partial<{ api_key: string; model: string; proxy_url: string; auto_review: boolean; spot_check: boolean; spot_check_mode: "light" | "full"; entity_memory: boolean; notes_table_style: ClipboardFormatOptions }>) => Promise<{ status: string }>;
  testConnection: (body: Partial<{ proxy_url: string; api_key: string; model: string }>) => Promise<{ status: string; model?: string; latency_ms?: number; message?: string }>;
  // When provided, a Cancel button is shown (used by the modal wrapper). The
  // page host omits it — there's nothing to cancel out of.
  onCancel?: () => void;
  // AI plumbing is admin-only (Phase 6): non-admins see the fields read-only
  // with a "managed by your administrator" note and no Save. Defaults to true
  // so existing callers (the legacy modal, tests) keep the editable form; the
  // Settings page threads the real value from /api/auth/me.
  isAdmin?: boolean;
}

interface FieldErrors {
  proxyUrl: string | null;
  apiKey: string | null;
  model: string | null;
}

// Pure validators — called both on blur (for immediate feedback) and again
// inside save/test handlers so a user can't bypass validation by pressing
// Enter/clicking before onBlur fires.
export function validate(fields: { proxyUrl: string; apiKey: string; model: string }): FieldErrors {
  return {
    proxyUrl:
      fields.proxyUrl && !fields.proxyUrl.startsWith("https://")
        ? "Proxy URL must start with https://"
        : null,
    apiKey:
      fields.apiKey && fields.apiKey.length < 8 ? "API key too short" : null,
    model: !fields.model.trim() ? "Model name is required" : null,
  };
}

export function hasAnyError(errors: FieldErrors): boolean {
  return !!(errors.proxyUrl || errors.apiKey || errors.model);
}

interface ConnectionResult {
  status: "ok" | "error";
  message: string;
}

const styles = {
  fieldGroup: {
    marginBottom: pwc.space.lg,
  } as React.CSSProperties,
  label: {
    fontFamily: pwc.fontHeading,
    fontWeight: 500,
    fontSize: 14,
    color: pwc.grey700,
    display: "block",
    marginBottom: pwc.space.xs,
  } as React.CSSProperties,
  labelExtra: {
    fontFamily: pwc.fontBody,
    fontWeight: 400,
    color: pwc.grey500,
    marginLeft: pwc.space.sm,
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
  inputMono: {
    width: "100%",
    padding: `${pwc.space.sm}px ${pwc.space.md}px`,
    border: `1px solid ${pwc.grey200}`,
    borderRadius: pwc.radius.md,
    fontFamily: pwc.fontMono,
    fontSize: 13,
    color: pwc.grey900,
    outline: "none",
    boxSizing: "border-box" as const,
  } as React.CSSProperties,
  inputError: {
    borderColor: pwc.error,
  },
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
    marginTop: pwc.space.xs,
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
  cancelButton: {
    ...ui.buttonSecondary,
    ...ui.buttonSm,
  } as React.CSSProperties,
  saveButton: {
    ...ui.buttonPrimary,
    ...ui.buttonSm,
  } as React.CSSProperties,
  testButton: {
    ...ui.buttonSecondary,
    ...ui.buttonSm,
  } as React.CSSProperties,
  testResult: {
    fontFamily: pwc.fontBody,
    fontSize: 13,
    marginTop: pwc.space.sm,
    display: "flex",
    alignItems: "center",
    gap: pwc.space.xs,
  } as React.CSSProperties,
  testSpinner: {
    width: 14,
    height: 14,
    border: `2px solid ${pwc.grey200}`,
    borderTop: `2px solid ${pwc.orange500}`,
    borderRadius: "50%",
    animation: "spin 0.8s linear infinite",
    display: "inline-block",
  } as React.CSSProperties,
  savedBadge: {
    fontFamily: pwc.fontBody,
    fontSize: 13,
    color: pwc.success,
  } as React.CSSProperties,
  loadError: {
    fontFamily: pwc.fontBody,
    fontSize: 14,
    color: pwc.error,
    marginBottom: pwc.space.lg,
  } as React.CSSProperties,
};

export function GeneralSettingsForm({ getSettings, saveSettings, testConnection, onCancel, isAdmin = true }: Props) {
  // Non-admins get a read-only view of the AI plumbing; the server enforces
  // the same boundary (api/config_routes.py), the UI just makes it clear.
  const readOnly = !isAdmin;
  const [model, setModel] = useState("");
  const [proxyUrl, setProxyUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [apiKeyPreview, setApiKeyPreview] = useState("");
  // Reviewer auto-trigger toggle (docs/Archive/PLAN-reviewer-agent.md). Default on.
  const [autoReview, setAutoReview] = useState(true);
  // Clean-run spot-check (issue 1): toggle + depth. Default on / light.
  const [spotCheck, setSpotCheck] = useState(true);
  const [spotCheckMode, setSpotCheckMode] = useState<"light" | "full">("light");
  // Per-entity advisory memory toggle (item 28). Default on.
  const [entityMemory, setEntityMemory] = useState(true);

  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);

  // Displayed errors: only populated after blur to avoid nagging the user
  // mid-type. Submission handlers compute their own live errors separately.
  const [errors, setErrors] = useState<FieldErrors>({ proxyUrl: null, apiKey: null, model: null });
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<ConnectionResult | null>(null);

  const hasErrors = hasAnyError(errors);

  // Track the "Saved!" toast timer so we can clear it on unmount or on a
  // subsequent save, preventing a stale setState call against an unmounted
  // component and overlapping timers racing each other (#28).
  const savedToastTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    return () => {
      if (savedToastTimerRef.current !== null) {
        clearTimeout(savedToastTimerRef.current);
        savedToastTimerRef.current = null;
      }
    };
  }, []);

  // Load current settings once on mount (the host decides when to mount us —
  // the modal mounts on open, the page mounts when the General tab activates).
  useEffect(() => {
    let cancelled = false;
    getSettings()
      .then((s) => {
        if (cancelled) return;
        setModel(s.model);
        setProxyUrl(s.proxy_url);
        setApiKeyPreview(s.api_key_preview);
        setApiKey("");
        // Default to on when the field is absent (older backend).
        setAutoReview(s.auto_review !== false);
        setSpotCheck(s.spot_check !== false);
        setSpotCheckMode(s.spot_check_mode === "full" ? "full" : "light");
        setEntityMemory(s.entity_memory !== false);
      })
      .catch((e) => {
        if (!cancelled) setLoadError(userMessage(e));
      });
    return () => { cancelled = true; };
  }, [getSettings]);

  // --- Blur validation (updates displayed errors) ---
  const validateField = useCallback(
    (field: keyof FieldErrors) => {
      const live = validate({ proxyUrl, apiKey, model });
      setErrors((prev) => ({ ...prev, [field]: live[field] }));
    },
    [proxyUrl, apiKey, model],
  );

  // --- Save ---
  const handleSave = useCallback(async () => {
    // Re-run validation against current values (user may have pressed Enter
    // before blur fired, leaving `errors` stale).
    const live = validate({ proxyUrl, apiKey, model });
    if (hasAnyError(live)) {
      setErrors(live);
      return;
    }
    setSaving(true);
    setLoadError(null);
    try {
      await saveSettings({
        model,
        proxy_url: proxyUrl,
        auto_review: autoReview,
        spot_check: spotCheck,
        spot_check_mode: spotCheckMode,
        entity_memory: entityMemory,
        ...(apiKey ? { api_key: apiKey } : {}),
      });
      setSaved(true);
      if (savedToastTimerRef.current !== null) {
        clearTimeout(savedToastTimerRef.current);
      }
      savedToastTimerRef.current = setTimeout(() => {
        setSaved(false);
        savedToastTimerRef.current = null;
      }, 2000);
    } catch (e) {
      setLoadError(userMessage(e));
    } finally {
      setSaving(false);
    }
  }, [model, proxyUrl, apiKey, autoReview, spotCheck, spotCheckMode, entityMemory, saveSettings]);

  // --- Test connection ---
  const handleTestConnection = useCallback(async () => {
    // Same live revalidation as save — don't test with invalid fields.
    const live = validate({ proxyUrl, apiKey, model });
    if (hasAnyError(live)) {
      setErrors(live);
      return;
    }
    setTesting(true);
    setTestResult(null);
    try {
      const result = await testConnection({
        model,
        proxy_url: proxyUrl,
        ...(apiKey ? { api_key: apiKey } : {}),
      });
      setTestResult({
        status: "ok",
        message: `${result.model} responded in ${result.latency_ms}ms`,
      });
    } catch (e) {
      setTestResult({
        status: "error",
        message: userMessage(e),
      });
    } finally {
      setTesting(false);
    }
  }, [model, proxyUrl, apiKey, testConnection]);

  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === "Enter") {
      e.preventDefault();
      // handleSave does its own validation, so it's safe to call even
      // if the displayed `errors` state is stale.
      handleSave();
    }
  }, [handleSave]);

  return (
    <div onKeyDown={handleKeyDown}>
      {loadError && <p style={styles.loadError}>{loadError}</p>}

      {/* Admin banner — the AI settings are shared, so make the audience of a
          change explicit; non-admins are told they're read-only. */}
      {readOnly ? (
        <div style={ui.alertInfo} role="note">
          <span aria-hidden="true" style={ui.alertIcon(pwc.info)}>ⓘ</span>
          <span>These settings are managed by your administrator.</span>
        </div>
      ) : (
        <div style={{ ...ui.alertInfo, marginBottom: pwc.space.lg }} role="note">
          <span aria-hidden="true" style={ui.alertIcon(pwc.info)}>ⓘ</span>
          <span>These settings apply to everyone using this tool.</span>
        </div>
      )}

      {/* Proxy URL */}
      <div style={styles.fieldGroup}>
        <label style={styles.label}>AI service address</label>
        <input
          type="text"
          value={proxyUrl}
          onChange={(e) => setProxyUrl(e.target.value)}
          onBlur={() => validateField("proxyUrl")}
          placeholder="https://genai-sharedservice-emea.pwc.com"
          // Focus the first field on mount so keyboard users land inside the
          // form, not on whatever was behind it.
          autoFocus={!readOnly}
          disabled={readOnly}
          style={{
            ...ui.input,
            width: "100%",
            ...(errors.proxyUrl ? styles.inputError : {}),
          }}
        />
        {errors.proxyUrl ? (
          <p style={styles.errorText}>{errors.proxyUrl}</p>
        ) : (
          <p style={styles.helperText}>
            The web address of your organisation&apos;s AI service — ask your IT
            team if you&apos;re unsure. Must start with https://.
          </p>
        )}
      </div>

      {/* API Key */}
      <div style={styles.fieldGroup}>
        <label style={styles.label}>
          API Key
          {apiKeyPreview && (
            <span style={styles.labelExtra}>(current: {apiKeyPreview})</span>
          )}
        </label>
        <input
          type="password"
          value={apiKey}
          onChange={(e) => setApiKey(e.target.value)}
          onBlur={() => validateField("apiKey")}
          placeholder={readOnly ? "" : "Enter new API key"}
          disabled={readOnly}
          style={{
            ...ui.input,
            width: "100%",
            ...(errors.apiKey ? styles.inputError : {}),
          }}
        />
        {errors.apiKey ? (
          <p style={styles.errorText}>{errors.apiKey}</p>
        ) : (
          <p style={styles.helperText}>
            The access key for your organisation&apos;s AI service.
          </p>
        )}
      </div>

      {/* Model */}
      <div style={styles.fieldGroup}>
        <label style={styles.label}>Model Name</label>
        <input
          type="text"
          value={model}
          onChange={(e) => setModel(e.target.value)}
          onBlur={() => validateField("model")}
          placeholder="openai.gpt-5.4"
          disabled={readOnly}
          style={{
            ...ui.input,
            width: "100%",
            fontFamily: pwc.fontMono,
            fontSize: 13,
            ...(errors.model ? styles.inputError : {}),
          }}
        />
        {errors.model ? (
          <p style={styles.errorText}>{errors.model}</p>
        ) : (
          <p style={styles.helperText}>e.g., openai.gpt-5.4</p>
        )}
      </div>

      {/* Reviewer auto-trigger toggle */}
      <div style={styles.fieldGroup}>
        <label style={{ display: "flex", alignItems: "center", gap: pwc.space.sm, cursor: "pointer" }}>
          <input
            type="checkbox"
            checked={autoReview}
            onChange={(e) => setAutoReview(e.target.checked)}
            disabled={readOnly}
            aria-label="Automatically run the reviewer after extraction"
          />
          <span style={styles.label}>Automatically run the reviewer after extraction</span>
        </label>
        <p style={styles.helperText}>
          When off, runs with failed cross-checks finish without the reviewer;
          you can still trigger it manually from a run's Review tab.
        </p>
      </div>

      {/* Clean-run spot-check toggle + depth (issue 1) */}
      <div style={styles.fieldGroup}>
        <label style={{ display: "flex", alignItems: "center", gap: pwc.space.sm, cursor: "pointer" }}>
          <input
            type="checkbox"
            checked={spotCheck}
            onChange={(e) => setSpotCheck(e.target.checked)}
            disabled={readOnly}
            aria-label="Spot-check runs even when all cross-checks pass"
          />
          <span style={styles.label}>Spot-check runs even when all cross-checks pass</span>
        </label>
        <p style={styles.helperText}>
          A grounded sanity pass over the high-value figures (face totals, units,
          signs) for runs that pass every cross-check — catching errors the
          checks can&apos;t (wrong value vs the PDF, scale slip, double-count).
        </p>
        <select
          value={spotCheckMode}
          onChange={(e) => setSpotCheckMode(e.target.value === "full" ? "full" : "light")}
          disabled={!spotCheck || readOnly}
          style={{ ...ui.input, opacity: spotCheck ? 1 : 0.5, maxWidth: 320 }}
          aria-label="Spot-check depth"
        >
          <option value="light">Light — fast sanity pass (default)</option>
          <option value="full">Full — holistic reviewer audit</option>
        </select>
        <p style={styles.helperText}>
          Light samples the highest-value figures in a few turns. Full runs the
          same deep reviewer used for failed runs (slower, more thorough).
        </p>
      </div>

      {/* Per-entity advisory memory toggle (item 28) */}
      <div style={styles.fieldGroup}>
        <label style={{ display: "flex", alignItems: "center", gap: pwc.space.sm, cursor: "pointer" }}>
          <input
            type="checkbox"
            checked={entityMemory}
            onChange={(e) => setEntityMemory(e.target.checked)}
            disabled={readOnly}
            aria-label="Reuse prior-year hints for repeat entities"
          />
          <span style={styles.label}>Reuse prior-year hints for repeat entities</span>
        </label>
        <p style={styles.helperText}>
          When a company has been processed before, last year&apos;s format,
          scale (e.g. RM &apos;000), and page positions are shown to the AI as
          hints to double-check against this year&apos;s PDF. Turn this off if
          two different companies share a name.
        </p>
      </div>

      {/* Notes table style — the firm-wide default theme for notes tables
          (docs/PLAN-notes-table-theme.md). Server-side (shared by everyone),
          persisted via /api/settings; it auto-saves on change, independent of
          the form's main Save button below. */}
      <NotesPasteFormatSection getSettings={getSettings} saveSettings={saveSettings} />

      {/* Test Connection — admin-only (it exercises the shared AI plumbing). */}
      {!readOnly && (
      <div style={{ marginBottom: pwc.space.lg }}>
        <button
          onClick={handleTestConnection}
          disabled={testing}
          className={uiClass.btnSecondary}
          style={styles.testButton}
        >
          {testing ? (
            <>
              <span style={styles.testSpinner} /> Testing...
            </>
          ) : (
            "Test Connection"
          )}
        </button>
        {testResult && (
          <div style={styles.testResult}>
            {testResult.status === "ok" ? (
              <>
                <span style={{ color: pwc.success, fontSize: 16 }}>✓</span>
                <span style={{ color: pwc.success }}>{testResult.message}</span>
              </>
            ) : (
              <>
                <span style={{ color: pwc.error, fontSize: 16 }}>✗</span>
                <span style={{ color: pwc.error }}>{testResult.message}</span>
              </>
            )}
          </div>
        )}
      </div>
      )}

      {/* Actions — a non-admin can't save the AI plumbing, so the Save row is
          hidden (a Cancel is still offered when the modal host provides one). */}
      {(!readOnly || onCancel) && (
        <div style={styles.actions}>
          {saved && <span style={styles.savedBadge}>Saved!</span>}
          {onCancel && (
            <button onClick={onCancel} className={uiClass.btnSecondary} style={styles.cancelButton}>
              {readOnly ? "Close" : "Cancel"}
            </button>
          )}
          {!readOnly && (
            <button
              onClick={handleSave}
              disabled={saving || hasErrors}
              className={uiClass.btnPrimary}
              style={styles.saveButton}
            >
              {saving ? "Saving..." : "Save"}
            </button>
          )}
        </div>
      )}
    </div>
  );
}

// Firm-wide notes-table style theme (docs/PLAN-notes-table-theme.md). Unlike
// the old per-browser localStorage paste format, this is the SHARED firm
// default stored server-side (.env via /api/settings) — so the whole firm
// inherits one house style for both the editor preview and the clipboard paste.
// It auto-saves on every change (its own POST), independent of the form's main
// Save button.
function NotesPasteFormatSection({
  getSettings,
  saveSettings,
}: Pick<Props, "getSettings" | "saveSettings">) {
  const [fmt, setFmt] = useState<ClipboardFormatOptions>(() =>
    parseThemeOptions(null),
  );
  const [saveError, setSaveError] = useState<string | null>(null);
  // Last value the SERVER confirmed — restored if a save fails so the UI never
  // shows (or copies) an unsaved theme that a refresh would silently revert
  // (peer-review MEDIUM #5).
  const lastSavedRef = useRef<ClipboardFormatOptions>(parseThemeOptions(null));
  // Debounce so a number input being typed ("1" on the way to "12") doesn't
  // fire a save per keystroke — the unclamped interim "1" would 400, and
  // rapid saves can land out of order (peer-review HIGH #2).
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Seed from the server firm default on mount.
  useEffect(() => {
    let cancelled = false;
    getSettings()
      .then((s) => {
        if (!cancelled) {
          const seeded = parseThemeOptions(s.notes_table_style);
          setFmt(seeded);
          lastSavedRef.current = seeded;
        }
      })
      .catch(() => {
        /* leave the built-in default showing; the save path surfaces errors */
      });
    return () => {
      cancelled = true;
    };
  }, [getSettings]);

  const update = useCallback(
    (next: ClipboardFormatOptions) => {
      setFmt(next); // optimistic — keep the input controlled + preview live
      if (saveTimer.current) clearTimeout(saveTimer.current);
      saveTimer.current = setTimeout(() => {
        // Clamp/validate BEFORE sending so an interim out-of-range value never
        // reaches (and is rejected by) the server.
        const clean = parseThemeOptions(next);
        saveSettings({ notes_table_style: clean })
          .then(() => {
            lastSavedRef.current = clean;
            setSaveError(null);
          })
          .catch(() => {
            setSaveError("Couldn't save the table style — check your connection.");
            setFmt(lastSavedRef.current); // revert to the last confirmed value
          });
      }, 500);
    },
    [saveSettings],
  );

  return (
    <div style={styles.fieldGroup}>
      <label style={styles.label}>Notes table style</label>
      <p style={styles.helperText}>
        The firm default look for notes tables — grid colour, header fill, font,
        spacing. It styles BOTH the on-screen Notes review preview AND what you
        paste into M-Tool, so they match. Shared by everyone; changes save
        automatically (no Save button). You can still override it per run, and
        format individual cells.
      </p>
      {saveError && (
        <p style={{ ...styles.helperText, color: pwc.error ?? "#b00020" }} role="alert">
          {saveError}
        </p>
      )}
      <ClipboardFormatControls value={fmt} onChange={update} idPrefix="settings-fmt" />
    </div>
  );
}
