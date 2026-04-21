import React, { useState, useEffect, useCallback, useMemo } from "react";
import type {
  StatementType,
  VariantSelection,
  ExtendedSettingsResponse,
  ModelEntry,
  RunConfigPayload,
  FilingLevel,
  NotesTemplateType,
  SSEEvent,
} from "../lib/types";
import {
  STATEMENT_TYPES,
  mapStatements,
  NOTES_TEMPLATE_TYPES,
} from "../lib/types";
import { pwc } from "../lib/theme";
import { abortAgent, updateSettings } from "../lib/api";
import { VariantSelector } from "./VariantSelector";
import { ScoutToggle } from "./ScoutToggle";
import { StatementRunConfig } from "./StatementRunConfig";
import { NotesRunConfig } from "./NotesRunConfig";
import { AgentTimeline } from "./AgentTimeline";
import { humanToolName } from "../lib/toolLabels";
import { parseSSEStream } from "../lib/sse";
import { buildToolTimeline, isScoutTimelineEvent } from "../lib/buildToolTimeline";

interface Props {
  sessionId: string;
  getSettings: () => Promise<ExtendedSettingsResponse>;
  onRun: (config: RunConfigPayload) => void;
}

const styles = {
  container: {
    background: pwc.white,
    borderRadius: pwc.radius.md,
    border: `1px solid ${pwc.grey200}`,
    boxShadow: pwc.shadow.card,
    padding: pwc.space.xl,
    display: "flex",
    flexDirection: "column" as const,
    gap: pwc.space.xl,
  } as React.CSSProperties,
  heading: {
    fontFamily: pwc.fontHeading,
    fontWeight: 600,
    fontSize: 16,
    color: pwc.grey900,
    margin: 0,
  } as React.CSSProperties,
  section: {
    display: "flex",
    flexDirection: "column" as const,
    gap: pwc.space.md,
  } as React.CSSProperties,
  sectionLabel: {
    fontFamily: pwc.fontHeading,
    fontWeight: 500,
    fontSize: 13,
    color: pwc.grey500,
    textTransform: "uppercase" as const,
    letterSpacing: 0.5,
  } as React.CSSProperties,
  divider: {
    height: 1,
    background: pwc.grey200,
    border: "none",
    margin: 0,
  } as React.CSSProperties,
  runButton: {
    padding: `${pwc.space.md}px ${pwc.space.xl}px`,
    fontFamily: pwc.fontHeading,
    fontSize: 15,
    fontWeight: 600,
    color: pwc.white,
    background: pwc.orange500,
    border: "none",
    borderRadius: pwc.radius.md,
    cursor: "pointer",
    alignSelf: "flex-end" as const,
  } as React.CSSProperties,
  runButtonDisabled: {
    padding: `${pwc.space.md}px ${pwc.space.xl}px`,
    fontFamily: pwc.fontHeading,
    fontSize: 15,
    fontWeight: 600,
    color: pwc.white,
    background: pwc.grey300,
    border: "none",
    borderRadius: pwc.radius.md,
    cursor: "not-allowed",
    alignSelf: "flex-end" as const,
  } as React.CSSProperties,
  loadingText: {
    fontFamily: pwc.fontBody,
    fontSize: 14,
    color: pwc.grey500,
  } as React.CSSProperties,
  errorText: {
    fontFamily: pwc.fontBody,
    fontSize: 14,
    color: pwc.error,
    padding: `${pwc.space.sm}px ${pwc.space.md}px`,
    background: pwc.errorBg,
    borderRadius: pwc.radius.sm,
    border: `1px solid ${pwc.errorBorder}`,
  } as React.CSSProperties,
  progressText: {
    fontFamily: pwc.fontBody,
    fontSize: 13,
    color: pwc.grey700,
    padding: `${pwc.space.sm}px ${pwc.space.md}px`,
    background: pwc.grey50,
    borderRadius: pwc.radius.sm,
    border: `1px solid ${pwc.grey200}`,
    display: "flex",
    alignItems: "center",
    gap: pwc.space.sm,
  } as React.CSSProperties,
  scoutProgressPanel: {
    padding: pwc.space.md,
    background: "#FFFBF5",
    borderRadius: pwc.radius.sm,
    borderLeft: `3px solid ${pwc.orange500}`,
    display: "flex",
    flexDirection: "column" as const,
    gap: pwc.space.xs,
  } as React.CSSProperties,
  scoutProgressHeader: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    gap: pwc.space.sm,
  } as React.CSSProperties,
};

