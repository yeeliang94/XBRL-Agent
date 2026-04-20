import type {
  UploadResponse,
  SettingsResponse,
  ExtendedSettingsResponse,
  RunListResponse,
  RunDetailJson,
  RunAgentJson,
  RunsFilterParams,
  SSEEvent,
} from "./types";

// Shared fetch helper — parses JSON error bodies for useful messages
async function apiFetch<T>(url: string, options?: RequestInit): Promise<T> {
  const res = await fetch(url, options);
  if (!res.ok) {
    let detail = `Request failed (${res.status})`;
    try {
      const body = await res.json();
      detail = body.detail || body.message || detail;
    } catch { /* no JSON body */ }
    throw new Error(detail);
  }
  return res.json();
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
  body: Partial<{ api_key: string; model: string; proxy_url: string }>,
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
  }));
  return { ...raw, agents };
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
