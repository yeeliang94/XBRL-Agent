import type { UploadResponse, SettingsResponse, ExtendedSettingsResponse } from "./types";

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
