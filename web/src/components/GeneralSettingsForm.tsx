import { useEffect, useRef, useState, useCallback } from "react";
import { userMessage } from "../lib/errors";
import type {
  ModelEntry,
  SettingsResponse,
  SourceIntegrityMode,
} from "../lib/types";
import { pwc } from "../lib/theme";
import { ui, uiClass } from "../lib/uiStyles";
import { STATUS_SYMBOLS } from "../lib/runStatus";
import { StatusIcon } from "./StatusIcon";
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
  getSettings: () => Promise<SettingsResponse & { auto_review?: boolean; notes_auto_review?: boolean; notes_coverage?: boolean; tolerance_rm?: number; spot_check?: boolean; spot_check_mode?: string; entity_memory?: boolean; pdf_sidecar?: boolean; pdf_notes_auto_format?: boolean; notes_source_integrity?: SourceIntegrityMode; notes_source_integrity_choices?: string[]; default_models?: Record<string, string>; default_model_overrides?: Record<string, string>; local_override_keys?: string[]; thinking_levels?: Record<string, string>; thinking_level_choices?: string[]; thinking_level_choices_by_model?: Record<string, string[]>; notes_table_style?: Partial<ClipboardFormatOptions>; available_models?: ModelEntry[] }>;
  saveSettings: (body: Partial<{ api_key: string; model: string; proxy_url: string; default_models: Record<string, string>; reset_keys: string[]; auto_review: boolean; notes_auto_review: boolean; notes_coverage: boolean; spot_check: boolean; spot_check_mode: "light" | "full"; entity_memory: boolean; pdf_sidecar: boolean; pdf_notes_auto_format: boolean; notes_source_integrity: SourceIntegrityMode; tolerance_rm: number; scout_wallclock_seconds: number; scout_max_turns: number; thinking_levels: Record<string, string>; notes_table_style: ClipboardFormatOptions }>) => Promise<{ status: string }>;
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

// The roles a thinking level can be set for, in the order they run. Labels
// are the operator's words, not the internal role keys.
const THINKING_ROLES: { key: string; label: string; hint: string }[] = [
  { key: "scout", label: "Scout", hint: "finds the pages — rarely needs depth" },
  { key: "SOFP", label: "Statement of financial position", hint: "" },
  { key: "SOPL", label: "Income statement", hint: "" },
  { key: "SOCI", label: "Comprehensive income", hint: "" },
  { key: "SOCF", label: "Cash flows", hint: "" },
  { key: "SOCIE", label: "Changes in equity", hint: "" },
  { key: "reviewer", label: "Reviewer", hint: "traces figures back to the PDF" },
  { key: "notes_reviewer", label: "Notes reviewer", hint: "" },
  { key: "notes_formatter", label: "Notes formatter", hint: "styling only" },
  // The five notes-EXTRACTION roles. These keys MUST be the NotesTemplateType
  // VALUES (notes_types.py) — `notes/agent.py` resolves its level with
  // `template_type.value`, and `/api/settings` validates against the same set.
  // They were first written in the CLI's spelling (`corporate_info`, from
  // `run.py --notes`), which is a different vocabulary: the PATCH 400'd on the
  // key name before it reached the empty-value skip, so EVERY save from this
  // form failed, not just these five rows (2026-08-03).
  { key: "CORP_INFO", label: "Notes: corporate information", hint: "" },
  { key: "ACC_POLICIES", label: "Notes: accounting policies", hint: "usually the longest note" },
  { key: "LIST_OF_NOTES", label: "Notes: the numbered notes", hint: "the bulk of the prose" },
  { key: "ISSUED_CAPITAL", label: "Notes: issued capital", hint: "" },
  { key: "RELATED_PARTY", label: "Notes: related party", hint: "" },
];

