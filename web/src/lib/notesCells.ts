// Step 8/9+ of docs/Archive/PLAN-NOTES-RICH-EDITOR.md — frontend client for the
// notes_cells GET/PATCH endpoints. Kept in `lib/` (not a React hook) so
// tests can exercise the URL + body contract directly; the editor tab
// calls these from `useEffect` / its save handler.

/** One row returned by GET /api/runs/{runId}/notes_cells. Mirrors the
 *  wire shape the backend emits — evidence is nullable because writer
 *  prose rows can leave col D/F empty when the agent chose not to
 *  cite a specific page.
 *
 *  `sanitizer_warnings` is only populated on the PATCH response (not
 *  on GET) — it carries the list of disallowed tags/attributes the
 *  backend stripped from the user's most recent edit so the editor
 *  can display "we removed <script>" rather than silently swapping
 *  content. Empty list when the sanitiser was a no-op; undefined on
 *  GET responses which do not carry a write-time warning snapshot. */
export interface NotesCell {
  row: number;
  label: string;
  // `kind` distinguishes the two row shapes the projection returns
  // (PLAN-notes-template-registry). Optional so older callers/tests that
  // omit it default to prose.
  kind?: "prose" | "numeric";
  html: string;
  evidence: string | null;
  source_pages: number[];
  updated_at: string;
  sanitizer_warnings?: string[];
  // How this cell got its table styling (schema v29): "ops" = agent observed
  // it at extraction, "floor" = deterministic house style, "unstyled" = plain,
  // "formatter" = the manual notes formatter pass styled it. null on blank
  // rows / reviewer-authored / legacy runs. Read-only signal so the operator
  // can spot cells that want a formatter pass (only unstyled/floor get a chip).
  style_source?: "ops" | "floor" | "unstyled" | "formatter" | null;
  // Prose registry identity (full-template projection) — present on rows that
  // came from notes_nodes; null on off-template/legacy filled rows.
  node_uuid?: string | null;
  xbrl_concept_id?: string | null;
  // Numeric rows: the canonical concept + its per-column values. `values`
  // keys are cy/py (Company filings) or group_cy/group_py/company_cy/
  // company_py (Group filings); a null value means the cell is unfilled.
  concept_uuid?: string | null;
  values?: Record<string, number | null>;
}

/** One sheet section — what the editor renders as a heading + stack of
 *  cells. Cells come pre-ordered by `row` so the UI does not re-sort.
 *  `kind` mirrors the row kind so the section can pick the right editor. */
export interface NotesSheet {
  sheet: string;
  kind?: "prose" | "numeric";
  rows: NotesCell[];
}

/** Maps a numeric `values` key to the (period, entity_scope) the facts API
 *  expects. Shared by the numeric editor so column → fact routing is in one
 *  place. */
export const NUMERIC_VALUE_COLUMNS: Record<
  string,
  { label: string; period: "CY" | "PY"; entity_scope: "Company" | "Group" }
> = {
  cy: { label: "Current year", period: "CY", entity_scope: "Company" },
  py: { label: "Prior year", period: "PY", entity_scope: "Company" },
  group_cy: { label: "Group CY", period: "CY", entity_scope: "Group" },
  group_py: { label: "Group PY", period: "PY", entity_scope: "Group" },
  company_cy: { label: "Company CY", period: "CY", entity_scope: "Company" },
  company_py: { label: "Company PY", period: "PY", entity_scope: "Company" },
};

export interface NotesCellsResponse {
  sheets: NotesSheet[];
}

export interface NotesFormatStatus {
  status: "idle" | "running" | "done";
  sheet: string;
  model?: string | null;
  summary?: string | null;
  confidence?: number | null;
  changed_rows?: number;
  /** Rows the CAS write skipped because they were edited during the pass. */
  skipped_rows?: number[];
  /** True when a pre-format snapshot exists — enables "Revert formatting". */
  can_revert?: boolean;
  error?: string | null;
  /** Failure taxonomy code (timeout | turn_budget | low_confidence | ...). */
  error_type?: string | null;
  prompt_tokens?: number;
  completion_tokens?: number;
}

// Slot order of the five notes templates, mirroring the NotesTemplateType
// enum in notes_types.py. MFRS and MPERS share the same sheet names
// (notes_types.py:71), so one array covers both filing standards. The DB
// returns rows ordered by (sheet, row) which is alphabetical — this array
// is the canonical review order users expect (Corp Info → Acc Policies →
// List of Notes → Issued Capital → Related Party).
const SHEET_SLOT_ORDER: readonly string[] = [
  "Notes-CI",
  "Notes-SummaryofAccPol",
  "Notes-Listofnotes",
  "Notes-Issuedcapital",
  "Notes-RelatedPartytran",
];

/** Return a new array of sheets in MBRS slot order. Known sheet names
 *  sort by their position in `SHEET_SLOT_ORDER`; unknown names fall to
 *  the end, alphabetised among themselves so a future template addition
 *  is still visible (just in a stable default place) until the frontend
 *  catches up. */
