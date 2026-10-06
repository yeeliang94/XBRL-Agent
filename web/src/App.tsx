import { useReducer, useCallback, useState, useRef, useEffect } from "react";
import type { RunConfigPayload, NotesTemplateType, RunSummaryJson } from "./lib/types";
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
import { Icon, SettingsIcon } from "./components/icons";
import { ArrowBack, Description, LeftPanelClose, LeftPanelOpen, Logout } from "./components/iconGlyphs";
import { DocumentsPage, useDocuments, documentStageLabel } from "./pages/DocumentsPage";
import { HistoryPage } from "./pages/HistoryPage";
import { ExtractPage } from "./pages/ExtractPage";
import { ConceptsPage } from "./pages/ConceptsPage";
import {
  readRunTabFromUrl,
  RUN_TAB_CHANGE_EVENT,
} from "./lib/runTabs";
import type { RunTabKey } from "./lib/runTabs";
import "./index.css";
import { confirmNavigationLeave, initializeNavigationHistory, pushNavigationHistory } from "./lib/navigationHistory";

// ---------------------------------------------------------------------------
// Inline styles using the XBRL focused-workspace tokens — only the app-chrome pieces (page/header/main)
// live here. ExtractPage-scoped styles live next to ExtractPage.
// ---------------------------------------------------------------------------

const SIDEBAR_DOCUMENT_LIMIT = 3;

