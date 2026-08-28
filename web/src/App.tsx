import { useReducer, useCallback, useState, useRef, useEffect } from "react";
import type { RunConfigPayload, NotesTemplateType } from "./lib/types";
import { NOTES_TEMPLATE_TYPES, STATEMENT_TYPES } from "./lib/types";
import { pwc, tokens } from "./lib/theme";
import { ui, uiClass } from "./lib/uiStyles";
import { appReducer, bootState, parseRouteFromPath } from "./lib/appReducer";
import { uploadPdf, abortAll, abortAgent } from "./lib/api";
import { getAuthMe, logout as apiLogout, refreshAuth } from "./lib/api";
import type { AuthMe } from "./lib/api";
import { LoginPage } from "./pages/LoginPage";
import {
  createMultiAgentSSE,
  createMultiAgentSSEByRunId,
  canResumeRunAfterSSEFailure,
  patchRunConfig,
  type SSEFailureKind,
} from "./lib/sse";
import { SettingsPage } from "./pages/SettingsPage";
import { TopNav } from "./components/TopNav";
import { SuccessToast } from "./components/SuccessToast";
import { SettingsIcon } from "./components/icons";
import { HistoryPage } from "./pages/HistoryPage";
import { ExtractPage } from "./pages/ExtractPage";
import { ConceptsPage } from "./pages/ConceptsPage";
import { BenchmarksPage } from "./pages/BenchmarksPage";
import { SuitesPage } from "./pages/SuitesPage";
import { TERMS } from "./lib/vocabulary";
import {
  announceRunTabChange,
  readRunTabFromUrl,
  RUN_TAB_CHANGE_EVENT,
} from "./lib/runTabs";
import type { RunTabKey } from "./lib/runTabs";
import "./index.css";

// ---------------------------------------------------------------------------
// Inline styles using the XBRL focused-workspace tokens — only the app-chrome pieces (page/header/main)
// live here. ExtractPage-scoped styles live next to ExtractPage.
// ---------------------------------------------------------------------------

const styles = {
  page: {
    ...ui.appShell,
  } as const,
  rail: {
    ...ui.appRail,
  } as const,
  railBrand: {
    display: "flex",
    alignItems: "center",
    gap: 10,
    padding: "2px 8px 24px",
  } as const,
  railToggle: {
    ...ui.buttonQuiet,
    width: 32,
    minWidth: 32,
    minHeight: 32,
    padding: 0,
    marginLeft: "auto",
    borderRadius: pwc.radius.sm,
    color: tokens.color.text.secondary,
  } as const,
  brandMark: {
    width: 27,
    height: 27,
    color: pwc.orange500,
    flexShrink: 0,
  } as const,
  headerTitle: {
    fontFamily: pwc.fontHeading,
    // Brand wordmark at semibold — the design system sets titles, headings
    // and the wordmark at 600 (two text weights: regular for body/data,
    // semibold for headings; no Light 300).
    fontWeight: pwc.weight.bold,
    fontSize: 17,
    letterSpacing: "-0.02em",
    color: pwc.black,
    margin: 0,
  } as const,
  railSectionLabel: {
    padding: "16px 10px 7px",
    fontFamily: pwc.fontBody,
    fontSize: 10,
    fontWeight: pwc.weight.bold,
    color: tokens.color.text.muted,
    textTransform: "uppercase" as const,
    letterSpacing: "0.09em",
  } as const,
  railNav: {
    flex: 1,
    minHeight: 0,
    overflowY: "auto" as const,
  } as const,
  railFooter: {
    marginTop: "auto",
    padding: "14px 8px 0",
    display: "flex",
    alignItems: "center",
    gap: 9,
    minWidth: 0,
  } as const,
  avatar: {
    width: 30,
    height: 30,
    display: "grid",
    placeItems: "center",
    flex: "0 0 auto",
    borderRadius: "50%",
    background: pwc.black,
    color: pwc.white,
    fontFamily: pwc.fontBody,
    fontSize: 11,
    fontWeight: pwc.weight.bold,
  } as const,
  userCopy: {
    minWidth: 0,
    display: "flex",
    flexDirection: "column" as const,
    gap: 2,
  } as const,
  workspace: {
    minWidth: 0,
    background: tokens.surface.canvas,
  } as const,
  topbar: {
    ...ui.appTopbar,
  } as const,
  context: {
    minWidth: 0,
  } as const,
  breadcrumb: {
    display: "flex",
    alignItems: "center",
    gap: pwc.space.sm,
    fontFamily: pwc.fontBody,
    fontSize: 13,
    color: tokens.color.text.secondary,
  } as const,
  contextTitle: {
    color: pwc.black,
    fontWeight: pwc.weight.medium,
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap" as const,
  } as const,
  settingsButton: {
    ...ui.buttonQuiet,
    ...ui.buttonSm,
  } as const,
  headerRight: {
    display: "flex",
    alignItems: "center",
    gap: pwc.space.md,
  } as const,
  userEmail: {
    fontFamily: pwc.fontBody,
    fontSize: 12,
    fontWeight: pwc.weight.medium,
    color: pwc.black,
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap" as const,
  } as const,
  logoutButton: {
    ...ui.buttonGhost,
    ...ui.buttonSm,
    color: tokens.color.text.secondary,
  } as const,
  // Standard page mode (design-system Layouts) — the shell owns the
  // route-level width.
  main: {
    maxWidth: 1500,
    margin: "0 auto",
    padding: "34px clamp(28px, 4vw, 62px) 110px",
    display: "flex",
    flexDirection: "column" as const,
    // 32px between major stacked blocks gives the airier section rhythm the
    // design language calls for (was 24px).
    gap: 28,
  } as const,
  // The concepts review workspace is a 3-column side-by-side surface that
  // genuinely benefits from the full viewport — the max-width cap left wide
  // gutters and squeezed the grid + PDF. No max-width here; tighter side
  // padding so the columns use most of the screen.
  mainFull: {
    maxWidth: "100%",
    margin: "0 auto",
    padding: "28px 28px 110px",
    display: "flex",
    flexDirection: "column" as const,
    gap: 28,
  } as const,
  // History is full-width like the concepts workspace but keeps the same
  // generous side padding as the standard (capped) page so it reads
  // consistently with the Template/Extract pages.
  mainHistory: {
    maxWidth: 1500,
    margin: "0 auto",
    padding: "34px clamp(28px, 4vw, 62px) 110px",
    display: "flex",
    flexDirection: "column" as const,
    gap: 28,
  } as const,
};

