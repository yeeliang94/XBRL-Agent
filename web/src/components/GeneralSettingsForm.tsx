import { useEffect, useRef, useState, useCallback } from "react";
import { userMessage } from "../lib/errors";
import type { ModelEntry, SettingsResponse } from "../lib/types";
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
  getSettings: () => Promise<SettingsResponse & { auto_review?: boolean; spot_check?: boolean; spot_check_mode?: string; entity_memory?: boolean; notes_table_style?: Partial<ClipboardFormatOptions>; available_models?: ModelEntry[] }>;
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
    color: pwc.grey700,
    marginLeft: pwc.space.sm,
  } as React.CSSProperties,
  // Shared control primitive: 44px targets + perceptible (3:1) boundaries.
  input: {
    ...ui.input,
    width: "100%",
    fontSize: 14,
    boxSizing: "border-box" as const,
  } as React.CSSProperties,
  inputMono: {
    ...ui.input,
    width: "100%",
    fontFamily: pwc.fontMono,
    fontSize: 13,
    boxSizing: "border-box" as const,
  } as React.CSSProperties,
  inputError: {
    borderColor: pwc.error,
  },
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
    marginTop: pwc.space.xs,
  } as React.CSSProperties,
  actions: {
    display: "flex",
    alignItems: "center",
    // Test Connection sits on the left, Save/Cancel group on the right (C4).
    justifyContent: "space-between",
    gap: pwc.space.md,
    marginTop: pwc.space.xl,
    paddingTop: pwc.space.lg,
    borderTop: `1px solid ${pwc.grey200}`,
  } as React.CSSProperties,
  actionsRight: {
    display: "flex",
    alignItems: "center",
    gap: pwc.space.md,
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
    color: pwc.successText,
  } as React.CSSProperties,
  unsavedBadge: {
    fontFamily: pwc.fontBody,
    fontSize: 13,
    color: pwc.grey700,
  } as React.CSSProperties,
  sectionHeading: {
    marginTop: pwc.space.xxl,
    marginBottom: pwc.space.lg,
    paddingTop: pwc.space.lg,
    borderTop: `1px solid ${pwc.grey200}`,
  } as React.CSSProperties,
  sectionTitle: {
    ...ui.sectionTitle,
    margin: 0,
  } as React.CSSProperties,
  sectionDescription: {
    ...ui.supportingText,
    margin: `${pwc.space.xs}px 0 0`,
  } as React.CSSProperties,
  // The auto-saving section — a subtle card with a left rule to set it apart
  // from the Save-button-gated fields (C4).
  autoSaveCard: {
    marginBottom: pwc.space.xl,
    padding: pwc.space.lg,
    background: pwc.grey100,
    borderLeft: `3px solid ${pwc.grey300}`,
    borderRadius: pwc.radius.sm,
  } as React.CSSProperties,
  autoSaveHeader: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    gap: pwc.space.sm,
  } as React.CSSProperties,
  autoSaveChip: {
    fontFamily: pwc.fontBody,
    fontSize: 12,
    fontWeight: pwc.weight.medium,
    color: pwc.successText,
  } as React.CSSProperties,
  notesPreview: {
    maxWidth: 360,
    margin: `${pwc.space.lg}px 0`,
    padding: pwc.space.md,
    background: pwc.white,
    border: `1px solid ${pwc.grey200}`,
  } as React.CSSProperties,
  previewCell: {
    padding: `${pwc.space.xs}px ${pwc.space.sm}px`,
    border: `1px solid ${pwc.grey300}`,
    fontFamily: pwc.fontBody,
    color: pwc.grey900,
  } as React.CSSProperties,
  loadError: {
    fontFamily: pwc.fontBody,
    fontSize: 14,
    color: pwc.errorText,
    marginBottom: pwc.space.lg,
  } as React.CSSProperties,
};