export function sortSheetsBySlot(sheets: NotesSheet[]): NotesSheet[] {
  const TAIL = SHEET_SLOT_ORDER.length;
  // `slice()` clones the input so callers can keep relying on the
  // original array (defensive — React state updates should be
  // non-mutating anyway).
  return sheets.slice().sort((a, b) => {
    const ai = SHEET_SLOT_ORDER.indexOf(a.sheet);
    const bi = SHEET_SLOT_ORDER.indexOf(b.sheet);
    const aKey = ai === -1 ? TAIL : ai;
    const bKey = bi === -1 ? TAIL : bi;
    if (aKey !== bKey) return aKey - bKey;
    // Both sheets unknown — tie-break alphabetically so the order is
    // at least deterministic instead of depending on DB insert order.
    return a.sheet.localeCompare(b.sheet);
  });
}

async function apiFetch<T>(url: string, options?: RequestInit): Promise<T> {
  const res = await fetch(url, options);
  if (!res.ok) {
    let detail = `Request failed (${res.status})`;
    try {
      const body = await res.json();
      detail = body.detail || body.message || detail;
    } catch {
      /* no JSON body */
    }
    const err = new Error(detail) as Error & { status?: number };
    err.status = res.status;
    throw err;
  }
  return res.json();
}

/** Fetch every notes cell for a run, grouped by sheet. 404s propagate
 *  as Error#status=404 so the caller can distinguish "bad run id" from
 *  a transport-level failure. */
export async function fetchNotesCells(runId: number): Promise<NotesCellsResponse> {
  return apiFetch<NotesCellsResponse>(`/api/runs/${runId}/notes_cells`);
}

export async function launchNotesFormatter(
  runId: number,
  sheet: string,
  model?: string,
): Promise<NotesFormatStatus & { ok: boolean; already_running?: boolean }> {
  return apiFetch<NotesFormatStatus & { ok: boolean; already_running?: boolean }>(
    `/api/runs/${runId}/notes-format`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      // Omit `model` when unset so the server falls back to the configured
      // notes_formatter default / the run's model (api/notes_formatter.py).
      body: JSON.stringify(model ? { sheet, model } : { sheet }),
    },
  );
}

export async function fetchNotesFormatStatus(
  runId: number,
  sheet: string,
): Promise<NotesFormatStatus> {
  return apiFetch<NotesFormatStatus>(
    `/api/runs/${runId}/notes-format/status?sheet=${encodeURIComponent(sheet)}`,
  );
}

/** Restore the sheet's pre-format HTML from the formatter snapshot. Rows
 *  whose CONTENT was edited after the pass are kept (listed in
 *  skipped_rows) — revert only undoes styling. */
export async function revertNotesFormatter(
  runId: number,
  sheet: string,
): Promise<{ ok: boolean; restored_rows: number; skipped_rows: number[] }> {
  return apiFetch<{ ok: boolean; restored_rows: number; skipped_rows: number[] }>(
    `/api/runs/${runId}/notes-format/revert`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sheet }),
    },
  );
}

/** PATCH one cell's HTML. Returns the updated cell shape (includes the
 *  server-side sanitised HTML and refreshed `updated_at`). The caller
 *  should reconcile the returned row into local state so an editor
 *  displaying the old HTML after save sees the cleaned form. */
export async function patchNotesCell(
  runId: number,
  sheet: string,
  row: number,
  html: string,
): Promise<NotesCell & { sheet: string }> {
  return apiFetch<NotesCell & { sheet: string }>(
    `/api/runs/${runId}/notes_cells/${encodeURIComponent(sheet)}/${row}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ html }),
    },
  );
}

/** Sentinel returned by {@link parseNumericInput} when the text isn't a valid
 *  number. Distinct from `null` (empty → clear the cell) and from any finite
 *  number. */
export const INVALID_NUMBER = Symbol("invalid-number");

/** Parse an accountant-formatted number from a numeric-note input.
 *
 *  Reviewers paste straight from financial statements, where numbers carry
 *  thousands separators and parenthesised negatives. We strip commas and
 *  whitespace and read `(95)` as `-95` so those paste cleanly instead of
 *  silently failing as `NaN` (PLAN-notes-template-registry code-review fix).
 *
 *  Returns:
 *   * `null`           — empty input (the caller clears the cell);
 *   * a finite number  — parsed value;
 *   * {@link INVALID_NUMBER} — text that isn't a number, so the caller can
 *     surface an explicit error instead of writing NaN.
 */
export function parseNumericInput(
  raw: string,
): number | null | typeof INVALID_NUMBER {
  const trimmed = raw.trim();
  if (trimmed === "") return null;
  // Accountant negative: "(1,234)" → -1234.
  const paren = /^\((.*)\)$/.exec(trimmed);
  const body = (paren ? paren[1] : trimmed).replace(/[\s,]/g, "");
  if (body === "") return INVALID_NUMBER;
  const n = Number(body);
  if (!Number.isFinite(n)) return INVALID_NUMBER;
  return paren ? -n : n;
}

/** PATCH one numeric-note value into run_concept_facts. Numeric notes live in
 *  the canonical fact store (PLAN-notes-template-registry Track B), so they
 *  reuse the same per-concept facts endpoint the Values tab uses — not the
 *  prose notes_cells PATCH. `value` of null clears the cell. */
export async function patchNotesFact(
  runId: number,
  conceptUuid: string,
  value: number | null,
  period: "CY" | "PY",
  entityScope: "Company" | "Group",
): Promise<unknown> {
  return apiFetch<unknown>(`/api/runs/${runId}/facts/${conceptUuid}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ value, period, entity_scope: entityScope }),
  });
}
