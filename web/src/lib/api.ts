import type {
  UploadResponse,
  SettingsResponse,
  ExtendedSettingsResponse,
  RunListResponse,
  RunSummaryJson,
  RunDetailJson,
  RunAgentJson,
  RunsFilterParams,
  SSEEvent,
  AgentTraceJson,
} from "./types";
import type { ClipboardFormatOptions } from "./clipboardFormat";
import { ApiError } from "./errors";

// Shared fetch helper — turns a non-OK response into an `ApiError` carrying a
// plain-English message (safe to render) plus the raw detail for a "Technical
// details" disclosure. A 401 anywhere means the session expired mid-use:
// broadcast it so the app shell can drop back to the login page (the "any
// 401 ⇒ show login" rule).
async function apiFetch<T>(url: string, options?: RequestInit): Promise<T> {
  const res = await fetch(url, options);
  if (!res.ok) {
    if (res.status === 401) {
      window.dispatchEvent(new CustomEvent("auth:unauthorized"));
    }
    let body: unknown = null;
    try {
      body = await res.json();
    } catch { /* no JSON body */ }
    throw ApiError.fromResponse(res.status, body);
  }
  return res.json();
}

// ---------------------------------------------------------------------------
// Auth (PLAN auth Phase 1.4). Session cookies are same-origin (the SPA is
// served by the same FastAPI app), so the browser attaches them automatically
// — no `credentials` override needed.
// ---------------------------------------------------------------------------

export interface AuthMe {
  email: string;
  display_name: string;
  provider: string;
  // Admin role (schema v20) — gates the Settings → Users tab. Optional so an
  // older backend that omits it reads as non-admin.
  is_admin?: boolean;
}

/** One account as returned by the admin user-management API (never the hash). */
export interface AdminUser {
  email: string;
  display_name: string;
  disabled: boolean;
  is_admin: boolean;
  has_password: boolean;
  created_at: string;
  password_set_at: string | null;
}

export type LoginResult =
  | { ok: true }
  | { ok: false; status: number; detail: string };

/** Current user, or null when not authenticated (401). In AUTH_MODE=dev the
 *  backend returns a synthetic dev user so the login page never shows. */
export async function getAuthMe(): Promise<AuthMe | null> {
  const res = await fetch("/api/auth/me");
  if (res.status === 401) return null;
  if (!res.ok) throw ApiError.fromResponse(res.status, null);
  return res.json();
}

export async function loginPassword(email: string, password: string): Promise<LoginResult> {
  const res = await fetch("/api/auth/login/password", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  if (res.ok) return { ok: true };
  let detail = "Login failed.";
  try {
    const body = await res.json();
    detail = body.detail || detail;
  } catch { /* no JSON body */ }
  return { ok: false, status: res.status, detail };
}

export async function logout(): Promise<void> {
  await fetch("/api/auth/logout", { method: "POST" });
}

/** Change my own password (re-auth with the current one). Throws (via apiFetch)
 *  with the server's detail message on 403 (wrong current) / 422 (too short).
 *  Wrong-current is deliberately 403 not 401 so it surfaces inline instead of
 *  tripping apiFetch's global "session expired" logout (Codex review P2). */
export async function changePassword(
  currentPassword: string,
  newPassword: string,
): Promise<{ ok: boolean }> {
  return apiFetch("/api/auth/change-password", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
  });
}

// ---------------------------------------------------------------------------
// Admin: user management (gated server-side on is_admin). Each helper throws
// with the server's detail message on failure (incl. the 409 last-admin guard).
// ---------------------------------------------------------------------------

export async function adminListUsers(): Promise<AdminUser[]> {
  const res = await apiFetch<{ users: AdminUser[] }>("/api/admin/users");
  return res.users;
}

export async function adminAddUser(body: {
  email: string;
  display_name?: string;
  password: string;
  is_admin?: boolean;
}): Promise<{ ok: boolean; user: AdminUser }> {
  return apiFetch("/api/admin/users", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function adminSetDisabled(email: string, disabled: boolean): Promise<{ ok: boolean }> {
  const action = disabled ? "disable" : "enable";
  return apiFetch(`/api/admin/users/${encodeURIComponent(email)}/${action}`, { method: "POST" });
}

export async function adminResetPassword(email: string, password: string): Promise<{ ok: boolean }> {
  return apiFetch(`/api/admin/users/${encodeURIComponent(email)}/reset-password`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ password }),
  });
}