// ---------------------------------------------------------------------------
// App component
// ---------------------------------------------------------------------------

export default function App() {
  const [state, dispatch] = useReducer(appReducer, undefined, bootState);
  const [railCollapsed, setRailCollapsed] = useState<boolean>(() => {
    try {
      return window.sessionStorage.getItem("xbrl-navigation-collapsed") === "true";
    } catch {
      return false;
    }
  });
  const toggleRail = useCallback(() => {
    setRailCollapsed((current) => {
      const next = !current;
      try {
        window.sessionStorage.setItem("xbrl-navigation-collapsed", String(next));
      } catch {
        // Session storage can be unavailable in locked-down browsers. The
        // explicit control still works for the current render.
      }
      return next;
    });
  }, []);
  const [extractMode, setExtractMode] = useState<"queue" | "new">(
    () => window.location.hash === "#new-extraction" ? "new" : "queue",
  );
  const [runTab, setRunTab] = useState<RunTabKey>(
    () => readRunTabFromUrl() ?? "overview",
  );
  useEffect(() => {
    const onTabChange = (event: Event) => {
      const next = (event as CustomEvent<RunTabKey>).detail;
      if (next) setRunTab(next);
    };
    const onPop = () => setRunTab(readRunTabFromUrl() ?? "overview");
    window.addEventListener(RUN_TAB_CHANGE_EVENT, onTabChange);
    window.addEventListener("popstate", onPop);
    return () => {
      window.removeEventListener(RUN_TAB_CHANGE_EVENT, onTabChange);
      window.removeEventListener("popstate", onPop);
    };
  }, []);
  // Canonical-mode feature flag from the backend (peer-review finding 5).
  // Canonical mode is now MANDATORY (gotcha #21 — `_canonical_mode_enabled()`
  // is hardcoded True), so the flag's real value is always `true`; the
  // one-shot /api/config fetch can only ever confirm it. Default to `true`
  // so a raced/failed/401'd mount fetch can't strand the user by hiding
  // mandatory canonical UI (the Concepts tab + the post-run review link).
  const [canonicalEnabled, setCanonicalEnabled] = useState(true);
  useEffect(() => {
    let cancelled = false;
    fetch("/api/config")
      .then((r) => (r.ok ? r.json() : null))
      .then((cfg) => {
        if (!cancelled && cfg) setCanonicalEnabled(Boolean(cfg.canonical_mode));
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  // --- Auth gate (PLAN auth Phase 1.4) ---------------------------------------
  // Optimistic: render the app shell immediately and flip to the login page
  // only once /api/auth/me (or any 401) reports we're anonymous. No
  // confidential data renders before auth resolves — every data call is itself
  // server-gated, and a 401 flips us to login. (Deliberate deviation from the
  // plan's "blank until resolved": avoids a blank first paint and keeps the
  // existing synchronous App tests valid; same security outcome.) In
  // AUTH_MODE=dev the backend returns a dev user so the app just renders.
  const [authStatus, setAuthStatus] = useState<"authed" | "anon">("authed");
  const [user, setUser] = useState<AuthMe | null>(null);

  const checkAuth = useCallback(() => {
    return getAuthMe()
      .then((me) => {
        setUser(me);
        setAuthStatus(me ? "authed" : "anon");
      })
      .catch(() => {
        // A transient error (not a clean 401) shouldn't trap the user on a
        // blank screen — fall back to the login page.
        setUser(null);
        setAuthStatus("anon");
      });
  }, []);

  useEffect(() => {
    checkAuth();
  }, [checkAuth]);

  // Client-side guard for the one admin-only surface left: the bare
  // "Field labels" concepts landing. Benchmarks + Evals are open to every
  // signed-in user (PRD decision #6 — the backend routes were never
  // admin-gated; the old redirect here was the piece contradicting the
  // written policy). A `/concepts/{id}` run page (selectedRunId set) is the
  // everyday Figures view and stays open.
  useEffect(() => {
    if (!user || user.is_admin) return;
    const onAdminOnly =
      state.view === "concepts" && state.selectedRunId == null;
    if (onAdminOnly) {
      dispatch({ type: "SET_VIEW", payload: "extract" });
      dispatch({ type: "SET_SELECTED_RUN_ID", payload: null });
    }
  }, [user, state.view, state.selectedRunId]);

  // Any 401 mid-use (broadcast by apiFetch) means the session expired ⇒ show
  // the login page.
  useEffect(() => {
    const onUnauthorized = () => {
      setUser(null);
      setAuthStatus("anon");
    };
    window.addEventListener("auth:unauthorized", onUnauthorized);
    return () => window.removeEventListener("auth:unauthorized", onUnauthorized);
  }, []);

  // Idle tracker: throttled refresh ping (≤ 1/min) on real user input so
  // "watching a long run while moving the mouse" stays logged in, while a
  // genuinely idle tab still times out. Only active while authenticated.
  const lastPingRef = useRef(0);
  useEffect(() => {
    if (authStatus !== "authed") return;
    const onActivity = () => {
      const now = Date.now();
      if (now - lastPingRef.current < 60_000) return;
      lastPingRef.current = now;
      refreshAuth();
    };
    window.addEventListener("mousemove", onActivity);
    window.addEventListener("keydown", onActivity);
    return () => {
      window.removeEventListener("mousemove", onActivity);
      window.removeEventListener("keydown", onActivity);
    };
  }, [authStatus]);

  const handleLogout = useCallback(() => {
    apiLogout().finally(() => {
      setUser(null);
      setAuthStatus("anon");
    });
  }, []);

  // Hold the SSE controller so we can abort it on reset/unmount.
  // Without this, an in-flight stream would keep dispatching stale events
  // after the user starts a new run or closes the page.
  const sseControllerRef = useRef<AbortController | null>(null);

  // Mirror of `state` kept in a ref so callbacks wired into memoised
  // children (AgentTabs, ToolCallCard) can read the latest agents / config
  // without taking them as useCallback deps. Without this, `handleRerunAgent`
  // re-binds on every SSE event that touches state.agents, which flips the
  // prop identity and defeats AgentTabs' React.memo.
  const stateRef = useRef(state);
  useEffect(() => {
    stateRef.current = state;
  }, [state]);

  // Abort any open stream on unmount
  useEffect(() => {
    return () => {
      sseControllerRef.current?.abort();
    };
  }, []);

  // --- URL <-> view sync --------------------------------------------------
  // Push the view (and selected/current run id, if any) into the address
  // bar so deep-linking and copy/paste of the URL work. Four shapes:
  //   /                → extract view, no run loaded
  //   /run/<id>        → extract view rehydrated from a draft/running run
  //                       (PLAN-persistent-draft-uploads.md)
  //   /history         → history list
  //   /history/<id>    → full-page run detail (existing alias for completed
  //                       runs viewed from the History tab)
  // Listening for popstate mirrors browser Back/Forward into state without
  // a full reload. /run/<id> wins over the other extract-view shapes when
  // currentRunId is non-null, so a refresh of /run/42 stays on /run/42
  // instead of being rewritten back to /.
  useEffect(() => {
    let expected: string;
    if (state.view === "concepts") {
      // Keep /concepts/<id> in the URL so refresh / share / back work for
      // the canonical-mode tree view. Without this branch the effect falls
      // through to "/" and immediately pushes the deep link away on boot.
      // Bare Template landing now has its own addressable path instead of
      // collapsing to "/" (which made the Field-labels page unshareable and
      // its deep link fail — docs/PLAN-design-qa-fixes.md R2).
      expected = state.selectedRunId != null
        ? `/concepts/${state.selectedRunId}`
        : "/field-labels";
    } else if (state.view === "benchmarks") {
      // Gold-standard eval (v16): /benchmarks lists; /benchmarks/<id> opens the
      // gold editor. The benchmark id rides on selectedRunId (the generic
      // selected-entity slot — see parseRouteFromPath).
      expected = state.selectedRunId != null
        ? `/benchmarks/${state.selectedRunId}`
        : "/benchmarks";
    } else if (state.view === "suites") {
      // Evals workspace — internal navigation lives inside the page, so the
      // top-level URL is the singleton /evals.
      expected = "/evals";
    } else if (state.view === "settings") {
      // Singleton settings surface — no entity id rides along.
      expected = "/settings";
    } else if (state.view === "history") {
      expected = state.selectedRunId != null
        ? `/history/${state.selectedRunId}`
        : "/history";
    } else if (state.currentRunId != null) {
      expected = `/run/${state.currentRunId}`;
    } else {
      expected = "/";
    }
    if (window.location.pathname !== expected) {
      window.history.pushState(
        {
          view: state.view,
          selectedRunId: state.selectedRunId,
          currentRunId: state.currentRunId,
        },
        "",
        expected,
      );
    }
  }, [state.view, state.selectedRunId, state.currentRunId]);

  // Reflect the selected run in the browser tab title so a user with
  // multiple Template tabs open can tell them apart without switching.
  useEffect(() => {
    // A run id under either `history` OR the `concepts` alias is the unified
    // run page — title it "Run N". The bare Template landing (concepts, no id)
    // keeps the generic title.
    document.title =
      state.selectedRunId != null &&
      (state.view === "history" || state.view === "concepts")
        ? `XBRL Agent — Run ${state.selectedRunId}`
        : "XBRL Agent";
  }, [state.view, state.selectedRunId]);

  useEffect(() => {
    const onPop = () => {
      // Route parsing is centralised in parseRouteFromPath so bootState
      // and popstate agree on how a URL maps to state — including the
      // "any /history/<garbage> still lands on the list" forgiveness path.
      const route = parseRouteFromPath(window.location.pathname);
      dispatch({ type: "SET_VIEW", payload: route.view });
      dispatch({ type: "SET_SELECTED_RUN_ID", payload: route.selectedRunId });
      dispatch({ type: "SET_CURRENT_RUN_ID", payload: route.currentRunId });
    };
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  const handleReset = useCallback(() => {
    // Abort any active stream before clearing state, so the old run
    // can't race in events after the user has moved on.
    sseControllerRef.current?.abort();
    sseControllerRef.current = null;
    dispatch({ type: "RESET" });
  }, []);

  const handleUpload = useCallback(async (file: File) => {
    const result = await uploadPdf(file);
    dispatch({
      type: "UPLOADED",
      payload: {
        sessionId: result.session_id,
        filename: result.filename,
        // Peer-review #3 (HIGH): pin the runId that owns this sessionId
        // so ExtractPage's rehydrate effect can skip re-fetch ONLY when
        // the session and the URL agree.
        runId: result.run_id ?? null,
      },
    });
    // PLAN-persistent-draft-uploads.md (Phase C): the upload response
    // carries a run_id pointing at a freshly-inserted draft row. Setting
    // currentRunId here makes the URL effect rewrite the address bar to
    // `/run/{id}` so the user can refresh, bookmark, or share. When the
    // backend's draft-write failed (best-effort path), run_id is null and
    // we keep the legacy `/` URL — the upload still works, it's just not
    // reattach-on-refresh durable.
    if (result.run_id != null) {
      dispatch({ type: "SET_CURRENT_RUN_ID", payload: result.run_id });
    }
    return result;
  }, []);

  const handleStreamTransportError = useCallback((error: string, kind: SSEFailureKind) => {
    const runId = stateRef.current.currentRunId;
    if (canResumeRunAfterSSEFailure(kind, runId)) {
      // The backend deliberately continues and persists a run after its SSE
      // client disconnects. Move to the durable detail view, whose running-run
      // polling resumes monitoring without issuing a second extraction.
      dispatch({ type: "SET_VIEW", payload: "history" });
      dispatch({ type: "SET_SELECTED_RUN_ID", payload: runId });
      dispatch({ type: "SET_CURRENT_RUN_ID", payload: null });
      return;
    }
    // HTTP start rejections are authoritative application errors, not lost
    // connections. Keep them visible even when the draft already has an id;
    // redirecting would hide the rejection behind a History detail fetch.
    dispatch({
      type: "EVENT",
      payload: {
        event: "error",
        data: { message: error, traceback: "" },
        timestamp: Date.now() / 1000,
      },
    });
  }, []);

  // Shared plumbing for handleMultiRun + handleRerunAgent. Both flows dispatch
  // every incoming event to the reducer. Transport loss reattaches to the
  // durable run when possible; only pre-start failures become fatal UI errors.
  // Returns the AbortController for the caller to stash.
  const startSSERun = useCallback(
    (sessionId: string, config: RunConfigPayload, endpointPath?: string) => {
      return createMultiAgentSSE(
        sessionId,
        config,
        (event) => dispatch({ type: "EVENT", payload: event }),
        () => {},
        handleStreamTransportError,
        endpointPath,
      );
    },
    [handleStreamTransportError],
  );

  // Multi-agent run: receives a RunConfigPayload from PreRunPanel.
  //
  // PLAN-persistent-draft-uploads.md (Phase C, steps 19-20): when the
  // current page is a draft (`currentRunId` is set), we route through the
  // run-id endpoint so the audit row is reused — flipping `draft` →
  // `running` instead of creating a fresh row. Before kicking off the
  // stream we PATCH the live config to the row so the persisted blob
  // matches what the user is about to extract; the backend belt-and-
  // braces overwrites it again at start time, but the PATCH is what the
  // History "config" column displays for the run.
  // Peer-review #4 (MEDIUM, RUN-REVIEW follow-up): debounced draft
  // config persistence. PreRunPanel debounces the actual fire (500ms),
  // so this handler can stay synchronous-shaped — it just kicks off
  // the PATCH and swallows transient network errors. Without this,
  // refreshing or sharing /run/{id} pre-Run loses every selection
  // the user made, contradicting the persistent-draft contract.
  // Mandatory-arg currentRunId means we no-op when there's no draft
  // to PATCH (e.g. legacy `/` upload-then-Run flow).
  const handleDraftConfigChange = useCallback(async (config: RunConfigPayload) => {
    if (state.currentRunId == null) return;
    try {
      await patchRunConfig(state.currentRunId, config);
    } catch {
      // Auto-save is best-effort — a flaky save shouldn't disrupt
      // the user's editing flow. The user's eventual Run-click
      // fires the same PATCH and surfaces any persistent failure.
    }
  }, [state.currentRunId]);

  const handleMultiRun = useCallback(async (config: RunConfigPayload) => {
    if (!state.sessionId) return;
    sseControllerRef.current?.abort();
    dispatch({
      type: "RUN_STARTED",
      payload: {
        statements: config.statements,
        notes: config.notes_to_run ?? [],
        config,
      },
    });
    if (state.currentRunId != null) {
      // Persist config to the draft, then start. The PATCH is the
      // authoritative way to get this run's choices (statements, level,
      // standard, models, infopack) into the DB; `/start` reads them
      // back out and never sees the live request body. So a failed
      // PATCH means the run would either start with stale config or
      // fail validation server-side after the UI has already moved
      // into "running" — surface the failure here instead.
      try {
        await patchRunConfig(state.currentRunId, config);
      } catch (e) {
        const message = e instanceof Error ? e.message : "Failed to save run config";
        dispatch({
          type: "EVENT",
          payload: {
            event: "error",
            data: { message, traceback: "" },
            timestamp: Date.now() / 1000,
          },
        });
        return;
      }
      sseControllerRef.current = createMultiAgentSSEByRunId(
        state.currentRunId,
        (event) => dispatch({ type: "EVENT", payload: event }),
        () => {},
        handleStreamTransportError,
      );
    } else {
      sseControllerRef.current = startSSERun(state.sessionId, config);
    }
  }, [
    state.sessionId,
    state.currentRunId,
    startSSERun,
    handleStreamTransportError,
  ]);

  // Abort all running agents
  const handleAbortAll = useCallback(async () => {
    if (!state.sessionId) return;
    try {
      await abortAll(state.sessionId);
    } catch {
      // 404 = no tasks left; SSE events will handle state updates
    }
  }, [state.sessionId]);

  // Abort a single agent
  const handleAbortAgent = useCallback(async (agentId: string) => {
    if (!state.sessionId) return;
    dispatch({ type: "ABORT_AGENT", payload: { agentId } });
    try {
      await abortAgent(state.sessionId, agentId);
    } catch {
      // 404 = agent already finished
    }
  }, [state.sessionId]);

  // Rerun a single agent after abort/failure. Reads state via stateRef
  // so the callback identity stays stable across SSE events — otherwise
  // AgentTabs' React.memo bails on every token/tool update.
  //
  // Branches by agent kind:
  //   - face statement  → {statements:[role], variants, models}
  //   - notes template  → {statements:[], notes_to_run:[role], notes_models}
  //   - scout/validator → no-op (button is hidden for these tabs)
  //
  // Preserves original settings from `lastRunConfig` so the retry uses the
  // same variant, model, filing level, and infopack as the original run.
  const handleRerunAgent = useCallback((agentId: string) => {
    const s = stateRef.current;
    if (!s.sessionId) return;
    const agent = s.agents[agentId];
    if (!agent) return;

    const prev = s.lastRunConfig;
    const role = agent.role;

    const isFaceStatement = (STATEMENT_TYPES as readonly string[]).includes(role);
    const isNotes = (NOTES_TEMPLATE_TYPES as readonly string[]).includes(role);

    // Scout + validator aren't single-agent retryable — scout has its own
    // Auto-detect button, validator is a pipeline phase, not an agent. The
    // UI hides the rerun button for those tabs; this guard is a belt-and-
    // braces check so a stray call can't POST a malformed payload.
    if (!isFaceStatement && !isNotes) return;

    dispatch({ type: "RERUN_STARTED", payload: { agentId } });

    let config: RunConfigPayload;
    if (isFaceStatement) {
      config = {
        statements: [role as RunConfigPayload["statements"][0]],
        variants: prev?.variants[role] ? { [role]: prev.variants[role] } : {},
        models: prev?.models?.[role] ? { [role]: prev.models[role] } : {},
        infopack: prev?.infopack || null,
        use_scout: false,
        filing_level: prev?.filing_level || "company",
        filing_standard: prev?.filing_standard || "mfrs",
        denomination: prev?.denomination || "thousands",
      };
    } else {
      const nt = role as NotesTemplateType;
      const prevNotesModel = prev?.notes_models?.[nt];
      config = {
        statements: [],
        variants: {},
        models: {},
        infopack: prev?.infopack || null,
        use_scout: false,
        filing_level: prev?.filing_level || "company",
        filing_standard: prev?.filing_standard || "mfrs",
        denomination: prev?.denomination || "thousands",
        notes_to_run: [nt],
        notes_models: prevNotesModel ? { [nt]: prevNotesModel } : {},
      };
    }
    // Use the rerun endpoint so it doesn't conflict with active_runs guard
    sseControllerRef.current = startSSERun(s.sessionId, config, `/api/rerun/${s.sessionId}`);
  }, [startSSERun]);

  useEffect(() => {
    if (state.selectedRunId == null) {
      setRunTab("overview");
    } else if (state.view === "concepts") {
      setRunTab("values");
    } else if (state.view === "history") {
      setRunTab(readRunTabFromUrl() ?? "overview");
    }
  }, [state.selectedRunId, state.view]);

  // Auth gate: once /api/auth/me (or any 401) reports anonymous, show login.
  if (authStatus === "anon") {
    return <LoginPage onAuthenticated={checkAuth} />;
  }

  // Settings is a utility surface opened from the header, not a signal that
  // the operator abandoned the uploaded draft. Keep the extract workspace in
  // one stable React tree position while Settings is open so component-owned
  // work such as the optional preview scan is not cancelled by an unmount.
  // The wrapper is display:contents on the extract route so it does not change
  // the main flex layout, and display:none while Settings is visible.
  const extractWorkspace = (
    <ExtractPage
      state={state}
      dispatch={dispatch}
      landingMode={extractMode}
      onOpenNewExtraction={() => {
        setExtractMode("new");
        window.requestAnimationFrame(() => {
          document.getElementById("new-extraction")?.scrollIntoView({ block: "start" });
        });
      }}
      isAdmin={Boolean(user?.is_admin)}
      handleUpload={handleUpload}
      handleMultiRun={handleMultiRun}
      handleAbortAll={handleAbortAll}
      handleAbortAgent={handleAbortAgent}
      handleRerunAgent={handleRerunAgent}
      handleReset={handleReset}
      handleConfigChange={handleDraftConfigChange}
      // Homepage home-base navigation. Drafts resume at /run/{id}
      // (same as History's draft click); finished runs open in
      // History's detail; "View all" jumps to the History list.
      onResumeDraft={(id) => {
        dispatch({ type: "SET_VIEW", payload: "extract" });
        dispatch({ type: "SET_SELECTED_RUN_ID", payload: null });
        dispatch({ type: "SET_CURRENT_RUN_ID", payload: id });
      }}
      onOpenRun={(id) => {
        dispatch({ type: "SET_VIEW", payload: "history" });
        dispatch({ type: "SET_SELECTED_RUN_ID", payload: id });
      }}
      onViewAllRuns={() => {
        dispatch({ type: "SET_VIEW", payload: "history" });
        dispatch({ type: "SET_SELECTED_RUN_ID", payload: null });
      }}
    />
  );
  const keepExtractWorkspaceMounted =
    state.view === "extract"
    || (state.view === "settings" && state.sessionId != null);
  const navigationView =
    state.view === "concepts" && state.selectedRunId != null
      ? "history"
      : state.view;
  const contextLabel = state.view === "extract"
    ? state.currentRunId != null && state.filename
      ? state.filename
      : extractMode === "new" ? "New extraction" : "Work queue"
    : state.view === "history" || (state.view === "concepts" && state.selectedRunId != null)
      ? state.selectedRunId != null ? "Current filing" : "Runs"
      : state.view === "concepts"
        ? "Field labels"
        : state.view === "benchmarks"
          ? "Benchmarks"
          : state.view === "suites"
            ? TERMS.evaluationSuites
            : "Settings";
  const reviewFocused = state.selectedRunId != null &&
    (runTab === "notes" || runTab === "values");
  const filingRunId = state.selectedRunId ?? state.currentRunId;
  const currentFilingTab = state.view === "concepts"
    ? "values"
    : runTab === "notes"
      ? "notes"
      : runTab === "overview"
        ? "overview"
        : null;

  return (
    <div
      className={`app-shell${railCollapsed ? " app-shell--collapsed" : ""}`}
      style={{ ...styles.page, ...(railCollapsed ? { gridTemplateColumns: "72px minmax(0, 1fr)" } : {}) }}
    >
      {/* Keyboard users can jump past the header + nav straight to the page
          content. Visually hidden until focused (index.css .skip-link). */}
      <a href="#main-content" className="skip-link">
        Skip to main content
      </a>
      <aside className="app-sidebar" style={styles.rail} aria-label="Workspace navigation">
        <div className="app-rail-brand" style={styles.railBrand}>
          <svg
            className="app-rail-mark"
            style={styles.brandMark}
            viewBox="0 0 100 100"
            fill="none"
            aria-hidden="true"
          >
            <path
              d="M12 78 L40 22 L52 50 L66 30 L88 78"
              stroke="currentColor"
              strokeWidth="11"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
          <span className="app-rail-brand-label" style={styles.headerTitle}>XBRL Agent</span>
          <button
            type="button"
            className={`${uiClass.btnQuiet} app-rail-toggle`}
            style={styles.railToggle}
            onClick={toggleRail}
            aria-label={railCollapsed ? "Expand navigation" : "Collapse navigation"}
            aria-expanded={!railCollapsed}
            aria-controls="app-primary-navigation"
            title={railCollapsed ? "Expand navigation" : "Collapse navigation"}
          >
            <span aria-hidden="true">{railCollapsed ? "›" : "‹"}</span>
          </button>
        </div>
        <div id="app-primary-navigation" className="app-rail-nav" style={styles.railNav}>
          <TopNav
            // The `concepts` view with a run id is the unified run page (a
            // History activity reached via "Review values" / the /concepts
            // alias), so highlight History — not Template. "Template" stays
            // highlighted only for the bare concept landing (no run id).
            view={navigationView}
            extractMode={extractMode}
            currentRunId={filingRunId}
            currentFilingTab={currentFilingTab}
            showConcepts={canonicalEnabled}
            isAdmin={Boolean(user?.is_admin)}
            onNewExtraction={() => {
              // Read the mirrored state at click time. The navigation callback
              // can otherwise retain the render that preceded RUN_STARTED
              // while the stream is already active.
              if (stateRef.current.isRunning) {
                // A second upload cannot start while this stream owns the
                // session. Preserve the active monitoring surface and URL.
                return;
              }
              setExtractMode("new");
              handleReset();
              window.history.replaceState({}, "", "/#new-extraction");
              window.requestAnimationFrame(() => {
                document.getElementById("new-extraction")?.scrollIntoView?.({ block: "start" });
              });
            }}
            onOpenCurrentFiling={(tab) => {
              if (filingRunId == null) return;
              if (tab === "values") {
                window.history.pushState({}, "", `/concepts/${filingRunId}`);
                announceRunTabChange("values");
                dispatch({ type: "SET_VIEW", payload: "concepts" });
                dispatch({ type: "SET_SELECTED_RUN_ID", payload: filingRunId });
                return;
              }
              window.history.pushState({}, "", `/history/${filingRunId}?tab=${tab}`);
              announceRunTabChange(tab);
              dispatch({ type: "SET_VIEW", payload: "history" });
              dispatch({ type: "SET_SELECTED_RUN_ID", payload: filingRunId });
            }}
            onViewChange={(v) => {
              // Tabs are "go to the top of that section" — clicking
              // History from anywhere must show the list, not the last
              // run the user was viewing. Without this clear,
              // selectedRunId leaks across tab switches and the URL
              // effect routes back to /history/<id> when the user next
              // clicks History. Popstate still dispatches its own
              // SET_SELECTED_RUN_ID so browser Back/Forward still
              // restore a deep-linked run correctly.
              //
              // "Extract" is the top of its own section: a fresh, empty
              // upload box. A bare SET_VIEW would leave currentRunId +
              // sessionId + the completed-run state intact, so the page
              // re-showed the *last run* instead of an empty box. Reuse
              // the full reset (also clears the /run/<id> URL + aborts any
              // stale stream). Guarded on isRunning so clicking Extract
              // while a run is still streaming surfaces it instead of
              // killing it.
              if (v === "extract" && !state.isRunning) {
                setExtractMode("queue");
                handleReset();
                window.history.replaceState({}, "", "/");
                return;
              }
              dispatch({ type: "SET_VIEW", payload: v });
              dispatch({ type: "SET_SELECTED_RUN_ID", payload: null });
            }}
          />
        </div>
        <div className="app-rail-footer" style={styles.railFooter}>
          {user && (
            <>
              <span style={styles.avatar} aria-hidden="true">
                {user.email.slice(0, 2).toUpperCase()}
              </span>
              <span className="app-user-copy" style={styles.userCopy}>
                <span className="app-user-email" style={styles.userEmail}>{user.email}</span>
                <span style={styles.breadcrumb}>{user.is_admin ? "Administrator" : "User"}</span>
              </span>
            </>
          )}
        </div>
      </aside>

      <div className="app-workspace" style={styles.workspace}>
        <header className="app-topbar" style={styles.topbar}>
          <div style={styles.context}>
            <span style={styles.breadcrumb}>
              <span>Workspace</span>
              <span aria-hidden="true">›</span>
              <strong style={styles.contextTitle} title={state.filename ?? contextLabel}>
                {reviewFocused && state.filename ? state.filename : contextLabel}
              </strong>
            </span>
          </div>
          <div className="app-header-right" style={styles.headerRight}>
          {user?.provider !== "dev" && (
            <button
              onClick={handleLogout}
              style={styles.logoutButton}
              className={uiClass.btnGhost}
              aria-label="Log out"
              title="Log out"
            >
              Log out
            </button>
          )}
          <button
            onClick={() => {
              dispatch({ type: "SET_VIEW", payload: "settings" });
              dispatch({ type: "SET_SELECTED_RUN_ID", payload: null });
            }}
            style={styles.settingsButton}
            className={uiClass.btnSubtle}
            aria-label="Settings"
          >
            <SettingsIcon />
            <span>Settings</span>
          </button>
          </div>
        </header>

      <main
        id="main-content"
        tabIndex={-1}
        className="app-main"
        style={
          // Two intentional widths (docs/PLAN-design-qa-fixes.md C3):
          //  • Workspace pages (History, run detail via /concepts/{id}) run
          //    full-bleed — they hold wide tables / side-by-side panes.
          //  • The bare Template landing is the 3-column ConceptsPage (its own
          //    tight-gutter full-bleed variant).
          //  • Everything else — Extract, Settings, Benchmarks — is a content
          //    page and shares the one capped, centred width so moving between
          //    them doesn't jump. (Benchmarks used to be full-bleed, which is
          //    what made the width feel inconsistent.)
          state.view === "concepts" && state.selectedRunId == null
            ? styles.mainFull
            : state.view === "history" ||
              state.view === "concepts"
            ? styles.mainHistory
            : styles.main
        }
      >
        {state.view === "settings" && (
          // Consolidated settings page (replaces the gear's settings modal):
          // General · Account · Users. The Users tab is admin-gated (and the
          // server enforces it independently).
          <SettingsPage isAdmin={Boolean(user?.is_admin)} currentEmail={user?.email} />
        )}
        {keepExtractWorkspaceMounted && (
          <div
            aria-hidden={state.view === "settings" ? true : undefined}
            style={{ display: state.view === "extract" ? "contents" : "none" }}
          >
            {extractWorkspace}
          </div>
        )}
        {state.view !== "settings" && state.view !== "extract" && (
          state.view === "benchmarks" ? (
          // Gold-standard eval (v16): the benchmark library + gold editor.
          // selectedRunId carries the selected benchmark id (the generic
          // selected-entity slot).
          <BenchmarksPage
            selectedId={state.selectedRunId}
            onSelectBenchmark={(id) =>
              dispatch({ type: "SET_SELECTED_RUN_ID", payload: id })
            }
            isAdmin={Boolean(user?.is_admin)}
          />
        ) : state.view === "suites" ? (
          // Evals workspace (Phase E/F): suites, batch runner, trends + compare.
          <SuitesPage />
        ) : state.view === "concepts" ? (
          // `/concepts/{id}` is now an alias that opens the unified run page
          // on the Values tab (the standalone full-page Concepts surface was
          // folded into the tabbed run detail — see
          // docs/PLAN-run-page-and-telemetry.md). The bare Template landing
          // (no run id) still shows the ConceptsPage empty/select state.
          state.selectedRunId != null ? (
            <HistoryPage
              canonicalEnabled={canonicalEnabled}
              selectedId={state.selectedRunId}
              initialRunTab="values"
              onSelectRun={(id) => {
                // Back from a /concepts/{id} run page (id === null) should
                // land on the History list, not the empty Template landing —
                // the run page belongs to History. Switch the view so the
                // user ends up where the nav already says they are.
                if (id == null) {
                  dispatch({ type: "SET_VIEW", payload: "history" });
                }
                dispatch({ type: "SET_SELECTED_RUN_ID", payload: id });
              }}
              onResumeDraft={(id) => {
                dispatch({ type: "SET_VIEW", payload: "extract" });
                dispatch({ type: "SET_SELECTED_RUN_ID", payload: null });
                dispatch({ type: "SET_CURRENT_RUN_ID", payload: id });
              }}
            />
          ) : (
            <ConceptsPage runId={state.selectedRunId} />
          )
        ) : (
          <HistoryPage
            canonicalEnabled={canonicalEnabled}
            selectedId={state.selectedRunId}
            onSelectRun={(id) =>
              dispatch({ type: "SET_SELECTED_RUN_ID", payload: id })
            }
            onResumeDraft={(id) => {
              // PLAN-persistent-draft-uploads.md: drafts in History
              // navigate to /run/{id} instead of opening the inline
              // detail. Switching the view + setting currentRunId
              // triggers the URL effect to push the shareable URL.
              dispatch({ type: "SET_VIEW", payload: "extract" });
              dispatch({ type: "SET_SELECTED_RUN_ID", payload: null });
              dispatch({ type: "SET_CURRENT_RUN_ID", payload: id });
            }}
          />
        ))}
      </main>

      {/* Phase 9: Run-complete success toast — top-right, auto-dismiss 4 s */}
      <SuccessToast
        toast={state.toast}
        onDismiss={() => dispatch({ type: "DISMISS_TOAST" })}
      />
      </div>
    </div>
  );
}