const styles = {
  headerTitle: {
    fontFamily: pwc.fontHeading,
    // Brand wordmark at semibold — the design system sets titles, headings
    // and the wordmark at 600 (two text weights: regular for body/data,
    // semibold for headings; no Light 300).
    fontWeight: pwc.weight.semibold,
    fontSize: 16,
    letterSpacing: "-0.02em",
    color: pwc.black,
    margin: 0,
  } as const,
  topbar: {
    ...ui.appTopbar,
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
  logoutButton: {
    ...ui.buttonGhost,
    ...ui.buttonSm,
    color: tokens.color.text.secondary,
  } as const,
  // The concepts review workspace is a 3-column side-by-side surface that
  // genuinely benefits from the full viewport — the max-width cap left wide
  // gutters and squeezed the grid + PDF. No max-width here; tighter side
  // padding so the columns use most of the screen.
  mainFull: {
    maxWidth: "100%",
    margin: "0 auto",
    padding: `${pwc.space.xl}px ${tokens.space.pageGutter}px 110px`,
    display: "flex",
    flexDirection: "column" as const,
    gap: tokens.space.section,
  } as const,
  // History is full-width like the concepts workspace but keeps the same
  // generous side padding as the standard (capped) page so it reads
  // consistently with the Template/Extract pages.
  mainHistory: {
    maxWidth: 1500,
    margin: "0 auto",
    padding: `${pwc.space.xxl}px ${tokens.space.pageGutter}px 110px`,
    display: "flex",
    flexDirection: "column" as const,
    gap: tokens.space.section,
  } as const,
};

// ---------------------------------------------------------------------------
// App component
// ---------------------------------------------------------------------------

export default function App() {
  useEffect(initializeNavigationHistory, []);
  const [state, dispatch] = useReducer(appReducer, undefined, bootState);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() => {
    try { return window.localStorage.getItem("xbrl-sidebar-collapsed") === "true"; }
    catch { return false; }
  });
  const toggleSidebar = () => {
    const next = !sidebarCollapsed;
    setSidebarCollapsed(next);
    try { window.localStorage.setItem("xbrl-sidebar-collapsed", String(next)); }
    catch { /* Navigation remains usable when storage is unavailable. */ }
  };
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
  // "Field labels" concepts landing. A `/concepts/{id}` run page
  // (selectedRunId set) is the everyday Figures view and stays open.
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
    if (!confirmNavigationLeave()) return;
    apiLogout().finally(() => {
      setUser(null);
      setAuthStatus("anon");
    });
  }, []);

  const documents = useDocuments(authStatus !== "anon");
  const [returnPath, setReturnPath] = useState(() => {
    try { return window.sessionStorage.getItem("xbrl-document-return") || "/"; }
    catch { return "/"; }
  });
  const streamGeneration = useRef(0);
  const [documentName, setDocumentName] = useState<{ id: number; pdf_filename: string } | null>(null);
  const documentLoaded = useCallback((document: { id: number; pdf_filename: string }) => {
    setDocumentName((current) => current?.id === document.id && current.pdf_filename === document.pdf_filename ? current : document);
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
      pushNavigationHistory(
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

  useEffect(() => {
    if (state.view === "settings" || (state.view === "concepts" && state.selectedRunId == null)) return;
    const path = window.location.pathname + window.location.search + window.location.hash;
    setReturnPath(path);
    try { window.sessionStorage.setItem("xbrl-document-return", path); } catch { /* storage is optional */ }
  }, [state.view, state.currentRunId, state.selectedRunId, runTab, extractMode]);

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
      if (route.currentRunId !== stateRef.current.currentRunId && route.view !== "settings") {
        streamGeneration.current += 1;
        sseControllerRef.current?.abort();
        dispatch({ type: "RESET" });
      }
      setExtractMode(window.location.hash === "#new-extraction" ? "new" : "queue");
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
    streamGeneration.current += 1;
    sseControllerRef.current?.abort();
    sseControllerRef.current = null;
    dispatch({ type: "RESET" });
  }, []);

  const showDocuments = (section: "progress" | "history" = "progress") => {
    if (!confirmNavigationLeave()) return;
    handleReset();
    setExtractMode("queue");
    const destination = section === "history" ? "/history" : "/";
    if (window.location.pathname + window.location.search + window.location.hash !== destination) pushNavigationHistory({}, "", destination);
    dispatch({ type: "SET_VIEW", payload: section === "history" ? "history" : "extract" });
    documents.refresh();
  };
  const openDocument = (run: Pick<RunSummaryJson, "id" | "status">) => {
    if (run.id === stateRef.current.selectedRunId && (stateRef.current.view === "history" || stateRef.current.view === "concepts")) return;
    if (!confirmNavigationLeave()) return;
    if (run.id === stateRef.current.currentRunId && stateRef.current.sessionId) {
      dispatch({ type: "SET_VIEW", payload: "extract" });
      return;
    }
    handleReset();
    dispatch({ type: "SET_VIEW", payload: run.status === "draft" ? "extract" : "history" });
    dispatch({ type: run.status === "draft" ? "SET_CURRENT_RUN_ID" : "SET_SELECTED_RUN_ID", payload: run.id });
    setRunTab("overview");
  };
  const addDocuments = () => {
    if (!confirmNavigationLeave()) return;
    handleReset();
    setExtractMode("new");
    pushNavigationHistory({}, "", "/#new-extraction");
  };
  const restoreDocument = () => {
    if (!confirmNavigationLeave()) return;
    const target = /^\/(?:run\/\d+|history(?:\/\d+)?|concepts\/\d+)?(?:[?#].*)?$/.test(returnPath) ? returnPath : "/";
    const route = parseRouteFromPath(target.split(/[?#]/)[0]);
    pushNavigationHistory({}, "", target);
    setRunTab(readRunTabFromUrl() ?? "overview");
    setExtractMode(target.includes("#new-extraction") ? "new" : "queue");
    dispatch({ type: "SET_VIEW", payload: route.view });
    dispatch({ type: "SET_SELECTED_RUN_ID", payload: route.selectedRunId });
    dispatch({ type: "SET_CURRENT_RUN_ID", payload: route.currentRunId });
  };
  const handleUploadFiles = async (files: File[]) => {
    const generation = streamGeneration.current;
    // Upload at most two files at once; each has its own durable preparation job.
    const remaining = [...files];
    const errors: string[] = [];
    await Promise.all([0, 1].map(async () => {
      while (remaining.length) {
        const file = remaining.shift()!;
        try { await uploadPdf(file); }
        catch (error) { errors.push(`${file.name}: ${error instanceof Error ? error.message : "Upload failed"}`); }
      }
    }));
    documents.refresh();
    if (errors.length) throw new Error(errors.join("\n"));
    if (generation === streamGeneration.current && stateRef.current.view === "extract") showDocuments();
  };

  const handleUpload = useCallback(async (file: File) => {
    const generation = streamGeneration.current;
    const result = await uploadPdf(file);
    if (generation !== streamGeneration.current) return result;
    if (stateRef.current.view === "settings" && result.run_id != null) {
      const path = `/run/${result.run_id}`;
      setReturnPath(path);
      try { window.sessionStorage.setItem("xbrl-document-return", path); } catch { /* optional */ }
    }
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
      streamGeneration.current += 1;
      dispatch({ type: "RESET" });
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

  const acceptRunEvent = useCallback((generation: number, event: import("./lib/types").SSEEvent) => {
    if (generation !== streamGeneration.current) return;
    dispatch({ type: "EVENT", payload: event });
    if (event.event === "status" && typeof event.data.run_id === "number") {
      // The start acknowledgement follows the durable audit write. From here
      // every document uses the same saved detail/polling surface. Detaching
      // this monitor does not stop the server-owned job.
      streamGeneration.current += 1;
      sseControllerRef.current?.abort();
      sseControllerRef.current = null;
      dispatch({ type: "RESET" });
      dispatch({ type: "SET_VIEW", payload: "history" });
      dispatch({ type: "SET_SELECTED_RUN_ID", payload: event.data.run_id });
      dispatch({ type: "SET_CURRENT_RUN_ID", payload: null });
      documents.refresh();
    }
  }, [documents.refresh]);

  // Shared plumbing for handleMultiRun + handleRerunAgent. Both flows dispatch
  // every incoming event to the reducer. Transport loss reattaches to the
  // durable run when possible; only pre-start failures become fatal UI errors.
  // Returns the AbortController for the caller to stash.
  const startSSERun = useCallback(
    (sessionId: string, config: RunConfigPayload, endpointPath?: string) => {
      const generation = streamGeneration.current;
      return createMultiAgentSSE(
        sessionId,
        config,
        (event) => acceptRunEvent(generation, event),
        () => {},
        (error, kind) => { if (generation === streamGeneration.current) handleStreamTransportError(error, kind); },
        endpointPath,
      );
    },
    [handleStreamTransportError, acceptRunEvent],
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
    const generation = streamGeneration.current;
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
        if (generation !== streamGeneration.current) return;
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
      if (generation !== streamGeneration.current) return;
      sseControllerRef.current = createMultiAgentSSEByRunId(
        state.currentRunId,
        (event) => acceptRunEvent(generation, event),
        () => {},
        (error, kind) => { if (generation === streamGeneration.current) handleStreamTransportError(error, kind); },
      );
    } else {
      sseControllerRef.current = startSSERun(state.sessionId, config);
    }
  }, [
    state.sessionId,
    state.currentRunId,
    startSSERun,
    handleStreamTransportError,
    acceptRunEvent,
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
      setRunTab(readRunTabFromUrl() ?? "values");
    } else if (state.view === "history") {
      setRunTab(readRunTabFromUrl() ?? "overview");
    }
  }, [state.selectedRunId, state.view]);

  // Auth gate: once /api/auth/me (or any 401) reports anonymous, show login.
  if (authStatus === "anon") {
    return <LoginPage onAuthenticated={checkAuth} />;
  }

  // Settings is a utility surface opened from the sidebar, not a signal that
  // the operator abandoned the uploaded draft. Keep the extract workspace in
  // one stable React tree position while Settings is open so component-owned
  // work such as the optional preview scan is not cancelled by an unmount.
  // The wrapper is display:contents on the extract route so it does not change
  // the main flex layout, and display:none while Settings is visible.
  const extractWorkspace = (
    <ExtractPage
      state={state}
      dispatch={dispatch}
      onOpenNewExtraction={() => {
        setExtractMode("new");
        pushNavigationHistory({}, "", "/#new-extraction");
        window.requestAnimationFrame(() => {
          document.getElementById("new-extraction")?.scrollIntoView({ block: "start" });
        });
      }}
      isAdmin={Boolean(user?.is_admin)}
      handleUpload={handleUpload}
      handleUploadFiles={handleUploadFiles}
      handleMultiRun={handleMultiRun}
      handleAbortAll={handleAbortAll}
      handleAbortAgent={handleAbortAgent}
      handleRerunAgent={handleRerunAgent}
      handleReset={handleReset}
      handleConfigChange={handleDraftConfigChange}
      onOpenRun={(id) => {
        dispatch({ type: "SET_VIEW", payload: "history" });
        dispatch({ type: "SET_SELECTED_RUN_ID", payload: id });
      }}
    />
  );
  const keepExtractWorkspaceMounted =
    state.view === "extract"
    || (state.view === "settings" && state.sessionId != null);
  const documentId = state.view === "extract" ? state.currentRunId : state.selectedRunId;
  const reviewFocused = state.selectedRunId != null && (runTab === "notes" || runTab === "values");
  const documentList = (state.view === "extract" && state.currentRunId == null && !state.sessionId && extractMode === "queue")
    || (state.view === "history" && state.selectedRunId == null);
  const currentFilename = documentName?.id === documentId ? documentName.pdf_filename
    : state.sessionRunId === documentId ? state.filename
    : documents.runs.find((run) => run.id === documentId)?.pdf_filename;
  const selectedDocument = documentId == null ? null
    : documents.runs.find((run) => run.id === documentId)
      ?? { id: documentId, pdf_filename: currentFilename || `Document ${documentId}`, status: state.view === "extract" ? "draft" : "running", preparation: undefined };
  const sidebarDocuments = (selectedDocument
    ? [selectedDocument, ...documents.runs.filter((run) => run.id !== documentId)]
    : documents.runs).slice(0, SIDEBAR_DOCUMENT_LIMIT);
  const contextLabel = state.view === "settings" ? "Settings"
    : state.view === "concepts" && documentId == null ? "Field labels"
    : documentId != null ? "Current filing"
    : state.view === "history" ? "History"
    : extractMode === "new" ? "Add documents" : "Workspace";
  return (
    <div className={`app-shell${sidebarCollapsed ? " app-shell--collapsed" : ""}`}
      style={{ ...ui.appShell, ...(sidebarCollapsed ? { gridTemplateColumns: "72px minmax(0, 1fr)" } : {}) }}>
      <a href="#main-content" className="skip-link">Skip to main content</a>
      <aside className="app-sidebar" aria-label="Workspace navigation" style={{ ...ui.appSidebar, maxHeight: "100dvh" }}>
        <div className="app-rail-brand" style={{ display: "flex", alignItems: "center", gap: 10, padding: "0 8px", minHeight: 28 }}>
          <svg viewBox="0 0 100 100" fill="none" aria-hidden="true" style={{ width: 28, height: 28, flexShrink: 0, color: pwc.orange500 }}>
            <path d="M12 78 L40 22 L52 50 L66 30 L88 78" stroke="currentColor" strokeWidth="11" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
          <span className="app-navigation-label" style={styles.headerTitle}>XBRL Agent</span>
        </div>
        <TopNav view={state.view} extractMode={extractMode} hasDocument={documentId != null}
          onAdd={addDocuments} onViewChange={(view) => showDocuments(view === "history" ? "history" : "progress")} />
        <nav className="app-document-navigation" aria-label="In-progress documents" style={{ display: "flex", flexDirection: "column", gap: 8, minHeight: 0 }}>
          {(documents.runs.length > 0 || documentId != null) && <span className="app-navigation-label" style={{ ...ui.metadata, padding: "0 10px" }}>Documents</span>}
          <div className="app-document-list" style={{ display: "flex", flexDirection: "column", gap: 4, minHeight: 0, overflowY: "auto" }}>
          {sidebarDocuments.map((run) => <button key={run.id} type="button"
            aria-current={documentId === run.id && state.view !== "settings" ? "page" : undefined}
            aria-label={`Open ${run.pdf_filename}`} title={run.pdf_filename} data-tooltip={run.pdf_filename}
            onClick={() => openDocument(run)}
            className={`${uiClass.btnQuiet} app-navigation-link`}
            style={{ ...ui.buttonQuiet, display: "flex", alignItems: "center", justifyContent: "flex-start", gap: 8, padding: 4, fontSize: 13, fontWeight: documentId === run.id ? 600 : 400, flexShrink: 0, minWidth: 0, textAlign: "left", whiteSpace: "normal", background: documentId === run.id && state.view !== "settings" ? pwc.white : "transparent" }}>
            <Icon glyph={Description} size={20} color={documentId === run.id ? pwc.orange500 : pwc.grey700} />
            <span className="app-navigation-label" style={{ minWidth: 0, overflowWrap: "anywhere" }}>
              <span style={{ display: "-webkit-box", WebkitBoxOrient: "vertical", WebkitLineClamp: 2, overflow: "hidden" }}>{run.pdf_filename}</span>
              {documents.runs.some((item) => item.id === run.id) && <span style={{ ...ui.metadata, display: "block", marginTop: 2 }}>
                {documentStageLabel(run)}
              </span>}
            </span>
          </button>)}
          </div>
          <button type="button" className="app-navigation-link" aria-label="View all documents" title="View all documents" style={{ ...ui.buttonQuiet, flexShrink: 0 }} onClick={() => showDocuments()}>View all<span className="app-navigation-label"> documents</span></button>
        </nav>
        <div className="app-rail-footer" style={{ display: "flex", flexDirection: "column", gap: 8, marginTop: "auto" }}>
          <button type="button" aria-label="Settings" data-tooltip="Settings"
            aria-current={state.view === "settings" ? "page" : undefined}
            style={{ ...styles.settingsButton, justifyContent: "flex-start", gap: 10, minHeight: 44, padding: "0 10px", background: state.view === "settings" ? pwc.white : "transparent" }}
            className={`${uiClass.btnSubtle} app-navigation-link`}
            onClick={() => { if (confirmNavigationLeave()) dispatch({ type: "SET_VIEW", payload: "settings" }); }}>
            <SettingsIcon /><span className="app-navigation-label">Settings</span>
          </button>
          <button type="button" className={`${uiClass.btnQuiet} app-rail-toggle app-navigation-link`}
            aria-label={sidebarCollapsed ? "Expand navigation" : "Collapse navigation"}
            data-tooltip={sidebarCollapsed ? "Expand navigation" : "Collapse navigation"}
            aria-expanded={!sidebarCollapsed} aria-controls="app-primary-navigation" onClick={toggleSidebar}
            style={{ ...ui.buttonQuiet, justifyContent: "flex-start", gap: 10, minHeight: 44, padding: "0 10px" }}>
            <Icon glyph={sidebarCollapsed ? LeftPanelOpen : LeftPanelClose} size={20} />
            <span className="app-navigation-label">{sidebarCollapsed ? "Expand sidebar" : "Collapse sidebar"}</span>
          </button>
          {user && <div className="app-user" style={{ display: "flex", alignItems: "center", gap: 10, borderTop: `1px solid ${pwc.grey200}`, padding: "16px 8px 0", marginTop: 8 }}>
            <span aria-hidden="true" style={{ display: "grid", placeItems: "center", width: 30, height: 30, flexShrink: 0, borderRadius: "50%", background: pwc.grey100, fontSize: 12 }}>{(user.display_name || user.email).slice(0, 2).toUpperCase()}</span>
            <div className="app-navigation-label" style={{ minWidth: 0 }}>
              <div style={{ fontSize: 13, overflowWrap: "anywhere" }}>{user.display_name || user.email}</div>
              <div style={ui.metadata}>{user.is_admin ? "Administrator" : "User"}</div>
            </div>
          </div>}
        </div>
      </aside>
      <div className="app-workspace" style={{ minWidth: 0 }}>
      <header className="app-topbar" style={{ ...styles.topbar, zIndex: 30, borderBottom: `1px solid ${pwc.grey200}`, height: 64 }}>
        <span style={ui.metadata}>{contextLabel}</span>
        <div style={{ ...styles.headerRight, marginLeft: "auto" }}>
          {user?.provider !== "dev" && <button type="button" aria-label="Log out" data-tooltip="Log out" onClick={handleLogout} style={styles.logoutButton}><Icon glyph={Logout} size={20} /></button>}
        </div>
      </header>
      <main id="main-content" tabIndex={-1} className="app-main" style={reviewFocused || state.view === "concepts" ? styles.mainFull : styles.mainHistory}>
        {(state.view === "settings" || (state.view === "concepts" && state.selectedRunId == null)) &&
          <button type="button" style={{ ...ui.buttonGhost, alignSelf: "flex-start" }} onClick={restoreDocument}><ArrowBack size={20} />Back to {returnPath.match(/\/(?:run|history|concepts)\/\d+/) ? "document" : "work queue"}</button>}
        {state.view === "settings" && <SettingsPage isAdmin={Boolean(user?.is_admin)} currentEmail={user?.email}
          onFieldLabels={canonicalEnabled ? () => { if (!confirmNavigationLeave()) return; dispatch({ type: "SET_VIEW", payload: "concepts" }); dispatch({ type: "SET_SELECTED_RUN_ID", payload: null }); } : undefined} />}
        {documentList && <DocumentsPage documents={documents} section={state.view === "history" ? "history" : "progress"}
          onSection={showDocuments} onAdd={addDocuments} onOpen={openDocument} />}
        {keepExtractWorkspaceMounted && !documentList && <div aria-hidden={state.view === "settings" ? true : undefined}
          style={{ display: state.view === "extract" ? "contents" : "none" }}>{extractWorkspace}</div>}
        {state.view === "concepts" && state.selectedRunId == null && <ConceptsPage runId={null} />}
        {state.view !== "settings" && state.view !== "extract" && state.selectedRunId != null &&
          <HistoryPage key={state.selectedRunId} canonicalEnabled={canonicalEnabled} selectedId={state.selectedRunId}
            initialRunTab={state.view === "concepts" ? "values" : undefined} hideDetailBack onDocumentLoaded={documentLoaded}
            onSelectRun={(id) => { if (id == null) showDocuments("history"); else openDocument({ id, status: "running" }); }}
            onResumeDraft={(id) => openDocument({ id, status: "draft" })} />}
      </main>
      </div>
      <SuccessToast toast={state.toast} onDismiss={() => dispatch({ type: "DISMISS_TOAST" })} />
    </div>
  );
}