export async function adminSetAdmin(email: string, isAdmin: boolean): Promise<{ ok: boolean; user: AdminUser }> {
  return apiFetch(`/api/admin/users/${encodeURIComponent(email)}/admin`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ is_admin: isAdmin }),
  });
}

/** Throttled activity ping — bumps the sliding-window idle timer. Best-effort;
 *  a failure is swallowed (the next real API call surfaces a true expiry). */
export async function refreshAuth(): Promise<void> {
  try {
    await fetch("/api/auth/refresh", { method: "POST" });
  } catch { /* offline / transient — ignore */ }
}

export async function uploadPdf(file: File): Promise<UploadResponse> {
  const formData = new FormData();
  formData.append("file", file);
  return apiFetch<UploadResponse>("/api/upload", { method: "POST", body: formData });
}

export async function getSettings(): Promise<SettingsResponse> {
  return apiFetch<SettingsResponse>("/api/settings");
}

export async function updateSettings(
  body: Partial<{
    api_key: string;
    model: string;
    proxy_url: string;
    // Extended fields already accepted server-side by POST /api/settings.
    // Adding them to the public signature lets the inline scout model
    // dropdown (and any future per-agent-model controls) persist through
    // the same helper instead of re-implementing the fetch.
    default_models: Record<string, string>;
    scout_enabled_default: boolean;
    tolerance_rm: number;
    auto_review: boolean;
    // Clean-run spot-check (issue 1): toggle + depth.
    spot_check: boolean;
    spot_check_mode: "light" | "full";
    entity_memory: boolean;
    // Firm-wide notes-table style theme (docs/PLAN-notes-table-theme.md).
    // The server validates + cleans it before persisting.
    notes_table_style: ClipboardFormatOptions;
  }>,
): Promise<{ status: string }> {
  return apiFetch<{ status: string }>("/api/settings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function testConnection(
  body: Partial<{ proxy_url: string; api_key: string; model: string }>,
): Promise<{ status: string; model?: string; latency_ms?: number; message?: string }> {
  return apiFetch("/api/test-connection", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function getResultJson(sessionId: string): Promise<Record<string, unknown>> {
  return apiFetch(`/api/result/${sessionId}/result.json`);
}

export async function getExtendedSettings(): Promise<ExtendedSettingsResponse> {
  return apiFetch<ExtendedSettingsResponse>("/api/settings");
}

// ---------------------------------------------------------------------------
// Abort / Rerun
// ---------------------------------------------------------------------------

/** Cancel all running agents in a session. */
export async function abortAll(sessionId: string): Promise<{ cancelled: number }> {
  return apiFetch(`/api/abort/${sessionId}`, { method: "POST" });
}

/** Cancel a single agent within a session. */
export async function abortAgent(sessionId: string, agentId: string): Promise<{ cancelled: string }> {
  return apiFetch(`/api/abort/${sessionId}/${agentId}`, { method: "POST" });
}

// ---------------------------------------------------------------------------
// Phase 5: History API
//
// Thin wrappers over the /api/runs endpoints. URL building lives here (not in
// the components) so the query-string format stays consistent and testable.
// ---------------------------------------------------------------------------

/** Build the querystring for `GET /api/runs`. Empty/undefined filters are
 *  dropped so the URL reads cleanly and the backend never sees `q=`. */
function buildRunsQuery(params: RunsFilterParams): string {
  const qs = new URLSearchParams();
  if (params.q) qs.set("q", params.q);
  if (params.status) qs.set("status", params.status);
  if (params.model) qs.set("model", params.model);
  // Backend middleware remaps `from` / `to` -> `date_from` / `date_to`.
  // Use the human-friendly names on the wire.
  if (params.dateFrom) qs.set("from", params.dateFrom);
  if (params.dateTo) qs.set("to", params.dateTo);
  if (params.includeSuiteChildren) qs.set("include_suite_children", "true");
  if (params.limit != null) qs.set("limit", String(params.limit));
  if (params.offset != null) qs.set("offset", String(params.offset));
  const str = qs.toString();
  return str ? `?${str}` : "";
}

/** Fetch a page of past runs, optionally filtered. */
export async function fetchRuns(params: RunsFilterParams): Promise<RunListResponse> {
  const url = `/api/runs${buildRunsQuery(params)}`;
  return apiFetch<RunListResponse>(url);
}

// ---------------------------------------------------------------------------
// Homepage split-hero data (PLAN-homepage-redesign.md)
//
// The Extract landing page surfaces a small "home base" beside the upload
// card: four headline counts plus the few most-recent runs. Both build on
// the existing `GET /api/runs` endpoint — no new backend route — so the URL
// format and error handling stay in one place (`fetchRuns`).
// ---------------------------------------------------------------------------

/** Headline counts shown in the homepage stat tiles.
 *
 *  `lastStatus` is intentionally NOT computed here — it comes from the
 *  most-recent run in `fetchRecentRuns`, so the homepage derives it from
 *  that list rather than paying for a fourth round-trip. */
export interface HomeStats {
  drafts: number;
  completedThisMonth: number;
  needsReview: number;
  active: number;
}

/** The few most-recent runs for the landing page, with real RESULTS surfaced
 *  ahead of drafts (UX-QA #10). A draft row is created at upload time, so the
 *  raw newest-first page is often all abandoned drafts — a first-time viewer
 *  then sees no evidence the tool has ever produced a result. We fetch a wider
 *  window and stable-partition non-drafts first (each group still newest-first),
 *  then cap to `limit`. */
export async function fetchRecentRuns(limit = 5): Promise<RunSummaryJson[]> {
  const res = await fetchRuns({ limit: Math.max(limit * 4, 20) });
  const rows = res.runs;
  const results = rows.filter((r) => r.status !== "draft");
  const drafts = rows.filter((r) => r.status === "draft");
  return [...results, ...drafts].slice(0, limit);
}

/** First day of the current month as `YYYY-MM-01`, in the browser's local
 *  timezone. The backend expands a bare date to `…T00:00:00Z`, so the
 *  "this month" boundary is evaluated in UTC — close enough for a dashboard
 *  count, and the only place a month-edge run could be miscounted. */
function currentMonthStart(): string {
  const now = new Date();
  const month = String(now.getMonth() + 1).padStart(2, "0");
  return `${now.getFullYear()}-${month}-01`;
}

/** Fetch the headline counts in parallel. Each call asks for a single
 *  row (`limit: 1`) purely to read the server's `total` for that filter —
 *  we never use the returned rows, so this stays cheap regardless of how
 *  many runs match. Completed-with-errors is counted twice intentionally:
 *  once with the month floor for throughput, and once without it for the
 *  all-time review queue. */
export async function fetchHomeStats(): Promise<HomeStats> {
  const monthStart = currentMonthStart();
  const [drafts, completed, completedWithErrorsThisMonth, completedWithErrorsAll, correctionExhausted, running] = await Promise.all([
    fetchRuns({ status: "draft", limit: 1 }),
    fetchRuns({ status: "completed", dateFrom: monthStart, limit: 1 }),
    // A run that finished with advisory errors still finished (UX-QA #10) —
    // count it so "Completed this month" doesn't under-report real work.
    fetchRuns({ status: "completed_with_errors", dateFrom: monthStart, limit: 1 }),
    fetchRuns({ status: "completed_with_errors", limit: 1 }),
    fetchRuns({ status: "correction_exhausted", limit: 1 }),
    fetchRuns({ status: "running", limit: 1 }),
  ]);
  return {
    drafts: drafts.total,
    completedThisMonth: completed.total + completedWithErrorsThisMonth.total,
    needsReview: completedWithErrorsAll.total + correctionExhausted.total,
    active: running.total,
  };
}

/** Fetch the hydrated detail view for a single run.
 *
 *  Legacy rows (pre-Phase-6.5) may omit the per-agent `events` array
 *  entirely. We normalise missing/null values to `[]` here so UI
 *  consumers (RunDetailView, AgentTimeline) can always spread the
 *  field without a null check. */
export async function fetchRunDetail(runId: number): Promise<RunDetailJson> {
  const raw = await apiFetch<RunDetailJson>(`/api/runs/${runId}`);
  const agents: RunAgentJson[] = (raw.agents ?? []).map((a) => ({
    ...a,
    events: Array.isArray(a.events) ? (a.events as SSEEvent[]) : [],
    // v8: default the telemetry fields so consumers can read them without
    // null-checking against legacy payloads.
    turns: Array.isArray(a.turns) ? a.turns : [],
  }));
  return { ...raw, agents };
}

/** Fetch the verbatim conversation trace for one agent of a run (v8).
 *  Returns the full request/response messages plus per-turn metrics so the
 *  Telemetry tab can show exactly what was sent and returned each turn. */
export async function fetchAgentTrace(
  runId: number,
  statement: string,
): Promise<AgentTraceJson> {
  assertRunId("fetchAgentTrace", runId);
  return apiFetch<AgentTraceJson>(
    `/api/runs/${runId}/agents/${encodeURIComponent(statement)}/trace`,
  );
}

/** Hard-delete a run row (DB only — the on-disk output folder is left alone). */
export async function deleteRun(runId: number): Promise<{ deleted: number }> {
  return apiFetch(`/api/runs/${runId}`, { method: "DELETE" });
}

/** Rescue a run wedged in `running` status (UX-QA #2). A dead row is flipped
 *  to `aborted`; a genuinely-live one is cancelled. Backend returns 409 if the
 *  run isn't running and 404 if it's unknown. */
export async function forceAbortRun(
  runId: number,
): Promise<{ aborted: number; mode: string }> {
  return apiFetch(`/api/runs/${runId}/force-abort`, { method: "POST" });
}

/** Bulk-delete abandoned draft runs (uploads that were never started). Returns
 *  the count removed. Draft-only server-side — cannot touch real runs. */
export async function deleteDraftRuns(): Promise<{ deleted: number }> {
  return apiFetch(`/api/runs/drafts`, { method: "DELETE" });
}

/** Build the download URL for a past run's merged workbook.
 *  Returned as a plain string so the caller can hand it to an `<a href>` or
 *  `window.location.href =` — streaming a file through fetch + Blob would
 *  add complexity we don't need. Asserts an integer so callers can't
 *  accidentally interpolate `NaN` / floats / injected strings into the path. */
export function downloadFilledUrl(runId: number): string {
  if (!Number.isInteger(runId) || runId <= 0) {
    throw new Error(`downloadFilledUrl: runId must be a positive integer (got ${runId})`);
  }
  return `/api/runs/${runId}/download/filled`;
}

// ---------------------------------------------------------------------------
// Source-PDF viewer (Review Workspace M1)
//
// Page images are served as plain PNGs so the viewer can use them as an
// `<img src>` directly — same rationale as downloadFilledUrl (no fetch+Blob
// dance). URL building lives here so the path format stays in one place.
// ---------------------------------------------------------------------------

function assertRunId(fn: string, runId: number): void {
  if (!Number.isInteger(runId) || runId <= 0) {
    throw new Error(`${fn}: runId must be a positive integer (got ${runId})`);
  }
}

/** URL for the source PDF's page-count metadata. */
export function pdfInfoUrl(runId: number): string {
  assertRunId("pdfInfoUrl", runId);
  return `/api/runs/${runId}/pdf/info`;
}

/** Build the `<img src>` URL for one rendered source-PDF page (1-indexed). */
export function pdfPageUrl(runId: number, page: number, dpi?: number): string {
  assertRunId("pdfPageUrl", runId);
  if (!Number.isInteger(page) || page < 1) {
    throw new Error(`pdfPageUrl: page must be a positive integer (got ${page})`);
  }
  const qs = dpi != null ? `?dpi=${dpi}` : "";
  return `/api/runs/${runId}/pdf/page/${page}.png${qs}`;
}

/** Fetch the source PDF's page count, or null when the run has no stored PDF
 *  (legacy / CLI runs). The viewer treats null as "no source available". */
export async function fetchPdfPageCount(runId: number): Promise<number | null> {
  try {
    const data = await apiFetch<{ pages: number }>(pdfInfoUrl(runId));
    return data.pages;
  } catch {
    return null;
  }
}

// ---------------------------------------------------------------------------
// Gold-standard eval / benchmark library (v16)
// ---------------------------------------------------------------------------

import type {
  BenchmarkJson, EvalScoreJson, RepeatGroupJson,
  SuiteSummaryJson, SuiteJson, SuiteRunSummaryJson, SuiteRunLaunch,
  SuiteEstimateJson, SuiteRunDetailJson, SuiteResultsJson, SuiteCompareJson,
  SlotDiffJson, ReviewerLiftJson,
} from "./types";

/** List every benchmark in the library. */
export async function fetchBenchmarks(): Promise<BenchmarkJson[]> {
  const data = await apiFetch<{ benchmarks: BenchmarkJson[] }>("/api/benchmarks");
  // Tolerate a malformed/empty body (e.g. a stubbed fetch in tests) — always
  // resolve to an array so callers can `.filter`/`.map` without guarding.
  return Array.isArray(data?.benchmarks) ? data.benchmarks : [];
}

/** Create a benchmark from a human-filled MBRS template workbook. The template
 *  set is auto-detected server-side from the workbook's sheets. */
export async function createBenchmark(args: {
  file: File;
  name: string;
  filing_standard: string;
  filing_level: string;
  document?: string;
}): Promise<{
  ok: boolean;
  id: number;
  ingested: number;
  statements: string[];
  // Number of gradeable cells dropped because the workbook's formulas were
  // never recalculated (un-cached SOCIE/rollup formulas → None on read), plus
  // an actionable message. Null/absent when nothing was lost.
  skipped_formula_cells?: number;
  warning?: string | null;
}> {
  const form = new FormData();
  form.append("file", args.file);
  form.append("name", args.name);
  form.append("filing_standard", args.filing_standard);
  form.append("filing_level", args.filing_level);
  if (args.document) form.append("document", args.document);
  return apiFetch("/api/benchmarks", { method: "POST", body: form });
}

/**
 * Seed a benchmark directly from a finished run's extracted facts — the
 * lossless alternative to uploading a workbook (an un-recalculated export
 * drops its SOCIE matrix + cross-sheet rollups, so sub-sheets vanish).
 */
export async function createBenchmarkFromRun(args: {
  run_id: number;
  name: string;
  document?: string;
}): Promise<{
  ok: boolean;
  id: number;
  ingested: number;
  statements: string[];
  source_run_id: number;
  source_run_status: string;
}> {
  return apiFetch("/api/benchmarks/from-run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(args),
  });
}

/** Evals workspace (Step C4): the face template variants available for a
 *  filing family, so the mTool-gold form can offer a variant-precise picker. */
export async function fetchEvalTemplates(
  standard: string, level: string,
): Promise<{ template_id: string; statement: string; variant: string; label: string }[]> {
  const data = await apiFetch<{ templates: { template_id: string; statement: string; variant: string; label: string }[] }>(
    `/api/eval/templates?standard=${encodeURIComponent(standard)}&level=${encodeURIComponent(level)}`,
  );
  return Array.isArray(data?.templates) ? data.templates : [];
}

/** Evals workspace (Step C4): create a benchmark by reverse-ingesting a
 *  human-filled mTool workbook. The unit is declared by the operator (no
 *  auto-guess) and the variant set is explicit (gotcha #21). Returns the
 *  ingest report (matched-by-statement / unmatched rows / scale warning). */
export async function createBenchmarkFromMtool(args: {
  file: File;
  name: string;
  filing_standard: string;
  filing_level: string;
  unit: "full" | "thousands";
  template_ids: string[];
  document?: string;
}): Promise<{
  ok: boolean;
  id: number;
  ingested: number;
  matched_by_statement: Record<string, number>;
  unmatched_rows: { sheet: string; row: number; label: string; values: number[] }[];
  prose_notes_captured: number;
  scale_warning: string | null;
  statements: string[];
}> {
  const form = new FormData();
  form.append("file", args.file);
  form.append("name", args.name);
  form.append("filing_standard", args.filing_standard);
  form.append("filing_level", args.filing_level);
  form.append("unit", args.unit);
  form.append("template_ids", JSON.stringify(args.template_ids));
  if (args.document) form.append("document", args.document);
  return apiFetch("/api/benchmarks/from-mtool", { method: "POST", body: form });
}

// --- Evals workspace: suites + batch runner + results (Phase E/F) ---

export async function fetchSuites(): Promise<SuiteSummaryJson[]> {
  const data = await apiFetch<{ suites: SuiteSummaryJson[] }>("/api/suites");
  return Array.isArray(data?.suites) ? data.suites : [];
}

export async function createSuite(name: string): Promise<SuiteJson> {
  return apiFetch("/api/suites", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
}

export async function getSuite(id: number): Promise<SuiteJson> {
  return apiFetch(`/api/suites/${id}`);
}

export async function renameSuite(id: number, name: string): Promise<SuiteJson> {
  return apiFetch(`/api/suites/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
}

export async function deleteSuite(id: number): Promise<void> {
  await apiFetch(`/api/suites/${id}`, { method: "DELETE" });
}

export async function addSuiteDoc(args: {
  suiteId: number;
  file: File;
  label?: string;
  filing_standard: string;
  filing_level: string;
  benchmark_id?: number | null;
}): Promise<{ doc_id: number; suite: SuiteJson }> {
  const form = new FormData();
  form.append("file", args.file);
  if (args.label) form.append("label", args.label);
  form.append("filing_standard", args.filing_standard);
  form.append("filing_level", args.filing_level);
  if (args.benchmark_id != null) form.append("benchmark_id", String(args.benchmark_id));
  return apiFetch(`/api/suites/${args.suiteId}/docs`, { method: "POST", body: form });
}

export async function deleteSuiteDoc(suiteId: number, docId: number): Promise<void> {
  await apiFetch(`/api/suites/${suiteId}/docs/${docId}`, { method: "DELETE" });
}

export async function listSuiteRuns(suiteId: number): Promise<SuiteRunSummaryJson[]> {
  const data = await apiFetch<{ suite_run_list: SuiteRunSummaryJson[] }>(
    `/api/suites/${suiteId}/runs`,
  );
  return Array.isArray(data?.suite_run_list) ? data.suite_run_list : [];
}

export async function estimateSuiteRun(
  suiteId: number, launch: SuiteRunLaunch,
): Promise<SuiteEstimateJson> {
  return apiFetch(`/api/suites/${suiteId}/estimate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(launch),
  });
}

export async function launchSuiteRun(
  suiteId: number, launch: SuiteRunLaunch,
): Promise<{ suite_run_id: number; status: string }> {
  return apiFetch(`/api/suites/${suiteId}/run`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(launch),
  });
}

export async function resumeSuiteRun(suiteId: number, suiteRunId: number): Promise<void> {
  await apiFetch(`/api/suites/${suiteId}/runs/${suiteRunId}/resume`, { method: "POST" });
}

export async function stopSuiteRun(suiteId: number, suiteRunId: number): Promise<void> {
  await apiFetch(`/api/suites/${suiteId}/runs/${suiteRunId}/stop`, { method: "POST" });
}

export async function getSuiteRun(
  suiteId: number, suiteRunId: number,
): Promise<SuiteRunDetailJson> {
  return apiFetch(`/api/suites/${suiteId}/runs/${suiteRunId}`);
}

export async function fetchSuiteResults(suiteId: number): Promise<SuiteResultsJson> {
  return apiFetch(`/api/suites/${suiteId}/results`);
}

export async function compareSuiteRuns(
  suiteId: number, a: number, b: number,
): Promise<SuiteCompareJson> {
  return apiFetch(`/api/suites/${suiteId}/compare?a=${a}&b=${b}`);
}

/** Value-level drill-down for one compare row (Step 12). */
export async function fetchCompareSlotDiff(
  suiteId: number, a: number, b: number, docId: number,
): Promise<SlotDiffJson> {
  return apiFetch(`/api/suites/${suiteId}/compare/slots?a=${a}&b=${b}&doc_id=${docId}`);
}

/** What the reviewer pass contributed to a graded run (Step 12). */
export async function fetchReviewerLift(runId: number): Promise<ReviewerLiftJson> {
  return apiFetch(`/api/runs/${runId}/reviewer-lift`);
}

/** Re-grade a run against its benchmark's CURRENT gold (Step 8). */
export async function reGradeRun(
  runId: number,
): Promise<{ old_score: number | null; new_score: number | null; score: EvalScoreJson }> {
  return apiFetch(`/api/runs/${runId}/re-grade`, { method: "POST" });
}

/** Delete a benchmark (cascades to its templates + gold facts + scores). */
export async function deleteBenchmark(id: number): Promise<void> {
  await apiFetch(`/api/benchmarks/${id}`, { method: "DELETE" });
}

/** Fetch the scorecard for a run, or null when the run wasn't graded. */
export async function fetchRunEval(runId: number): Promise<EvalScoreJson | null> {
  try {
    return await apiFetch<EvalScoreJson>(`/api/runs/${runId}/eval`);
  } catch {
    return null;
  }
}

/** Evals workspace (v30): fetch a repeat group + its consistency result. */
export async function fetchRepeatGroup(
  groupId: number,
): Promise<RepeatGroupJson | null> {
  try {
    return await apiFetch<RepeatGroupJson>(`/api/repeat-groups/${groupId}`);
  } catch {
    return null;
  }
}
