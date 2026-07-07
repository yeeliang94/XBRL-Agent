// One place that turns any thrown/caught value into a plain-English message a
// non-technical auditor can act on — plus an optional "technical details"
// string for an expand-to-view block. The goal: no user ever sees
// "[object Object]", a bare "HTTP 422", a Python traceback, or a raw exception
// class as the whole error. See docs/PLAN-ui-ux-plain-language-overhaul.md §1.
//
// Usage:
//   catch (e) { setError(userMessage(e)); setDetail(technicalDetail(e)); }
// and, in the fetch plumbing:
//   throw ApiError.fromResponse(status, body);

/** Map an HTTP status to a plain, actionable sentence — never a bare code. */
export function statusSentence(status: number): string {
  switch (status) {
    case 400:
      return "The request couldn't be completed. Please check your entries and try again.";
    case 401:
      return "Your session expired. Please sign in again.";
    case 403:
      return "You don't have permission to do that.";
    case 404:
      return "We couldn't find what you asked for — it may have been deleted.";
    case 409:
      return "That clashes with the current state. Refresh the page and try again.";
    case 413:
      return "That file or content is too large to accept.";
    case 422:
      return "Some of the details weren't valid. Please check your entries and try again.";
    case 429:
      return "Too many requests in a short time. Please wait a moment and try again.";
    default:
      if (status >= 500) return "The server ran into a problem. Please try again in a moment.";
      if (status >= 400) return "The request couldn't be completed. Please try again.";
      return "Something went wrong. Please try again.";
  }
}

/** A single pydantic-style validation entry: `{loc, msg, type}`. */
interface FieldError {
  loc?: unknown[];
  msg?: string;
}

function isFieldError(v: unknown): v is FieldError {
  return typeof v === "object" && v !== null && "msg" in v;
}

/** Title-case the last meaningful segment of a pydantic `loc` path
 *  (`["body","statements"]` → "Statements") for a readable prefix. */
function fieldName(loc: unknown[] | undefined): string | null {
  if (!Array.isArray(loc) || loc.length === 0) return null;
  const last = loc[loc.length - 1];
  if (typeof last !== "string" || last === "body") return null;
  return last.charAt(0).toUpperCase() + last.slice(1).replace(/_/g, " ");
}

/**
 * Pull a readable string out of a JSON error body, whatever shape it takes:
 *   - `{detail: "..."}`                      → the string
 *   - `{detail: [{loc, msg}]}` (pydantic)    → "Field: msg" lines
 *   - `{detail: {input_errors: [...]}}`      → joined lines (mTool)
 *   - `{message: "..."}`                     → the string
 * Returns null when nothing usable is present, so the caller can fall back to
 * a status sentence. Never returns "[object Object]".
 */
export function extractErrorDetail(body: unknown): string | null {
  if (typeof body === "string") return null; // a raw string body isn't a structured detail
  if (typeof body !== "object" || body === null) return null;

  const b = body as Record<string, unknown>;
  const detail = b.detail;

  if (typeof detail === "string" && detail.trim()) return detail.trim();

  if (Array.isArray(detail)) {
    const lines = detail
      .filter(isFieldError)
      .map((e) => {
        const name = fieldName(e.loc);
        const msg = (e.msg ?? "").toString().trim();
        return name ? `${name}: ${msg}` : msg;
      })
      .filter(Boolean);
    if (lines.length) return lines.join("\n");
  }

  if (typeof detail === "object" && detail !== null) {
    const d = detail as Record<string, unknown>;
    const arr = d.input_errors ?? d.errors;
    if (Array.isArray(arr) && arr.length) {
      return arr.map((x) => (typeof x === "string" ? x : String(x))).join("\n");
    }
    if (typeof d.error === "string" && d.error.trim()) return d.error.trim();
  }

  if (typeof b.message === "string" && b.message.trim()) return b.message.trim();
  return null;
}

/** True when a string is only an HTTP-status artefact ("HTTP 422",
 *  "Request failed (500)") — the kind of message we must never show raw. */
function isBareStatusMessage(s: string): boolean {
  const t = s.trim();
  return (
    /^HTTP\s*\d{3}$/i.test(t) ||
    /^Request failed \(\d{3}\)$/i.test(t) ||
    /^\d{3}$/.test(t) ||
    t === "[object Object]"
  );
}

/**
 * Error thrown by the fetch plumbing. Carries a friendly `message` (safe to
 * render as-is) plus the raw `technical` string and `status` for a collapsed
 * "Technical details" disclosure.
 */
export class ApiError extends Error {
  readonly status?: number;
  readonly technical?: string;

  constructor(message: string, opts?: { status?: number; technical?: string }) {
    super(message);
    this.name = "ApiError";
    this.status = opts?.status;
    this.technical = opts?.technical;
  }

  /** Build from a non-OK fetch response's status + parsed JSON body. */
  static fromResponse(status: number, body: unknown): ApiError {
    const detail = extractErrorDetail(body);
    // Trust a real sentence from the backend; otherwise use the status
    // sentence. Either way the raw detail is kept for the disclosure.
    const message = detail && !isBareStatusMessage(detail) ? detail : statusSentence(status);
    const technical = detail && detail !== message ? detail : undefined;
    return new ApiError(message, { status, technical });
  }
}

/** The plain-English message to show the user for any caught value. */
export function userMessage(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) {
    const m = err.message?.trim();
    if (!m || isBareStatusMessage(m)) return "Something went wrong. Please try again.";
    return m;
  }
  if (typeof err === "string" && err.trim() && !isBareStatusMessage(err)) return err.trim();
  return "Something went wrong. Please try again.";
}

/** The raw technical string for a collapsed disclosure, or null when there's
 *  nothing extra worth showing beyond the friendly message. */
export function technicalDetail(err: unknown): string | null {
  if (err instanceof ApiError) return err.technical ?? null;
  return null;
}
