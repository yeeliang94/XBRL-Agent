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

// Shared fetch helper — parses JSON error bodies for useful messages.
// A 401 anywhere means the session expired mid-use: broadcast it so the app
// shell can drop back to the login page (the "any 401 ⇒ show login" rule).
async function apiFetch<T>(url: string, options?: RequestInit): Promise<T> {
  const res = await fetch(url, options);
  if (!res.ok) {
    if (res.status === 401) {
      window.dispatchEvent(new CustomEvent("auth:unauthorized"));
    }
    let detail = `Request failed (${res.status})`;
    try {
      const body = await res.json();
      detail = body.detail || body.message || detail;
    } catch { /* no JSON body */ }
    throw new Error(detail);
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
  if (!res.ok) throw new Error(`auth check failed (${res.status})`);
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
    entity_memory: boolean;
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
  total: number;
  drafts: number;
  completedThisMonth: number;
}

/** The few most-recent runs, newest first. Reuses the runs list (which is
 *  already ordered `created_at DESC` server-side) capped to `limit`. */
export async function fetchRecentRuns(limit = 5): Promise<RunSummaryJson[]> {
  const res = await fetchRuns({ limit });
  return res.runs;
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

/** Fetch the three headline counts in parallel. Each call asks for a single
 *  row (`limit: 1`) purely to read the server's `total` for that filter —
 *  we never use the returned rows, so this stays cheap regardless of how
 *  many runs match. */
export async function fetchHomeStats(): Promise<HomeStats> {
  const [all, drafts, completed] = await Promise.all([
    fetchRuns({ limit: 1 }),
    fetchRuns({ status: "draft", limit: 1 }),
    fetchRuns({ status: "completed", dateFrom: currentMonthStart(), limit: 1 }),
  ]);
  return {
    total: all.total,
    drafts: drafts.total,
    completedThisMonth: completed.total,
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

import type { BenchmarkJson, EvalScoreJson } from "./types";

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

// ---------------------------------------------------------------------------
// Scanned-PDF → readable-document feature (docs/PLAN-scanned-pdf-to-doc.md).
// Standalone utility, independent of extraction.
// ---------------------------------------------------------------------------

export interface DocConvertStatus {
  job_id: number;
  status: "queued" | "running" | "done" | "failed";
  current_page: number;
  total_pages: number;
  original_filename: string;
  error: string | null;
}

/** Upload a PDF and launch a conversion. Returns the new job id. */
export async function startDocConvert(
  file: File,
): Promise<{ job_id: number; status: string }> {
  const form = new FormData();
  form.append("file", file);
  return apiFetch("/api/doc-convert", { method: "POST", body: form });
}

/** Poll the current status/progress of a conversion job. */
export async function getDocConvertStatus(jobId: number): Promise<DocConvertStatus> {
  return apiFetch(`/api/doc-convert/${jobId}`);
}

/** Same-origin URL for the converted HTML (rendered in an iframe). */
export function docConvertViewUrl(jobId: number): string {
  return `/api/doc-convert/${jobId}/view`;
}

/** Same-origin URL for the Word download (an <a download> target). */
export function docConvertDocxUrl(jobId: number): string {
  return `/api/doc-convert/${jobId}/download/docx`;
}
