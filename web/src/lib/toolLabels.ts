// Shared tool-label module. Used by the live extract timeline, the scout
// pre-run panel, and the history replay so all three render identical wording.
//
// Three pure functions:
//   humanToolName — friendly verb-phrase for a tool name ("view_pdf_pages" → "Checking PDF pages")
//   argsPreview   — short inline arg summary shown next to the tool name
//   resultSummary — short right-side badge text + tone, derived from prose result summaries

/** Friendly labels for every extraction and scout tool. */
export const TOOL_LABELS: Record<string, string> = {
  // Extraction agent tools
  read_template: "Reading template",
  view_pdf_pages: "Checking PDF pages",
  fill_workbook: "Filling workbook",
  verify_totals: "Verifying totals",
  save_result: "Saving result",
  // Scout agent tools
  find_toc: "Locating table of contents",
  view_pages: "Checking PDF pages",
  parse_toc_text: "Reading table of contents",
  check_variant_signals: "Checking variant signals",
  discover_notes: "Discovering notes",
  save_infopack: "Saving scout results",
};

/** Return a human-readable label for a tool name, falling back to Title Case. */
export function humanToolName(name: string): string {
  const mapped = TOOL_LABELS[name];
  if (mapped) return mapped;
  return name.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

// --- fill_workbook field parsing -------------------------------------------

/** One field_entry from fill_workbook's fields_json arg. */
export interface FillField {
  sheet: string;
  field_label?: string;
  row?: number;
  col?: number;
  value: unknown;
  section?: string;
  evidence?: string;
}

/**
 * Parse fill_workbook's fields_json arg into typed field entries. Accepts
 * either a JSON string or an already-parsed object; tolerates either
 * {fields: [...]} or a bare array. Returns null on any parse failure so
 * callers can degrade cleanly.
 */
export function parseFillFields(args: Record<string, unknown>): FillField[] | null {
  try {
    const raw = typeof args.fields_json === "string"
      ? JSON.parse(args.fields_json)
      : args.fields_json;
    const arr = (raw as { fields?: FillField[] } | null)?.fields
      ?? (Array.isArray(raw) ? (raw as FillField[]) : null);
    if (Array.isArray(arr) && arr.length > 0) return arr;
  } catch {
    // Invalid JSON — fall through to null.
  }
  return null;
}

// --- argsPreview -----------------------------------------------------------

// En-dash for the collapsed range summary (e.g. "7 pages (1–7)").
const EN_DASH = "\u2013";

/**
 * Return true if `pages` is strictly ascending with step 1 (e.g. [3,4,5,6]).
 * The range-summary form ("N pages (1–7)") is only safe to use in this case;
 * for non-contiguous sets it would misleadingly imply every page between
 * first and last is included.
 */
function isContiguousRange(pages: number[]): boolean {
  for (let i = 1; i < pages.length; i++) {
    if (pages[i] !== pages[i - 1] + 1) return false;
  }
  return true;
}

/** Format a list of page numbers in English-style ("pages 1, 2 and 3"). */
function formatPageList(pages: number[]): string {
  if (pages.length === 0) return "";
  if (pages.length === 1) return `page ${pages[0]}`;
  // Only collapse to "N pages (first–last)" when the set is strictly
  // consecutive. Scattered page sets fall through to the Oxford-list form
  // so we never lie about which pages were requested.
  if (pages.length >= 5 && isContiguousRange(pages)) {
    const first = pages[0];
    const last = pages[pages.length - 1];
    return `${pages.length} pages (${first}${EN_DASH}${last})`;
  }
  if (pages.length === 2) return `pages ${pages[0]} and ${pages[1]}`;
  // 3+ pages: "pages a, b, c and d" — Oxford 'and' before the last.
  const head = pages.slice(0, -1).join(", ");
  const tail = pages[pages.length - 1];
  return `pages ${head} and ${tail}`;
}

/**
 * Short inline preview of a tool call's arguments. Returns an empty string
 * for tools with no meaningful inline summary — the caller hides the preview
 * line in that case.
 */
export function argsPreview(toolName: string, args: Record<string, unknown>): string {
  // view_pdf_pages (extraction) and view_pages (scout) share the same formatter.
  if (toolName === "view_pdf_pages" || toolName === "view_pages") {
    const pages = args.pages;
    if (Array.isArray(pages) && pages.every((p) => typeof p === "number")) {
      return formatPageList(pages as number[]);
    }
    return "";
  }

  if (toolName === "fill_workbook") {
    const fields = parseFillFields(args);
    if (fields) {
      const sheet = fields[0]?.sheet;
      return sheet ? `${fields.length} fields \u2192 ${sheet}` : `${fields.length} fields`;
    }
    return "";
  }

  if (toolName === "read_template") {
    const path = args.path as string | undefined;
    if (path) return path.split("/").pop() || path;
    return "";
  }

  if (toolName === "parse_toc_text") {
    const text = args.text as string | undefined;
    if (!text) return "";
    // Short excerpt — first ~40 chars, with an ellipsis if we cut it short.
    const MAX = 40;
    return text.length > MAX ? `${text.slice(0, MAX)}...` : text;
  }

  if (toolName === "discover_notes") {
    // Just a hint that we're reading from the face page's cross-refs;
    // the face_text arg is too long to preview inline.
    return args.face_text ? "from face page" : "";
  }

  // Tools with no meaningful preview: find_toc, save_infopack, save_result,
  // verify_totals, check_variant_signals, etc. Return empty so the row stays tight.
  return "";
}

// --- resultSummary ---------------------------------------------------------

export type ResultTone = "success" | "warn";

export interface ResultSummary {
  text: string;
  tone: ResultTone;
}

// Pre-compiled regexes so the hot path doesn't re-parse on every event.
const RE_WROTE_N = /wrote\s+(\d+)/i;
const RE_BALANCED = /Balanced:\s*(True|False)/i;
// find_toc result comes through coordinator.py as str(dict)[:800]. Each TOC
// entry is a small dict like {'name': 'SOFP', 'type': 'SOFP', 'page': 5},
// so counting 'name': occurrences gives us the number of entries without
// trying to parse Python's repr format. The bare-number "N entries" form is
// also supported for any future backend that emits it directly.
const RE_NAME_KEY = /['"]name['"]\s*:/g;
const RE_N_ENTRIES = /(\d+)\s*entries/i;

/**
 * Convert a raw prose result_summary into a short success/warn badge.
 * Returns null when the summary is unknown or unparseable so the caller can
 * fall back to the duration badge. Never throws — any regex failure or type
 * surprise degrades to null.
 */
export function resultSummary(toolName: string, summary: string): ResultSummary | null {
  try {
    if (!summary) return null;

    if (toolName === "fill_workbook") {
      const m = summary.match(RE_WROTE_N);
      if (m) return { text: `${m[1]} values`, tone: "success" };
      return null;
    }

    if (toolName === "verify_totals") {
      const m = summary.match(RE_BALANCED);
      if (m) {
        return m[1].toLowerCase() === "true"
          ? { text: "balanced", tone: "success" }
          : { text: "mismatch", tone: "warn" };
      }
      return null;
    }

    if (toolName === "find_toc") {
      // Prefer the explicit "N entries" form if a future backend emits it.
      const explicit = summary.match(RE_N_ENTRIES);
      if (explicit) return { text: `${explicit[1]} entries`, tone: "success" };
      // Otherwise count 'name': keys inside the stringified dict the scout
      // tool returns today. Zero matches → null so the card falls back to
      // the duration badge rather than showing "0 entries".
      const matches = summary.match(RE_NAME_KEY);
      if (matches && matches.length > 0) {
        return { text: `${matches.length} entries`, tone: "success" };
      }
      return null;
    }

    if (toolName === "save_infopack") {
      // Any non-empty summary means scout persisted its infopack successfully.
      return { text: "saved", tone: "success" };
    }

    return null;
  } catch {
    return null;
  }
}