// Plain-language labels for the Word-source modes. The SERVER owns the list of
// modes (`notes_source_integrity_choices`); this map only supplies wording for
// the ones we have words for, so an unrecognised mode still renders — as its
// raw value — instead of vanishing from the picker and being written back as
// `off` on the next save (peer review 2026-08-04).
const SOURCE_INTEGRITY_LABELS: Record<string, string> = {
  off: "Off — agents write notes from their own reading (default)",
  shadow: "Measure only — source-built notes; the verdict never affects run status",
  enforce: "On — source-built notes; unused content flags the run",
};

// Used only when a backend predates `notes_source_integrity_choices`.
const SOURCE_INTEGRITY_FALLBACK = ["off", "shadow", "enforce"];

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
  thinkingRow: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    gap: pwc.space.sm,
    padding: "5px 0",
  } as React.CSSProperties,
  thinkingRoleLabel: {
    display: "flex",
    flexDirection: "column",
    fontSize: 14,
    color: pwc.grey900,
  } as React.CSSProperties,
  thinkingHint: {
    fontSize: 12, color: pwc.grey700,
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
  // The auto-saving section uses one quiet surface and neutral boundary.
  autoSaveCard: {
    marginBottom: pwc.space.xl,
    padding: pwc.space.lg,
    background: pwc.grey100,
    border: "none",
    borderRadius: 0,
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
  const [notesAutoReview, setNotesAutoReview] = useState(true);
  const [notesCoverage, setNotesCoverage] = useState(true);
  const [toleranceRm, setToleranceRm] = useState<number | "">(1);
  const [scoutWallclockSeconds, setScoutWallclockSeconds] =
    useState<number | "">(300);
  const [scoutMaxTurns, setScoutMaxTurns] = useState<number | "">(20);
  // Clean-run spot-check (issue 1): toggle + depth. Default on / light.
  const [spotCheck, setSpotCheck] = useState(true);
  const [spotCheckMode, setSpotCheckMode] = useState<"light" | "full">("light");
  // Per-entity advisory memory toggle (item 28). Default on.
  // Per-role thinking level. An absent role sends nothing, which is what
  // every agent did before this setting existed.
  const [thinkingLevels, setThinkingLevels] =
    useState<Record<string, string>>({});
  const [defaultModels, setDefaultModels] =
    useState<Record<string, string>>({});
  const [roleModelUpdates, setRoleModelUpdates] =
    useState<Record<string, string>>({});
  const [localOverrideKeys, setLocalOverrideKeys] = useState<Set<string>>(new Set());
  const [showRoleModels, setShowRoleModels] = useState(false);
  const [levelChoices, setLevelChoices] = useState<string[]>([]);
  // Per-model vocabulary. GPT-5.6 dropped `minimal`, so offering it there
  // means the operator picks a level the run then substitutes — the picker
  // must narrow to what the selected model accepts (peer review 2026-08-02).
  const [levelChoicesByModel, setLevelChoicesByModel] =
    useState<Record<string, string[]>>({});
  const [entityMemory, setEntityMemory] = useState(true);
  // Scanned-PDF source transcript (docs/PLAN-pdf-source-sidecar.md). Default
  // OFF: it adds one paid vision call per notes page, so an admin turns it
  // on deliberately rather than every scanned upload paying for it.
  const [pdfSidecar, setPdfSidecar] = useState(false);
  const [pdfNotesAutoFormat, setPdfNotesAutoFormat] = useState(false);
  // Notes source-integrity rollout mode (gotcha #31). Default off — `shadow`
  // computes the verdict and changes nothing, `enforce` makes the block-id
  // path live and lets an unresolved block tip the run status.
  const [sourceIntegrity, setSourceIntegrity] =
    useState<SourceIntegrityMode>("off");
  // Server-published vocabulary. Never hardcoded into the picker: a mode this
  // build doesn't know about must still be selectable and, above all, must
  // survive a save untouched.
  const [integrityChoices, setIntegrityChoices] =
    useState<string[]>(SOURCE_INTEGRITY_FALLBACK);

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
        setNotesAutoReview(s.notes_auto_review !== false);
        setNotesCoverage(s.notes_coverage !== false);
        setToleranceRm(typeof s.tolerance_rm === "number" ? s.tolerance_rm : 1);
        setScoutWallclockSeconds(
          typeof s.scout_wallclock_seconds === "number"
            ? s.scout_wallclock_seconds
            : 300,
        );
        setScoutMaxTurns(
          typeof s.scout_max_turns === "number" ? s.scout_max_turns : 20,
        );
        setSpotCheck(s.spot_check !== false);
        setSpotCheckMode(s.spot_check_mode === "full" ? "full" : "light");
        setThinkingLevels(s.thinking_levels || {});
        setDefaultModels(s.default_model_overrides || s.default_models || {});
        setLocalOverrideKeys(new Set(s.local_override_keys || []));
        setLevelChoices(s.thinking_level_choices || []);
        setLevelChoicesByModel(s.thinking_level_choices_by_model || {});
        setEntityMemory(s.entity_memory !== false);
        // Default to OFF when the field is absent (older backend) — the
        // opposite of the other toggles, because this one costs money.
        setPdfSidecar(s.pdf_sidecar === true);
        setPdfNotesAutoFormat(s.pdf_notes_auto_format === true);
        // The server's own list decides what is valid — this build's knowledge
        // of the modes does not. Keep whatever mode it reports as long as it is
        // in that list; only an absent or genuinely out-of-list value falls
        // back to `off`, which is also the shipped default.
        const choices =
          Array.isArray(s.notes_source_integrity_choices) &&
          s.notes_source_integrity_choices.length > 0
            ? s.notes_source_integrity_choices
            : SOURCE_INTEGRITY_FALLBACK;
        setIntegrityChoices(choices);
        setSourceIntegrity(
          s.notes_source_integrity && choices.includes(s.notes_source_integrity)
            ? s.notes_source_integrity
            : "off",
        );
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
    if (toleranceRm === "") {
      setLoadError("Enter a cross-check tolerance of 0 or more before saving.");
      return;
    }
    if (
      scoutWallclockSeconds === "" ||
      !Number.isFinite(scoutWallclockSeconds) ||
      scoutWallclockSeconds < 0
    ) {
      setLoadError("Enter a Scout wall-clock timeout of 0 or more before saving.");
      return;
    }
    if (
      scoutMaxTurns === "" ||
      !Number.isInteger(scoutMaxTurns) ||
      scoutMaxTurns < 1 ||
      scoutMaxTurns > 40
    ) {
      setLoadError("Enter a maximum Scout turn count between 1 and 40 before saving.");
      return;
    }
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
        ...(Object.keys(roleModelUpdates).length > 0
          ? { default_models: roleModelUpdates }
          : {}),
        auto_review: autoReview,
        notes_auto_review: notesAutoReview,
        notes_coverage: notesCoverage,
        tolerance_rm: toleranceRm,
        scout_wallclock_seconds: scoutWallclockSeconds,
        scout_max_turns: scoutMaxTurns,
        spot_check: spotCheck,
        spot_check_mode: spotCheckMode,
        entity_memory: entityMemory,
        pdf_sidecar: pdfSidecar,
        pdf_notes_auto_format: pdfNotesAutoFormat,
        notes_source_integrity: sourceIntegrity,
        // Send EVERY role, with "" for the ones set back to the provider
        // default. The server clears only the keys it is given, so omitting a
        // cleared role would leave its old level active — and omitting the
        // field entirely (the first version of this) meant the control saved
        // nothing at all while the form still said "Saved".
        thinking_levels: Object.fromEntries(
          THINKING_ROLES.map(({ key }) => [key, thinkingLevels[key] || ""]),
        ),
        ...(apiKey ? { api_key: apiKey } : {}),
      });
      setRoleModelUpdates({});
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
  }, [dirty, model, proxyUrl, apiKey, roleModelUpdates, autoReview, notesAutoReview, notesCoverage, toleranceRm, scoutWallclockSeconds, scoutMaxTurns, spotCheck, spotCheckMode, entityMemory, pdfSidecar, pdfNotesAutoFormat, sourceIntegrity, thinkingLevels, saveSettings]);

  const handleUseDeploymentDefault = useCallback(async (key: "model" | "proxy_url" | "api_key") => {
    setSaving(true);
    setLoadError(null);
    try {
      await saveSettings({ reset_keys: [key] });
      const fresh = await getSettings();
      setModel(fresh.model);
      setProxyUrl(fresh.proxy_url);
      setApiKeyPreview(fresh.api_key_preview);
      setApiKey("");
      setDefaultModels(fresh.default_model_overrides || fresh.default_models || {});
      setLocalOverrideKeys(new Set(fresh.local_override_keys || []));
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
  }, [getSettings, saveSettings]);

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
        {localOverrideKeys.has("proxy_url") && (
          <button
            type="button"
            onClick={() => void handleUseDeploymentDefault("proxy_url")}
            disabled={readOnly || saving}
            style={{ ...ui.buttonSecondary, ...ui.buttonSm, alignSelf: "flex-start" }}
          >
            Use deployment service address
          </button>
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
        {localOverrideKeys.has("api_key") && (
          <button
            type="button"
            onClick={() => void handleUseDeploymentDefault("api_key")}
            disabled={readOnly || saving}
            style={{ ...ui.buttonSecondary, ...ui.buttonSm, alignSelf: "flex-start" }}
          >
            Use deployment access key
          </button>
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
        {localOverrideKeys.has("model") && (
          <button
            type="button"
            onClick={() => void handleUseDeploymentDefault("model")}
            disabled={readOnly || saving}
            style={{ ...ui.buttonSecondary, ...ui.buttonSm, alignSelf: "flex-start" }}
          >
            Follow deployment model
          </button>
        )}
      </div>

      <div style={styles.fieldGroup}>
        <label style={styles.label}>Model by pipeline role</label>
        <p style={styles.helperText}>
          Most runs use the model above. Open this only when a pipeline role
          needs a different default.
        </p>
        <button
          type="button"
          aria-expanded={showRoleModels}
          aria-controls="role-model-defaults"
          onClick={() => setShowRoleModels((open) => !open)}
          style={{ ...ui.buttonSecondary, ...ui.buttonSm, alignSelf: "flex-start" }}
        >
          {showRoleModels ? "Hide role-specific models" : "Customize role-specific models"}
        </button>
        {showRoleModels && (
          <div id="role-model-defaults">
            {THINKING_ROLES.map(({ key, label }) => {
              const selected = defaultModels[key] || "";
              return (
                <div key={`model-${key}`} style={styles.thinkingRow}>
                  <span style={styles.thinkingRoleLabel}>{label}</span>
                  <select
                    aria-label={`Default model for ${label}`}
                    value={selected}
                    disabled={readOnly}
                    onChange={(e) => {
                      setDefaultModels((prev) => ({ ...prev, [key]: e.target.value }));
                      setRoleModelUpdates((prev) => ({ ...prev, [key]: e.target.value }));
                      setDirty(true);
                    }}
                    style={{ ...ui.select, width: 260 }}
                  >
                    <option value="">Follow global model ({model})</option>
                    {selected && !availableModels.some((m) => m.id === selected) && (
                      <option value={selected}>{selected} (custom)</option>
                    )}
                    {availableModels.map((m) => (
                      <option key={m.id} value={m.id}>{m.display_name || m.id}</option>
                    ))}
                  </select>
                </div>
              );
            })}
          </div>
        )}
      </div>

      <SettingsSectionHeading
        title="Scout limits"
        description="Set how long the document scan may run before extraction continues without its page hints."
      />
      <div style={{ display: "flex", flexWrap: "wrap", gap: pwc.space.xl }}>
        <div style={{ ...styles.fieldGroup, flex: "1 1 220px" }}>
          <label style={styles.label} htmlFor="scout-wallclock-seconds">
            Wall-clock timeout (seconds)
          </label>
          <input
            id="scout-wallclock-seconds"
            type="number"
            min={0}
            step={1}
            value={scoutWallclockSeconds}
            disabled={readOnly}
            onChange={(e) => {
              if (e.target.value === "") {
                setScoutWallclockSeconds("");
              } else {
                const next = Number(e.target.value);
                if (Number.isFinite(next)) setScoutWallclockSeconds(next);
              }
              setDirty(true);
            }}
            style={{ ...ui.input, width: "100%", maxWidth: 240 }}
          />
          <p style={styles.helperText}>
            Default: 300 seconds. Increase this for long or scanned filings.
            Enter 0 to remove the overall Scout deadline; the per-turn timeout
            still applies.
          </p>
        </div>

        <div style={{ ...styles.fieldGroup, flex: "1 1 220px" }}>
          <label style={styles.label} htmlFor="scout-max-turns">
            Maximum turns
          </label>
          <input
            id="scout-max-turns"
            type="number"
            min={1}
            max={40}
            step={1}
            value={scoutMaxTurns}
            disabled={readOnly}
            onChange={(e) => {
              if (e.target.value === "") {
                setScoutMaxTurns("");
              } else {
                const next = Number(e.target.value);
                if (Number.isFinite(next)) setScoutMaxTurns(next);
              }
              setDirty(true);
            }}
            style={{ ...ui.input, width: "100%", maxWidth: 240 }}
          />
          <p style={styles.helperText}>
            Default: 20 model responses. The safe maximum is 40 so Scout stops
            before the model framework&apos;s internal 50-request limit.
          </p>
        </div>
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

      <div style={styles.fieldGroup}>
        <label style={{ display: "flex", alignItems: "center", gap: pwc.space.sm, cursor: "pointer" }}>
          <input
            type="checkbox"
            checked={notesAutoReview}
            onChange={(e) => { setNotesAutoReview(e.target.checked); setDirty(true); }}
            disabled={readOnly}
            aria-label="Automatically review extracted notes"
          />
          <span style={styles.label}>Automatically review extracted notes</span>
        </label>
        <p style={styles.helperText}>
          Checks prose notes after extraction and applies grounded corrections.
        </p>
      </div>

      <div style={styles.fieldGroup}>
        <label style={{ display: "flex", alignItems: "center", gap: pwc.space.sm, cursor: "pointer" }}>
          <input
            type="checkbox"
            checked={notesCoverage}
            onChange={(e) => { setNotesCoverage(e.target.checked); setDirty(true); }}
            disabled={readOnly}
            aria-label="Check notes coverage against the document inventory"
          />
          <span style={styles.label}>Check notes coverage against the document inventory</span>
        </label>
      </div>

      <div style={styles.fieldGroup}>
        <label style={styles.label} htmlFor="cross-check-tolerance">Cross-check tolerance (RM)</label>
        <input
          id="cross-check-tolerance"
          type="number"
          min={0}
          step="0.01"
          value={toleranceRm}
          disabled={readOnly}
          onChange={(e) => {
            if (e.target.value === "") {
              setToleranceRm("");
            } else {
              const next = Number(e.target.value);
              if (Number.isFinite(next) && next >= 0) setToleranceRm(next);
            }
            setDirty(true);
          }}
          style={{ ...ui.input, width: 180 }}
        />
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
        title="Word source handling"
        description="Controls how notes content is built when a filing is uploaded as a Word document."
      />
      {/* Notes source-integrity rollout mode (gotcha #31). Three values, not a
          checkbox: `shadow` exists precisely so the verdict can be computed and
          compared before anything changes. */}
      <div style={styles.fieldGroup}>
        <label style={styles.label} htmlFor="notes-source-integrity">
          Build notes from the Word source
        </label>
        <select
          id="notes-source-integrity"
          value={sourceIntegrity}
          onChange={(e) => {
            setSourceIntegrity(e.target.value as SourceIntegrityMode);
            setDirty(true);
          }}
          disabled={readOnly}
          style={{ ...ui.input, width: "100%", maxWidth: 420 }}
          aria-label="Word source handling mode"
        >
          {integrityChoices.map((mode) => (
            <option key={mode} value={mode}>
              {SOURCE_INTEGRITY_LABELS[mode] ?? mode}
            </option>
          ))}
        </select>
        <p style={styles.helperText}>
          Off leaves each agent to write every note in its own output, which is
          where formatting and fidelity are lost. Measure only and On both
          change how notes are extracted: agents are instructed to assemble
          each note directly from the Word document&apos;s own text. The
          difference is what the coverage verdict does — under Measure only it
          is recorded but never affects the run&apos;s status; under On, unused
          source content marks the run as needing review. Notes the document
          doesn&apos;t cover, and the two figure sheets (Issued Capital,
          Related Party), are still written the ordinary way in every mode.
          Agents are instructed, not forced — the run&apos;s source-coverage
          report shows what they actually did.
        </p>
        {sourceIntegrity === "enforce" && (
          <p style={{ ...styles.helperText, color: pwc.grey700, fontWeight: 500 }}>
            This can affect a run&apos;s status. The source-built workflow has
            not yet been validated on a live filing — run &quot;Measure
            only&quot; on the same document first.
          </p>
        )}
        <p style={styles.helperText}>
          Word uploads only. PDF filings are unaffected by this setting.
        </p>
      </div>

      <SettingsSectionHeading
        title="Scanned PDF handling"
        description="Controls whether scanned (image-only) PDFs get a transcript for the notes agents to copy from."
      />
      {/* Scanned-PDF source transcript toggle (docs/PLAN-pdf-source-sidecar.md).
          A checkbox, not a mode picker: the pass either runs or it doesn't.
          Admin-only server-side (it changes cost for everyone). */}
      <div style={styles.fieldGroup}>
        <label style={{ display: "flex", alignItems: "center", gap: pwc.space.sm, cursor: "pointer" }}>
          <input
            type="checkbox"
            checked={pdfSidecar}
            onChange={(e) => { setPdfSidecar(e.target.checked); setDirty(true); }}
            disabled={readOnly}
            aria-label="Transcribe scanned PDF notes pages before extraction"
          />
          <span style={styles.label}>Transcribe scanned PDF notes pages before extraction</span>
        </label>
        <p style={styles.helperText}>
          When a filing is uploaded as a scanned PDF (no selectable text), the
          notes pages are first read by the AI into a text transcript with the
          tables and rules kept. Notes agents then copy tables and layout from
          that transcript the way they do from a Word source, instead of
          re-describing what they see. Figures in the transcript are treated
          as unverified — agents are told to check every number against the
          PDF. Adds one image-reading call per notes page (roughly a quarter
          of a US dollar for a 20-page notes section). If the transcript
          cannot be built the run continues as before, and the run page says
          why.
        </p>
        <p style={styles.helperText}>
          Scanned PDFs only. PDFs with selectable text and Word uploads are
          unaffected by this setting.
        </p>
      </div>
      <div style={styles.fieldGroup}>
        <label style={{ display: "flex", alignItems: "center", gap: pwc.space.sm, cursor: "pointer" }}>
          <input
            type="checkbox"
            checked={pdfNotesAutoFormat}
            onChange={(e) => { setPdfNotesAutoFormat(e.target.checked); setDirty(true); }}
            disabled={readOnly}
            aria-label="Automatically format PDF notes for mTool"
          />
          <span style={styles.label}>Automatically format PDF notes for mTool</span>
        </label>
        <p style={styles.helperText}>
          After extraction and notes review, the AI formatter compares each
          prose table with its PDF pages and applies the standard mTool-safe
          profile: source-aware borders and totals rules, restrained fills,
          and consistent numeric alignment. Scanned and selectable-text PDFs
          use the same path. Word uploads are not changed.
        </p>
        <p style={styles.helperText}>
          Adds one paid formatting pass per filled prose sheet. Content, figures,
          table rows and columns are locked by the format-only verifier.
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

      {/* Thinking level, per agent role. Never set before this — every model
          ran at its provider default. An empty selection sends nothing, which
          keeps that behaviour, so the control is additive rather than a
          silent migration. Per role rather than global because scout is
          navigation and extraction is judgement; one level would overpay on
          one or underpower the other. */}
      <div style={styles.fieldGroup}>
        <label style={styles.label}>Thinking level</label>
        <p style={styles.helperText}>
          How much reasoning each part of the run does before answering.
          Leave a row on <strong>Provider default</strong> to keep today&apos;s
          behaviour. Higher levels cost more and take longer — thinking is
          billed at the output rate.
        </p>
        {THINKING_ROLES.map(({ key, label, hint }) => {
          const roleModel = defaultModels[key] || model;
          const choices = levelChoicesByModel[roleModel] || levelChoices;
          const selected = thinkingLevels[key] || "";
          const renderedChoices = selected && !choices.includes(selected)
            ? [selected, ...choices]
            : choices;
          return <div key={key} style={styles.thinkingRow}>
            <span style={styles.thinkingRoleLabel}>
              {label}
              <span style={styles.thinkingHint}>{hint}</span>
            </span>
            <select
              id={`thinking-${key}`}
              aria-label={`Thinking level for ${label}`}
              value={selected}
              disabled={readOnly}
              onChange={(e) => {
                const next = { ...thinkingLevels };
                if (e.target.value) next[key] = e.target.value;
                else delete next[key];
                setThinkingLevels(next);
                setDirty(true);
              }}
              style={{ ...ui.select, width: 190 }}
            >
              <option value="">Provider default</option>
              {renderedChoices.map((lvl) => (
                <option key={lvl} value={lvl}>
                  {lvl.charAt(0).toUpperCase() + lvl.slice(1)}
                </option>
              ))}
            </select>
          </div>
        })}
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
              <StatusIcon symbol={STATUS_SYMBOLS.success} size={16} style={{ color: pwc.success }} />
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
                  <span className="pwc-spinner" style={styles.testSpinner} /> Testing...
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
// default stored server-side (the local runtime settings file via the API) — so the whole firm
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

  // Cell edges derived from the theme, exactly as the editor/clipboard derive
  // them: no grid when borderStyle is "none"; the header rule (when on) is a
  // bottom edge on <th> only.
  const previewGrid =
    fmt.borderStyle === "none"
      ? undefined
      : `${fmt.borderStyle === "double" ? 3 : 1}px ${
          fmt.borderStyle === "double" ? "double" : "solid"
        } ${fmt.borderColor || pwc.grey300}`;
  const previewBodyCell: React.CSSProperties = {
    ...styles.previewCell,
    border: previewGrid ?? "none",
  };
  const previewHeaderCell: React.CSSProperties = {
    ...previewBodyCell,
    fontWeight: fmt.headerBold === false ? 400 : 600,
    ...(fmt.headerRule
      ? { borderBottom: `1px solid ${fmt.borderColor || "#999"}` }
      : {}),
  };

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
      {/* The preview must show what SAVING produces, not a fixed grid: it is the
        * only place an operator sees the theme before committing it. It used to
        * hard-code a 1px cell border, so the ruled house default (no grid, one
        * rule under the header) previewed as boxed — the opposite of the output.
        * Border resolution mirrors themeToCssVars + notes_decorate._header_extra. */}
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
              <th style={previewHeaderCell}>Revenue</th>
              <th style={{ ...previewHeaderCell, textAlign: "right" }}>2025</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td style={previewBodyCell}>Contract income</td>
              <td style={{ ...previewBodyCell, textAlign: "right", fontVariantNumeric: "tabular-nums" }}>1,250,000</td>
            </tr>
          </tbody>
        </table>
      </div>
      <ClipboardFormatControls value={fmt} onChange={update} idPrefix="settings-fmt" />
    </div>
  );
}