export function GeneralSettingsForm({ getSettings, saveSettings, testConnection, onCancel, isAdmin = true }: Props) {
  // Non-admins get a read-only view of the AI plumbing; the server enforces
  // the same boundary (api/config_routes.py), the UI just makes it clear.
  const readOnly = !isAdmin;
  const [model, setModel] = useState("");
  // Known models from config/models.json (same source the run-config pickers
  // use). When present, the model field is a dropdown instead of typo-prone
  // free text (D4); an empty list falls back to the text input.
  const [availableModels, setAvailableModels] = useState<ModelEntry[]>([]);
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
  const [dirty, setDirty] = useState(false);
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
        if (Array.isArray(s.available_models)) setAvailableModels(s.available_models);
        setDirty(false);
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
    if (!dirty) return;
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
      setDirty(false);
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
  }, [dirty, model, proxyUrl, apiKey, autoReview, spotCheck, spotCheckMode, entityMemory, saveSettings]);

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

      <SettingsSectionHeading
        title="Service connection"
        description="Advanced shared configuration. Changes affect everyone and future runs."
      />
      {/* Proxy URL */}
      <div style={styles.fieldGroup}>
        <label style={styles.label} htmlFor="ai-service-address">AI service address</label>
        <input
          type="url"
          id="ai-service-address"
          name="ai-service-address"
          // A URL field sitting directly above a password field trips the
          // browser's "username + password" login heuristic, which then
          // autofills the saved account email here. Naming it non-credentially
          // and turning autofill off breaks that pairing so the field stays
          // empty until the operator types a real address.
          autoComplete="off"
          value={proxyUrl}
          onChange={(e) => { setProxyUrl(e.target.value); setDirty(true); }}
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
          id="ai-service-api-key"
          name="ai-service-api-key"
          // "new-password" tells password managers this is a value to set, not
          // an existing credential to autofill — so they don't paste the saved
          // login password here (and, paired with the URL field's non-login
          // name above, don't treat the two as a sign-in form).
          autoComplete="new-password"
          value={apiKey}
          onChange={(e) => { setApiKey(e.target.value); setDirty(true); }}
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

      <SettingsSectionHeading
        title="Extraction behaviour"
        description="Choose the model used when a new extraction starts. Existing runs are unchanged."
      />
      {/* Model — a picker of known models (config/models.json) instead of a
          typo-prone free-text field (D4). Falls back to a text input when the
          model list isn't available. */}
      <div style={styles.fieldGroup}>
        <label style={styles.label} htmlFor="settings-model">Model</label>
        {availableModels.length > 0 ? (
          <select
            id="settings-model"
            value={model}
            onChange={(e) => { setModel(e.target.value); setDirty(true); }}
            disabled={readOnly}
            style={{ ...ui.select, width: "100%" }}
          >
            {/* Keep a saved model that isn't in the known list so it isn't
                silently dropped on save. */}
            {model && !availableModels.some((m) => m.id === model) && (
              <option value={model}>{model} (custom)</option>
            )}
            {availableModels.map((m) => (
              <option key={m.id} value={m.id}>
                {m.display_name} ({m.id})
              </option>
            ))}
          </select>
        ) : (
          <input
            type="text"
            id="settings-model"
            value={model}
            onChange={(e) => { setModel(e.target.value); setDirty(true); }}
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
        )}
        {errors.model ? (
          <p style={styles.errorText}>{errors.model}</p>
        ) : (
          <p style={styles.helperText}>
            Which AI model runs the extraction. Ask your team if unsure.
          </p>
        )}
      </div>

      <SettingsSectionHeading
        title="Review behaviour"
        description="These defaults apply to future runs and can increase processing time and usage."
      />
      {/* Reviewer auto-trigger toggle */}
      <div style={styles.fieldGroup}>
        <label style={{ display: "flex", alignItems: "center", gap: pwc.space.sm, cursor: "pointer" }}>
          <input
            type="checkbox"
            checked={autoReview}
            onChange={(e) => { setAutoReview(e.target.checked); setDirty(true); }}
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
            onChange={(e) => { setSpotCheck(e.target.checked); setDirty(true); }}
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
          onChange={(e) => { setSpotCheckMode(e.target.value === "full" ? "full" : "light"); setDirty(true); }}
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

      <SettingsSectionHeading
        title="Prior-year assistance"
        description="Controls whether future runs receive advisory context from the same entity's earlier filings."
      />
      {/* Per-entity advisory memory toggle (item 28) */}
      <div style={styles.fieldGroup}>
        <label style={{ display: "flex", alignItems: "center", gap: pwc.space.sm, cursor: "pointer" }}>
          <input
            type="checkbox"
            checked={entityMemory}
            onChange={(e) => { setEntityMemory(e.target.checked); setDirty(true); }}
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

      <SettingsSectionHeading
        title="Notes appearance"
        description="This section saves independently and updates the shared default immediately."
      />
      {/* Notes table style — the firm-wide default theme for notes tables
          (docs/PLAN-notes-table-theme.md). Server-side (shared by everyone),
          persisted via /api/settings; it auto-saves on change, independent of
          the form's main Save button below. */}
      <NotesPasteFormatSection getSettings={getSettings} saveSettings={saveSettings} />

      {/* Test-connection result — shown above the action row (which holds the
          Test Connection button itself, admin-only). */}
      {!readOnly && testResult && (
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

      {/* One action row: Test Connection on the left, Save/Cancel on the right,
          so the primary controls aren't scattered across the form (C4). A
          non-admin can't save the AI plumbing, so Test Connection + Save are
          hidden (a Cancel is still offered when the modal host provides one). */}
      {(!readOnly || onCancel) && (
        <div style={styles.actions}>
          {!readOnly ? (
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
          ) : (
            <span />
          )}
          <div style={styles.actionsRight}>
            {dirty && !saving && <span style={styles.unsavedBadge} role="status">Unsaved changes</span>}
            {saved && <span style={styles.savedBadge} role="status" aria-live="polite">Saved</span>}
            {onCancel && (
              <button onClick={onCancel} className={uiClass.btnSecondary} style={styles.cancelButton}>
                {readOnly ? "Close" : "Cancel"}
              </button>
            )}
            {!readOnly && (
              <button
                onClick={handleSave}
                disabled={saving || hasErrors || !dirty}
                className={uiClass.btnPrimary}
                style={styles.saveButton}
              >
                {saving ? "Saving…" : "Save shared settings"}
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function SettingsSectionHeading({ title, description }: { title: string; description: string }) {
  return (
    <div style={styles.sectionHeading}>
      <h3 style={styles.sectionTitle}>{title}</h3>
      <p style={styles.sectionDescription}>{description}</p>
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
  // Transient "Saved" confirmation so the auto-save is VISIBLE — otherwise the
  // user can't tell this section persists on change while the rest of the form
  // waits for the Save button (the "mixed save model" confusion, C4).
  const [justSaved, setJustSaved] = useState(false);
  const [savingAppearance, setSavingAppearance] = useState(false);
  const savedTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
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
      setSavingAppearance(true);
      if (saveTimer.current) clearTimeout(saveTimer.current);
      saveTimer.current = setTimeout(() => {
        // Clamp/validate BEFORE sending so an interim out-of-range value never
        // reaches (and is rejected by) the server.
        const clean = parseThemeOptions(next);
        saveSettings({ notes_table_style: clean })
          .then(() => {
            lastSavedRef.current = clean;
            setSaveError(null);
            setSavingAppearance(false);
            // Flash a brief "Saved" so the auto-save is legible.
            setJustSaved(true);
            if (savedTimer.current) clearTimeout(savedTimer.current);
            savedTimer.current = setTimeout(() => setJustSaved(false), 2000);
          })
          .catch(() => {
            setSavingAppearance(false);
            setSaveError("Couldn't save the table style — check your connection.");
            setFmt(lastSavedRef.current); // revert to the last confirmed value
          });
      }, 500);
    },
    [saveSettings],
  );

  // Clear pending timers on unmount so a late setState (the "Saved" flash or a
  // still-pending debounced save) can't fire against an unmounted section
  // (peer-review LOW). Refs, so this runs once.
  useEffect(() => {
    return () => {
      if (savedTimer.current) clearTimeout(savedTimer.current);
      if (saveTimer.current) clearTimeout(saveTimer.current);
    };
  }, []);

  return (
    // Card + left rule visually mark this section as the one that AUTO-SAVES,
    // so it's clearly distinct from the Save-button-gated fields around it (C4).
    <div style={styles.autoSaveCard}>
      <div style={styles.autoSaveHeader}>
        <label style={styles.label}>Notes table style</label>
        <span
          style={{
            ...styles.autoSaveChip,
            visibility: savingAppearance || justSaved ? "visible" : "hidden",
          }}
          role="status"
          aria-live="polite"
        >
          {savingAppearance ? "Saving…" : "Saved"}
        </span>
      </div>
      <p style={styles.helperText}>
        The firm default look for notes tables — grid colour, header fill, font,
        spacing. It styles BOTH the on-screen Notes review preview AND what you
        paste into M-Tool, so they match. Shared by everyone; changes save
        automatically — no Save button needed for this section. You can still
        override it per run, and format individual cells.
      </p>
      {saveError && (
        <p style={{ ...styles.helperText, color: pwc.error ?? "#b00020" }} role="alert">
          {saveError}
        </p>
      )}
      <div style={styles.notesPreview} aria-label="Notes table style preview">
        <table
          style={{
            width: "100%",
            borderCollapse: "collapse",
            fontSize: `${fmt.fontSizePt}pt`,
            border: fmt.borderStyle === "none"
              ? "none"
              : `${fmt.borderStyle === "double" ? 3 : 1}px ${fmt.borderStyle === "double" ? "double" : "solid"} ${fmt.borderColor || pwc.grey300}`,
          }}
        >
          <thead>
            <tr style={{ background: fmt.headerFill === "transparent" ? pwc.white : (fmt.headerFill || pwc.grey100) }}>
              <th style={styles.previewCell}>Revenue</th>
              <th style={{ ...styles.previewCell, textAlign: "right" }}>2025</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td style={styles.previewCell}>Contract income</td>
              <td style={{ ...styles.previewCell, textAlign: "right", fontVariantNumeric: "tabular-nums" }}>1,250,000</td>
            </tr>
          </tbody>
        </table>
      </div>
      <ClipboardFormatControls value={fmt} onChange={update} idPrefix="settings-fmt" />
    </div>
  );
}