const makeEmptySelections = (): Record<StatementType, VariantSelection> =>
  mapStatements(() => ({ variant: "", confidence: null }));

const makeAllEnabled = (): Record<StatementType, boolean> =>
  mapStatements(() => true);

// Notes templates start OFF by default — PLAN §4 Phase D.2: "5 new checkboxes
// (default OFF)". Users opt in per run.
const makeNotesDisabled = (): Record<NotesTemplateType, boolean> => {
  const out = {} as Record<NotesTemplateType, boolean>;
  for (const nt of NOTES_TEMPLATE_TYPES) out[nt] = false;
  return out;
};

export function PreRunPanel({ sessionId, getSettings, onRun }: Props) {
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [scoutError, setScoutError] = useState<string | null>(null);
  const [scoutProgress, setScoutProgress] = useState<string | null>(null);
  const [scoutStartTime, setScoutStartTime] = useState<number | null>(null);
  const [scoutEnabled, setScoutEnabled] = useState(true);
  // Operator override for scanned (image-only) PDFs. When ticked, the scout
  // endpoint is told to skip the PyMuPDF-regex notes-inventory path and go
  // straight to the vision fallback. Default off — the LLM scout still
  // handles text PDFs fine and vision-only is more expensive.
  const [scannedPdf, setScannedPdf] = useState(false);
  // Persisted scout model lives in XBRL_DEFAULT_MODELS.scout server-side
  // (see server.py `_load_extended_settings`). We hydrate this from the
  // /api/settings call so the dropdown shows the last-selected model; on
  // change we fire updateSettings() to write through, matching how the
  // Settings page has always done it.
  const [scoutModel, setScoutModel] = useState<string>("");
  // Tracks the in-flight POST /api/settings from a dropdown change. Peer-
  // review [HIGH]: without this, a fast change-then-click flow lets the
  // scout endpoint read the stale .env because the browser fired the scout
  // POST before the settings POST flushed. handleAutoDetect awaits this
  // ref so the scout call is always serialised after the persist. Held as
  // a ref (not state) to avoid an extra render on each save, and because
  // handleAutoDetect needs the *current* pending promise at click time.
  const scoutModelSaveRef = React.useRef<Promise<unknown> | null>(null);
  // Non-fatal error surface for persist failures. Previously a console.warn;
  // surfacing inline gives operators visible feedback that their choice
  // didn't persist so they can retry or fall back to the Settings page.
  const [scoutModelSaveError, setScoutModelSaveError] = useState<string | null>(null);
  // Accumulated SSE events for the current / most recent scout run. Reused
  // by AgentTimeline so the scout's tool-call feed renders identically to
  // extraction agents. Reset to [] when Auto-detect fires so previous runs
  // don't bleed into a fresh one. Collapsed UI state (scoutLogOpen) lives
  // alongside so the log auto-opens during detect and collapses after.
  const [scoutEvents, setScoutEvents] = useState<SSEEvent[]>([]);
  const [scoutLogOpen, setScoutLogOpen] = useState(false);
  const [isDetecting, setIsDetecting] = useState(false);
  const [infopack, setInfopack] = useState<Record<string, unknown> | null>(null);

  const [filingLevel, setFilingLevel] = useState<FilingLevel>("company");
  const [variantSelections, setVariantSelections] = useState(makeEmptySelections);
  const [statementsEnabled, setStatementsEnabled] = useState(makeAllEnabled);
  // Tracks statements the user has EXPLICITLY enabled. Scout will not
  // silently disable these even if it failed to detect them (#18). Cleared
  // entries mean "no explicit user preference" — scout is free to manage them.
  const [userEnabledOverrides, setUserEnabledOverrides] = useState<Set<StatementType>>(
    () => new Set<StatementType>(),
  );
  // Mirror the latest overrides into a ref so the scout handler — which
  // runs asynchronously across many SSE events — sees mid-run toggles
  // instead of the snapshot captured when `handleAutoDetect` was created.
  // Without this, a user enabling a statement mid-scout could still have
  // scout disable it when infopack arrives (peer-review finding #4).
  const userEnabledOverridesRef = React.useRef(userEnabledOverrides);
  useEffect(() => {
    userEnabledOverridesRef.current = userEnabledOverrides;
  }, [userEnabledOverrides]);
  // Populated when scout would have disabled a statement but we respected
  // the user's explicit enable instead. Drives the one-line notice in UI.
  const [scoutOverrideNote, setScoutOverrideNote] = useState<string | null>(null);
  const [modelOverrides, setModelOverrides] = useState<Record<StatementType, string>>(
    {} as Record<StatementType, string>,
  );
  const [availableModels, setAvailableModels] = useState<ModelEntry[]>([]);
  const [notesEnabled, setNotesEnabled] = useState(makeNotesDisabled);
  // Per-note model overrides — mirrors `modelOverrides` for face statements.
  // Initialized from the same defaults as the face-statement rows so every
  // cell always has a concrete model id (required by <select value=...>).
  const [notesModelOverrides, setNotesModelOverrides] = useState<
    Record<NotesTemplateType, string>
  >({} as Record<NotesTemplateType, string>);

  // Load settings on mount
  useEffect(() => {
    let cancelled = false;
    getSettings()
      .then((settings) => {
        if (cancelled) return;
        setScoutEnabled(settings.scout_enabled_default);
        setAvailableModels(settings.available_models);
        // Peer-review #7 defensive fallback: if the persisted scout model
        // has been removed from config/models.json between sessions, a
        // bare-value assignment would make <select value> point to a
        // non-existent <option> and React warns + renders blank. Prefer
        // persisted → global → first available, so the dropdown always
        // has a matching option.
        const persistedScout = settings.default_models.scout || settings.model;
        const knownIds = new Set(settings.available_models.map((m) => m.id));
        if (knownIds.has(persistedScout)) {
          setScoutModel(persistedScout);
        } else if (knownIds.has(settings.model)) {
          setScoutModel(settings.model);
        } else if (settings.available_models.length > 0) {
          setScoutModel(settings.available_models[0].id);
        } else {
          // No models at all — leave empty; the <select> will render zero
          // options and the Auto-detect call will fail informatively.
          setScoutModel(persistedScout);
        }
        // Initialize model overrides from defaults
        const overrides = {} as Record<StatementType, string>;
        for (const stmt of STATEMENT_TYPES) {
          overrides[stmt] = settings.default_models[stmt] || settings.model;
        }
        setModelOverrides(overrides);
        // Notes use the same default-model fallback chain: per-template
        // default_models entry → global `settings.model`. The backend
        // accepts partial notes_models, so we only send explicit overrides
        // at submit time — but every dropdown still needs a value at init.
        const notesOverrides = {} as Record<NotesTemplateType, string>;
        for (const nt of NOTES_TEMPLATE_TYPES) {
          notesOverrides[nt] = settings.default_models[nt] || settings.model;
        }
        setNotesModelOverrides(notesOverrides);
        setLoading(false);
      })
      .catch((err) => {
        if (!cancelled) {
          setLoadError(err instanceof Error ? err.message : "Failed to load settings");
          setLoading(false);
        }
      });
    return () => { cancelled = true; };
  }, [getSettings]);

  const handleVariantChange = useCallback(
    (stmt: StatementType, sel: VariantSelection) => {
      setVariantSelections((prev) => ({ ...prev, [stmt]: sel }));
    },
    [],
  );

  const handleToggleStatement = useCallback(
    (stmt: StatementType, enabled: boolean) => {
      setStatementsEnabled((prev) => ({ ...prev, [stmt]: enabled }));
      // Track explicit user intent so scout won't silently flip it back (#18).
      // Enabling adds the override; disabling removes it (the user is fine
      // with scout managing this statement from here on).
      setUserEnabledOverrides((prev) => {
        const next = new Set(prev);
        if (enabled) next.add(stmt);
        else next.delete(stmt);
        return next;
      });
    },
    [],
  );

  const handleModelChange = useCallback(
    (stmt: StatementType, modelId: string) => {
      setModelOverrides((prev) => ({ ...prev, [stmt]: modelId }));
    },
    [],
  );

  // Elapsed-time ticker for scout progress
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    if (!scoutStartTime) { setElapsed(0); return; }
    const interval = setInterval(() => setElapsed(Math.floor((Date.now() - scoutStartTime) / 1000)), 1000);
    return () => clearInterval(interval);
  }, [scoutStartTime]);
  const elapsedText = elapsed > 0 ? `${elapsed}s` : "";

  // AbortController ref for cancelling in-flight scout requests on unmount
  const scoutAbortRef = React.useRef<AbortController | null>(null);

  // Cleanup on unmount: abort any in-flight scout request
  useEffect(() => {
    return () => { scoutAbortRef.current?.abort(); };
  }, []);

  const handleAutoDetect = useCallback(async () => {
    // Peer-review [HIGH] race guard: await any pending scout-model persist
    // so the scout endpoint reads the up-to-date .env. updateSettings's
    // rejection is swallowed here — the handler already surfaced the error
    // via scoutModelSaveError, and the scout call will fall back to the
    // previously-persisted model which is the sensible default for a
    // transient /api/settings outage.
    const pending = scoutModelSaveRef.current;
    if (pending) {
      try { await pending; } catch { /* error already surfaced */ }
    }

    // Abort any previous in-flight scout request
    scoutAbortRef.current?.abort();
    const abortController = new AbortController();
    scoutAbortRef.current = abortController;
    let cancelled = false;

    setIsDetecting(true);
    setScoutError(null);
    setScoutProgress(null);
    setScoutOverrideNote(null);
    setScoutStartTime(Date.now());
    // Reset timeline state for a fresh Auto-detect run and auto-expand the
    // log so users see the work happening. Collapsing back happens on
    // scout_complete / scout_cancelled / error (see the switch below).
    setScoutEvents([]);
    setScoutLogOpen(true);
    try {
      // Call the scout endpoint via SSE. When the user flagged the upload
      // as scanned, pass scanned_pdf so the server forces the vision path
      // for notes inventory (skipping the PyMuPDF regex that returns [] on
      // image-only PDFs). Body is only sent when the flag is set so the
      // default request shape is unchanged for text PDFs.
      const response = await fetch(`/api/scout/${sessionId}`, {
        method: "POST",
        signal: abortController.signal,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ scanned_pdf: scannedPdf }),
      });
      if (!response.ok) {
        let detail = `Scout failed (${response.status})`;
        try {
          const body = await response.json();
          detail = body.detail || body.message || detail;
        } catch { /* no JSON body */ }
        if (!cancelled) setScoutError(detail);
        return;
      }
      const reader = response.body?.getReader();
      if (!reader) {
        if (!cancelled) setScoutError("No response stream from scout");
        return;
      }

      // Cancel reader when abort fires
      abortController.signal.addEventListener("abort", () => {
        cancelled = true;
        reader.cancel().catch(() => {});
      });

      // One dispatch function per scout event type. Keeping each handler
      // small and local makes the loop body below just a switch statement.
      const handleInfopack = (data: Record<string, unknown>) => {
        const infopackValue = data.infopack as Record<string, unknown> | undefined;
        if (!infopackValue) return;
        setInfopack(infopackValue);
        setScoutProgress("Auto-detect complete");

        const statements = (infopackValue.statements ?? {}) as Record<string, unknown>;
        const scoutDetectedAnything = Object.keys(statements).length > 0;

        // Respect explicit user enables (#18): if the user manually turned on
        // a statement that scout didn't detect, leave it on and surface a
        // one-line notice so they know scout disagreed. Read from the ref
        // (not the closure-captured state) so a mid-run user toggle is
        // honoured (peer-review finding #4).
        //
        // Guard the "scout detected nothing" case (empty statements dict).
        // Without this, handleInfopack silently unchecks every row and the
        // Variants panel collapses — leaving the operator with no affordance
        // to proceed. Treat it as a soft-failure: keep enabled rows enabled,
        // surface a single notice explaining scout came up empty.
        const protectedStmts: StatementType[] = [];
        const latestOverrides = userEnabledOverridesRef.current;
        if (scoutDetectedAnything) {
          setStatementsEnabled((prev) => {
            const next = { ...prev };
            for (const stmt of STATEMENT_TYPES) {
              if (!(stmt in statements)) {
                if (latestOverrides.has(stmt) && prev[stmt]) {
                  protectedStmts.push(stmt);
                } else {
                  next[stmt] = false;
                }
              }
            }
            return next;
          });
        }
        setScoutOverrideNote(
          !scoutDetectedAnything
            ? "Scout didn't detect any statements in this PDF — keeping your current selection. Pick variants manually or try a different model."
            : protectedStmts.length > 0
              ? `Scout didn't detect ${protectedStmts.join(", ")} — kept enabled based on your selection.`
              : null,
        );

        // Detected statements get their variant + confidence; missing ones
        // get a "not_detected" marker.
        //
        // Peer-review finding #2: when scout comes up completely empty
        // (zero statements detected), we must NOT overwrite any variant
        // the operator picked manually — the notice above promised to
        // "keep your current selection". Skip the reset loop entirely on
        // the empty path so manual variants + confidences survive.
        if (scoutDetectedAnything) {
          setVariantSelections((prev) => {
            const next = { ...prev };
            for (const stmt of STATEMENT_TYPES) {
              const info = statements[stmt] as Record<string, unknown> | undefined;
              if (info) {
                const variant = info.variant_suggestion as string | undefined;
                if (variant) {
                  const rawConf = String(info.confidence || "MEDIUM").toLowerCase();
                  const confidence = (["high", "medium", "low"].includes(rawConf)
                    ? rawConf
                    : "medium") as "high" | "medium" | "low";
                  next[stmt] = { variant, confidence };
                } else {
                  next[stmt] = { variant: "", confidence: "low" };
                }
              } else {
                next[stmt] = { variant: "", confidence: "low" };
              }
            }
            return next;
          });
        }
      };

      for await (const evt of parseSSEStream(reader)) {
        if (cancelled) break;
        const data = (evt.data ?? {}) as Record<string, unknown>;

        // Mirror every timeline-relevant event into scoutEvents so
        // AgentTimeline can render the tool-call feed. `isScoutTimelineEvent`
        // is the single source of truth for which events matter; routing
        // envelopes (scout_complete, scout_cancelled) stay out of the log.
        if (isScoutTimelineEvent(evt)) {
          setScoutEvents((prev) => [...prev, evt as unknown as SSEEvent]);
        }

        switch (evt.event) {
          case "tool_call":
            setScoutProgress(`${humanToolName(String(data.tool_name ?? ""))}…`);
            break;
          case "tool_result":
            // Tool results don't drive UI state (Phase 10.3 removed the
            // bullet list); swallowed to keep the switch exhaustive.
            break;
          case "status":
            // Phase/status events set no visible state today — the active
            // tool name already drives the header line.
            break;
          case "scout_complete":
            if (data.success) handleInfopack(data);
            // Auto-collapse the log once the work is done. The operator
            // can re-expand to inspect what scout did after the fact.
            setScoutLogOpen(false);
            break;
          case "scout_cancelled":
            // Server-side cancellation. The abort handler already flipped
            // isDetecting/startTime; nothing else to do.
            setScoutLogOpen(false);
            break;
          case "error":
            setScoutError(typeof data.message === "string" ? data.message : "Scout failed");
            setScoutStartTime(null);
            // Keep the log open on error — the timeline is diagnostic
            // evidence for what went wrong.
            break;
          default:
            // Unknown event type — ignore to stay forward-compatible.
            break;
        }
      }
    } catch (err) {
      if (!cancelled) {
        const msg = err instanceof Error ? err.message : "Auto-detect failed";
        // AbortError is expected when we cancel — don't show it to the user
        if (err instanceof DOMException && err.name === "AbortError") return;
        setScoutError(msg);
      }
    } finally {
      if (!cancelled) {
        setIsDetecting(false);
        setScoutStartTime(null);
      }
    }
    // userEnabledOverrides intentionally excluded — the handler reads the
    // latest value via userEnabledOverridesRef so recreating the callback
    // every time the user toggles a statement isn't necessary (and would
    // leak the stale-closure bug back in).
  }, [sessionId, scannedPdf]);

  const handleStopScout = useCallback(() => {
    scoutAbortRef.current?.abort();
    setIsDetecting(false);
    setScoutStartTime(null);
    setScoutProgress(null);
    // Best-effort server-side cancellation
    if (sessionId) {
      abortAgent(sessionId, "scout").catch(() => {});
    }
  }, [sessionId]);

  // Derive the tool timeline once per scoutEvents change — buildToolTimeline
  // is O(N) but re-running it on every render (e.g. an unrelated checkbox
  // toggle) is wasted work when the event list hasn't changed. Declared here
  // alongside the other hooks so the hook-call order stays constant across
  // renders (React throws "Rendered more hooks than during the previous
  // render" if a hook sits after a conditional early-return).
  const scoutToolTimeline = useMemo(() => buildToolTimeline(scoutEvents), [scoutEvents]);

  const handleScoutModelChange = useCallback((modelId: string) => {
    // Optimistic local update so the dropdown reflects the choice instantly,
    // then persist through the existing settings endpoint. The promise is
    // tracked on scoutModelSaveRef so handleAutoDetect can await it before
    // kicking off the scout call — closes the race the peer-review flagged.
    // Failure surfaces via scoutModelSaveError; the current session still
    // uses the locally-picked model on the next persist that succeeds.
    setScoutModel(modelId);
    setScoutModelSaveError(null);
    const p: Promise<unknown> = updateSettings({ default_models: { scout: modelId } })
      .catch((err) => {
        const msg = err instanceof Error ? err.message : "Failed to persist scout model selection";
        setScoutModelSaveError(msg);
        // Re-throw so awaiters (handleAutoDetect) see the failure and can
        // decide whether to proceed. We still start the scout run — running
        // the scout on the old persisted model is a better default than
        // blocking the user indefinitely on a transient /api/settings
        // outage.
        throw err;
      })
      .finally(() => {
        // Only clear the ref if THIS promise is still the active one; a
        // second rapid-fire change would have replaced the ref and we
        // don't want to clobber the newer save.
        if (scoutModelSaveRef.current === p) {
          scoutModelSaveRef.current = null;
        }
      });
    scoutModelSaveRef.current = p;
  }, []);

  const handleToggleNote = useCallback((nt: NotesTemplateType, enabled: boolean) => {
    setNotesEnabled((prev) => ({ ...prev, [nt]: enabled }));
  }, []);

  const handleNotesModelChange = useCallback(
    (nt: NotesTemplateType, modelId: string) => {
      setNotesModelOverrides((prev) => ({ ...prev, [nt]: modelId }));
    },
    [],
  );

  const handleRun = useCallback(() => {
    const enabledStmts = STATEMENT_TYPES.filter((s) => statementsEnabled[s]);
    const variants: Record<string, string> = {};
    const models: Record<string, string> = {};

    for (const stmt of enabledStmts) {
      if (variantSelections[stmt].variant) {
        variants[stmt] = variantSelections[stmt].variant;
      }
      models[stmt] = modelOverrides[stmt];
    }

    const notes_to_run = NOTES_TEMPLATE_TYPES.filter((nt) => notesEnabled[nt]);
    // Only include model entries for notes the user actually enabled, so
    // the backend doesn't spin up proxy-model objects for templates it
    // won't run. Mirrors how `models` is populated for face statements.
    const notes_models: Partial<Record<NotesTemplateType, string>> = {};
    for (const nt of notes_to_run) {
      notes_models[nt] = notesModelOverrides[nt];
    }

    onRun({
      statements: enabledStmts,
      variants,
      models,
      infopack: scoutEnabled ? infopack : null,
      use_scout: scoutEnabled,
      filing_level: filingLevel,
      notes_to_run,
      notes_models,
    });
  }, [statementsEnabled, variantSelections, modelOverrides, infopack, scoutEnabled, filingLevel, notesEnabled, notesModelOverrides, onRun]);

  if (loading) {
    return (
      <div style={styles.container}>
        <p style={styles.loadingText}>Loading settings...</p>
      </div>
    );
  }

  if (loadError) {
    return (
      <div style={styles.container}>
        <p style={styles.errorText}>Failed to load settings: {loadError}</p>
      </div>
    );
  }

  const enabledStmts = STATEMENT_TYPES.filter((s) => statementsEnabled[s]);
  const enabledNotes = NOTES_TEMPLATE_TYPES.filter((n) => notesEnabled[n]);
  // PLAN §4 D.2: submitting with no notes selected still runs face-only
  // (current behaviour). Notes-only runs are also allowed so an operator
  // can refill just the notes sheets after an earlier face extraction.
  const canRun = enabledStmts.length > 0 || enabledNotes.length > 0;

  return (
    <div style={styles.container}>
      <h2 style={styles.heading}>Run Configuration</h2>

      {/* Filing level: Company or Group */}
      <div style={styles.section}>
        <span style={styles.sectionLabel}>Filing Level</span>
        <div style={{ display: "inline-flex", alignSelf: "flex-start", border: `1px solid ${pwc.grey200}`, borderRadius: pwc.radius.md, overflow: "hidden" }}>
          {(["company", "group"] as const).map((level) => {
            const active = filingLevel === level;
            return (
              <button
                key={level}
                type="button"
                onClick={() => setFilingLevel(level)}
                style={{
                  fontFamily: pwc.fontHeading,
                  fontSize: 13,
                  fontWeight: active ? 600 : 500,
                  padding: "8px 24px",
                  border: "none",
                  borderRight: level === "company" ? `1px solid ${pwc.grey200}` : "none",
                  borderRadius: 0,
                  background: active ? pwc.orange500 : pwc.white,
                  color: active ? pwc.white : pwc.grey700,
                  cursor: "pointer",
                  transition: "background 0.15s, color 0.15s",
                }}
              >
                {level === "company" ? "Company" : "Group"}
              </button>
            );
          })}
        </div>
      </div>

      {/* Scout toggle + auto-detect */}
      <div style={styles.section}>
        <span style={styles.sectionLabel}>Scout</span>
        <ScoutToggle
          enabled={scoutEnabled}
          onToggle={setScoutEnabled}
          onAutoDetect={handleAutoDetect}
          isDetecting={isDetecting}
          canAutoDetect={!!sessionId}
          availableModels={availableModels}
          scoutModel={scoutModel}
          onScoutModelChange={handleScoutModelChange}
        />
        {scoutEnabled && (
          <label
            style={{
              display: "flex", alignItems: "center", gap: pwc.space.sm,
              fontFamily: pwc.fontBody, fontSize: 13, color: pwc.grey800,
              cursor: "pointer",
            }}
          >
            <input
              type="checkbox"
              checked={scannedPdf}
              onChange={(e) => setScannedPdf(e.target.checked)}
              disabled={isDetecting}
            />
            <span>
              Scanned PDF
              <span style={{ color: pwc.grey300, marginLeft: 6, fontSize: 12 }}>
                (skip text extraction, use vision)
              </span>
            </span>
          </label>
        )}
        {scoutModelSaveError && (
          <p
            role="status"
            style={{
              fontFamily: pwc.fontBody,
              fontSize: 12,
              color: pwc.error,
              margin: 0,
            }}
          >
            Couldn't save scout model selection: {scoutModelSaveError}. The
            next Auto-detect will use the previously persisted model.
          </p>
        )}
        {isDetecting && (
          <div style={styles.scoutProgressPanel}>
            <div style={styles.scoutProgressHeader}>
              <div style={{ display: "flex", alignItems: "center", gap: pwc.space.sm }}>
                <span style={{
                  width: 16, height: 16, borderRadius: "50%",
                  border: `2px solid ${pwc.grey200}`, borderTop: `2px solid ${pwc.orange500}`,
                  animation: "spin 0.8s linear infinite", flexShrink: 0, display: "inline-block",
                }} />
                <span style={{ fontFamily: pwc.fontHeading, fontSize: 13, fontWeight: 600, color: pwc.grey800 }}>
                  {scoutProgress || "Starting scout..."}
                </span>
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: pwc.space.sm }}>
                {scoutStartTime && (
                  <span style={{ fontFamily: pwc.fontBody, fontSize: 12, color: pwc.grey300 }}>
                    {elapsedText}
                  </span>
                )}
                <button
                  onClick={handleStopScout}
                  style={{
                    padding: "2px 10px", fontSize: 12, fontFamily: pwc.fontBody,
                    background: pwc.grey100, border: `1px solid ${pwc.grey200}`,
                    borderRadius: 4, cursor: "pointer", color: pwc.grey800,
                  }}
                >
                  Stop
                </button>
              </div>
            </div>
          </div>
        )}
        {scoutEvents.length > 0 && (
          // Collapsible tool-call timeline. Mirrors the AgentTimeline used by
          // extraction agents so the scout pane gets the same level of detail
          // once the log is expanded. Kept below the progress strip so the
          // one-line summary stays the primary status signal.
          <div>
            <button
              type="button"
              onClick={() => setScoutLogOpen((v) => !v)}
              aria-expanded={scoutLogOpen}
              aria-controls="scout-log-region"
              style={{
                padding: "4px 10px",
                fontSize: 12,
                fontFamily: pwc.fontBody,
                background: pwc.white,
                color: pwc.grey700,
                border: `1px solid ${pwc.grey200}`,
                borderRadius: pwc.radius.sm,
                cursor: "pointer",
              }}
            >
              {scoutLogOpen ? "▾ Hide scout log" : `▸ Show scout log (${scoutToolTimeline.length})`}
            </button>
            {scoutLogOpen && (
              <div
                id="scout-log-region"
                role="region"
                aria-label="Scout tool-call timeline"
                style={{ marginTop: pwc.space.sm }}
              >
                <AgentTimeline
                  events={scoutEvents}
                  toolTimeline={scoutToolTimeline}
                  isRunning={isDetecting}
                />
              </div>
            )}
          </div>
        )}
        {scoutError && <p style={styles.errorText}>{scoutError}</p>}
        {scoutOverrideNote && (
          <div style={styles.scoutProgressPanel} role="note">
            <span style={{ fontFamily: pwc.fontBody, fontSize: 12, color: pwc.grey800 }}>
              {scoutOverrideNote}
            </span>
          </div>
        )}
        {infopack && Array.isArray(infopack.notes_inventory) && (() => {
          const count = (infopack.notes_inventory as unknown[]).length;
          const notesRequested = NOTES_TEMPLATE_TYPES.some((n) => notesEnabled[n]);
          const hintVisible = count === 0 && notesRequested;
          return (
            <div
              style={{
                fontFamily: pwc.fontBody, fontSize: 12,
                color: hintVisible ? pwc.error : pwc.grey800,
                display: "flex", flexDirection: "column", gap: 2,
              }}
              role="status"
            >
              <span>Inventory: {count} note{count === 1 ? "" : "s"}</span>
              {hintVisible && (
                <span>
                  Scout couldn't read any notes from this PDF. Enable "Scanned PDF"
                  above and re-run Auto-detect to force the vision path.
                </span>
              )}
            </div>
          );
        })()}
      </div>

      <hr style={styles.divider} />

      {/* Variant selection */}
      <div style={styles.section}>
        <span style={styles.sectionLabel}>Variants</span>
        <VariantSelector
          selections={variantSelections}
          enabledStatements={enabledStmts}
          onChange={handleVariantChange}
        />
      </div>

      <hr style={styles.divider} />

      {/* Statement selection + model overrides */}
      <div style={styles.section}>
        <span style={styles.sectionLabel}>Statements & Models</span>
        <StatementRunConfig
          enabled={statementsEnabled}
          modelOverrides={modelOverrides}
          availableModels={availableModels}
          onToggleStatement={handleToggleStatement}
          onModelChange={handleModelChange}
        />
      </div>

      <hr style={styles.divider} />

      {/* Notes templates — independent of face statements. Default OFF.
          Layout mirrors Statements & Models (checkbox + per-row model
          picker) so users can opt in per template *and* pick the model
          that fills it. */}
      <div style={styles.section}>
        <span style={styles.sectionLabel}>Notes & Models</span>
        <NotesRunConfig
          enabled={notesEnabled}
          modelOverrides={notesModelOverrides}
          availableModels={availableModels}
          onToggleNote={handleToggleNote}
          onModelChange={handleNotesModelChange}
        />
      </div>

      <hr style={styles.divider} />

      {/* Run button — enabled when at least one face or notes template is selected. */}
      <button
        onClick={handleRun}
        disabled={!canRun}
        style={canRun ? styles.runButton : styles.runButtonDisabled}
      >
        Run Extraction
      </button>
    </div>
  );
}
