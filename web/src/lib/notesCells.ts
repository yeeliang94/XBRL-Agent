// Step 8/9+ of docs/PLAN-NOTES-RICH-EDITOR.md — frontend client for the
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
  html: string;
  evidence: string | null;
  source_pages: number[];
  updated_at: string;
  sanitizer_warnings?: string[];
}

/** One sheet section — what the editor renders as a heading + stack of
 *  cells. Cells come pre-ordered by `row` so the UI does not re-sort. */
export interface NotesSheet {
  sheet: string;
  rows: NotesCell[];
}

export interface NotesCellsResponse {
  sheets: NotesSheet[];
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
