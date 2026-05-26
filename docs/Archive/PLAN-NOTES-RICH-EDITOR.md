# Implementation Plan: Notes Rich-Text Editor (Red-Green TDD)

**Overall Progress:** `100%` (Phases 1–5 landed: 13 of 13)
**Brainstorm reference:** session 2026-04-24 (locked decisions in summary below)
**Related docs:** `docs/NOTES-PIPELINE.md`, `docs/MPERS.md`, `CLAUDE.md` gotcha #14
**Last Updated:** 2026-04-24

## Summary

Notes agents currently emit plain text into Excel cells (sheets 10–14
MFRS / 11–15 MPERS). Users must hand-copy that content into M-Tool's
HTML-aware text editor for the final XBRL — Excel strips all formatting
(bold, italics, lists, tables), so the manual workaround is "generate
HTML → render in browser → copy from browser → paste into M-Tool".

This plan rewires the notes pipeline so agents emit **HTML** for every
cell, persists that HTML in the DB as the canonical payload, surfaces
it in a post-run **WYSIWYG editor** (one row per template line) with a
per-cell **copy-as-rich-text** button, and continues to render Excel
on download by **flattening HTML → plaintext** (tables flattened to
pipe/newline form). Edits write back to the JSON; Excel is regenerated
on demand. Agent re-runs **clobber** user edits (no merge logic) and
the UI confirms before destruction.

Strict Red-Green-Refactor TDD throughout: every implementation step
opens with a failing test that encodes the desired behaviour *before*
the code that satisfies it. No green commit lands without its red test
in the same commit.

## Key Decisions (locked)

- **Canonical store = DB (`notes_cells` table), not xlsx.** xlsx is
  re-rendered on every download from the JSON payload + template. No
  in-place patching of the workbook.
- **Editor: TipTap** (React, ProseMirror under the hood, plays nice
  with inline-styles-only constraint per CLAUDE.md gotcha #7). Backend
  stripper: **BeautifulSoup4** (deterministic, well-known API).
- **Full HTML subset.** User-tested: M-Tool round-trips bold, italic,
  lists, tables, headings via the OS clipboard `text/html` channel
  when copied from a rendered browser DOM. No subsetting needed.
- **30k char cap on RENDERED text** (BeautifulSoup `get_text()` length).
  Preserves the existing semantic limit without the agent paying the
  tag-overhead tax.
- **Tables in Excel: flatten to pipe + newline.** `<table>` → header row
  joined by ` | ` then each body row joined the same way, separated by
  `\n`. Honest about the loss; user gets the UI for the rich version.
- **Re-run clobbers edits.** No `user_edited` flag, no merge UI; the
  regenerate button on the UI shows a confirm dialog before clobbering.
- **Editable columns: content only.** Col B (prose) / col B+C+D+E
  (numeric on Group) / col X (SOCIE if SOCIE ever joins this surface).
  Evidence column (D Company / F Group) is **read-only** in the editor
  — it's the audit trail.
- **TDD discipline — one red test per behaviour.** Each step opens with
  a failing test for the right reason. Refactor only with green.

## Pre-Implementation Checklist

- [ ] 🟥 User confirms scope (this plan)
- [ ] 🟥 Confirm `bs4` (BeautifulSoup4) is acceptable as a backend dep
- [ ] 🟥 Confirm `@tiptap/react` + `@tiptap/starter-kit` + `@tiptap/extension-table` are acceptable as frontend deps
- [ ] 🟥 No in-flight PR touching `notes/agent.py`, `notes/writer.py`,
      `notes/payload.py`, `db/schema.py`, or `prompts/_notes_*.md`

---

## Tasks

### Phase 1: Backend Foundation — HTML→Text Stripper + Schema 🟩 Done

#### Step 1: HTML→Excel-plaintext converter (`notes/html_to_text.py`) 🟩 Done
**Why first:** every other step depends on this. No agent rewrite, no
download route, no cap-enforcement until we can deterministically turn
HTML into the plaintext form Excel will hold.

**Failing tests (RED) — `tests/test_notes_html_to_text.py`:**
- `test_strips_inline_tags_to_plaintext` — `<p>Hello <b>world</b></p>` → `"Hello world"`.
- `test_paragraph_breaks_become_double_newline` — two `<p>` blocks → one `\n\n` between.
- `test_unordered_list_renders_as_dash_lines` — `<ul><li>a</li><li>b</li></ul>` → `"- a\n- b"`.
- `test_ordered_list_renders_as_numbered_lines` — `<ol><li>x</li><li>y</li></ol>` → `"1. x\n2. y"`.
- `test_table_flattens_to_pipe_separated_rows` — 2-col, 2-row table → `"H1 | H2\nA | B"`.
- `test_nested_tables_flatten_recursively` — inner table becomes nested pipe lines.
- `test_headings_get_blank_line_before_and_after` — `<h3>X</h3><p>Y</p>` → `"X\n\nY"`.
- `test_rendered_length_helper` — `rendered_length("<p>" + "x" * 10 + "</p>")` returns `10`.
- `test_truncate_to_rendered_length_clips_at_grapheme_boundary` — does not split mid-tag and appends footer.
- `test_empty_or_none_input_returns_empty_string`.
- `test_malformed_html_does_not_raise` — `<p>unclosed` parses without exception.

**Implementation:**
- New file `notes/html_to_text.py` with `html_to_excel_text(html: str, source_pages: list[int] | None = None) -> str` and `rendered_length(html: str) -> int`.
- Pure functions; no imports from `notes/writer.py` or other notes modules — keeps the module reusable.
- BeautifulSoup4 with `html.parser` (no external lib needed).

**Verify:** `pytest tests/test_notes_html_to_text.py -v` all green.

**Acceptance:** Module is importable and pure; covers prose + lists + tables + headings; rendered-length helper used by Step 3 cap enforcement.

**Complexity:** S (single file, ~150 LOC, all unit-tested).

---

#### Step 2: `notes_cells` table + repository helpers 🟩 Done
**Why second:** payload persistence layer for everything downstream.
Schema bump is additive-only so existing v2 DBs migrate forward
automatically.

**Failing tests (RED):**
- `tests/test_db_schema_v3.py::test_v2_to_v3_creates_notes_cells_table`
- `tests/test_db_schema_v3.py::test_v3_init_is_idempotent`
- `tests/test_db_schema_v3.py::test_notes_cells_unique_on_run_sheet_row`
- `tests/test_db_repository_notes_cells.py::test_upsert_notes_cell_inserts_then_updates`
- `tests/test_db_repository_notes_cells.py::test_list_notes_cells_for_run_returns_in_sheet_row_order`
- `tests/test_db_repository_notes_cells.py::test_delete_notes_cells_for_run_sheet_clears_all_rows_for_one_sheet`

**Implementation:**
- Bump `db/schema.py::CURRENT_SCHEMA_VERSION` to 3.
- Add a `_v3_migrate(conn)` block analogous to the v2 migration: create `notes_cells` if missing, FK to `runs(id)` ON DELETE CASCADE, UNIQUE `(run_id, sheet, row)`.
  ```sql
  CREATE TABLE notes_cells (
      id            INTEGER PRIMARY KEY AUTOINCREMENT,
      run_id        INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
      sheet         TEXT NOT NULL,        -- 'Notes-CI', 'Notes-SummaryofAccPol', ...
      row           INTEGER NOT NULL,
      label         TEXT NOT NULL,        -- col-A label, denormalised for the UI
      html          TEXT NOT NULL,
      evidence      TEXT,                 -- read-only, mirrors writer's col D/F
      source_pages  TEXT,                 -- JSON array string
      updated_at    TEXT NOT NULL,
      UNIQUE(run_id, sheet, row)
  );
  CREATE INDEX ix_notes_cells_run_id ON notes_cells(run_id);
  ```
- Add `db/repository.py::upsert_notes_cell`, `list_notes_cells_for_run`, `delete_notes_cells_for_run_sheet`.
- Two-phase migration commit (BEGIN IMMEDIATE) mirroring the v2 pattern.

**Verify:** `pytest tests/test_db_schema_v3.py tests/test_db_repository_notes_cells.py -v` all green; existing `tests/test_db_schema_v2.py` still green.

**Dependencies:** Step 1 not strictly required; can run in parallel.

**Complexity:** S (schema + 3 small helpers, mirrors v2 pattern).

---

### Phase 2: Notes Pipeline — HTML output end to end 🟩 Done

#### Step 3: Update `NotesPayload` cap enforcement to count rendered chars 🟩 Done
**Why third:** the agent prompt rewrite (Step 4) tells the model "30k of
rendered text" — that contract must be enforced server-side first or
the agent will silently overrun.

**Failing tests (RED) — `tests/test_notes_payload_html_cap.py`:**
- `test_payload_with_html_under_30k_rendered_passes` — 25k of text wrapped in `<p>` etc., total HTML >32k chars, validates clean.
- `test_payload_over_30k_rendered_truncates_with_footer` — 35k of rendered text → truncated with `[truncated -- see PDF pages N]` footer; truncation happens at a tag boundary, not mid-tag.
- `test_existing_plaintext_payloads_still_work` — backwards-compat: a `content` with no tags behaves identically to today.

**Implementation:**
- `notes/writer.py::_truncate_with_footer` now consults `notes/html_to_text.rendered_length`.
- When rendered length > `CELL_CHAR_LIMIT`, truncate at the **HTML char index** that produces a rendered length just under the cap, then append the footer **as HTML** (`<p><em>[truncated -- see PDF pages N, M]</em></p>`).
- Helper lives in `notes/html_to_text.py`: `truncate_html_to_rendered_length(html: str, max_rendered: int) -> str`.

**Verify:** all three tests green; `pytest tests/test_notes_writer.py tests/test_notes_char_limit.py -v` still green (existing plaintext path unaffected).

**Dependencies:** Step 1.

**Complexity:** M (the truncation-at-tag-boundary is the tricky bit; expect to lean on BeautifulSoup's tree walker).

---

#### Step 4: Rewrite notes prompts to emit HTML 🟩 Done
**Why fourth:** this is the **bulk of the work** (per brainstorm risk
flagged at the top). All five per-template prompts plus `_notes_base.md`
plus the LoN sub-agent prompt must require HTML output. Backwards-compat
with plaintext payloads stays in for one release window so we can ship
this incrementally.

**Failing tests (RED):**
- `tests/test_notes_prompts_emit_html_contract.py::test_base_prompt_specifies_html_output_format`
- `tests/test_notes_prompts_emit_html_contract.py::test_base_prompt_lists_supported_tags` — asserts `<p>`, `<ul>`, `<ol>`, `<li>`, `<table>`, `<tr>`, `<td>`, `<th>`, `<strong>`, `<em>`, `<h3>`, `<br>` are mentioned.
- `tests/test_notes_prompts_emit_html_contract.py::test_base_prompt_explicitly_forbids_markdown` — guards against the model defaulting to `**bold**`.
- `tests/test_notes_prompts_emit_html_contract.py::test_per_template_prompts_inherit_html_format[corporate_info]` and one per template (5 cases, parametrised).
- `tests/test_notes_prompts_emit_html_contract.py::test_listofnotes_subagent_prompt_emits_html` — Sheet-12 sub-coordinator prompt.

**Implementation:**
- Edit `prompts/_notes_base.md` `=== CELL FORMAT ===` block: replace plaintext rules with the HTML contract + tag whitelist + table example.
- Add `=== ALLOWED HTML TAGS ===` section with the whitelist and one short example each.
- Per-template prompts: only need updates where they currently include `\n\n` instructions — replace with `<p>`. Most just inherit from base.
- `notes/listofnotes_subcoordinator.py` sub-agent prompt: same update.

**Verify:** all assertions green; manual smoke-read of each prompt to confirm phrasing isn't contradictory.

**Dependencies:** Step 3 (so the cap contract referenced in the prompt is accurate).

**Complexity:** M (5 prompts + base + subagent, plus careful red tests so we catch leaks).

---

#### Step 5: HTML payload validation in writer + agent tool 🟩 Done
**Why fifth:** belt-and-braces guard. If the model regresses to
plaintext, we want a visible warning, not a silent loss of formatting.
Conversely, if the model produces HTML we don't expect (script tags,
event handlers), we want it stripped before persisting.

**Failing tests (RED) — `tests/test_notes_html_payload_validation.py`:**
- `test_payload_with_script_tag_strips_it` — `<p>x</p><script>...</script>` → script removed; warning recorded.
- `test_payload_with_inline_event_handler_strips_it` — `<a onclick="evil()">x</a>` → handler removed.
- `test_payload_with_no_tags_is_wrapped_in_paragraph` — agent emits `"Hello"` → stored as `<p>Hello</p>`. Backwards-compat shim.
- `test_payload_with_disallowed_tag_is_logged_as_warning` — `<iframe>` removed and added to `NotesWriteResult.warnings`.

**Implementation:**
- New `notes/html_sanitize.py::sanitize_notes_html(html: str) -> tuple[str, list[str]]` returning sanitised HTML + list of warnings. Whitelist: the tag list from Step 4. Use BeautifulSoup `decompose()` for disallowed nodes; strip `on*` attributes and `style` from all elements.
- `notes/writer.py::_write_row` calls `sanitize_notes_html` on prose payloads before truncation.
- `notes/agent.py::write_notes` tool already collects errors — extend to surface sanitiser warnings the same way.

**Verify:** `pytest tests/test_notes_html_payload_validation.py -v` green; existing `tests/test_notes_writer.py`, `tests/test_notes_writer_dedup.py` still green.

**Dependencies:** Step 1 (uses bs4).

**Complexity:** S (single file + 2 small touches).

---

#### Step 6: Persist payloads to `notes_cells` after each notes agent succeeds 🟩 Done
**Why sixth:** server now has a place to put HTML and a guarantee that
what's there is well-formed. This step bridges the agent output to the
DB so the UI in Phase 3 has something to read.

**Failing tests (RED):**
- `tests/test_notes_cells_persistence.py::test_successful_notes_run_writes_cells_to_db` — mock a single notes agent producing 3 payloads; assert 3 rows in `notes_cells` after the coordinator finishes.
- `tests/test_notes_cells_persistence.py::test_rerun_of_same_sheet_clobbers_prior_cells` — first run writes 3, second run writes 2 → DB has only the 2 new ones.
- `tests/test_notes_cells_persistence.py::test_failed_notes_agent_does_not_persist_cells` — exhausted retry budget → no rows for that sheet.
- `tests/test_notes_cells_persistence.py::test_evidence_column_is_persisted_alongside_html` — evidence string + source_pages JSON round-trip.

**Implementation:**
- After `write_notes_workbook` succeeds in `notes/coordinator.py`, call a new helper `_persist_notes_cells(run_id, sheet_name, payloads)` that:
  1. Opens an audit conn via the existing `_open_audit_conn` shim used by recorders.
  2. `delete_notes_cells_for_run_sheet(run_id, sheet)` to clobber any prior cells (re-run semantics).
  3. `upsert_notes_cell(...)` per payload.
- Wire `run_id` through `NotesRunConfig` so the coordinator has it (currently the audit `run_id` is owned by `server.py::run_multi_agent_stream` — pass it in via the config the same way `output_dir` flows).
- New helper lives in `notes/persistence.py` (keep `coordinator.py` from growing further).

**Verify:** all four tests green; smoke run with the FINCO PDF (out-of-band) shows the DB populated.

**Dependencies:** Steps 2, 5.

**Complexity:** M (cross-module wiring + a new module + run_id plumbing).

---

#### Step 7: Excel download regenerated from `notes_cells` (HTML → text) 🟩 Done
**Why seventh:** the existing `/api/runs/{id}/download/filled` returns
the file written at extraction time. Once edits land in `notes_cells`,
that file is stale. We need to regenerate the notes sheets at download
time from the canonical JSON.

**Failing tests (RED) — `tests/test_download_renders_from_notes_cells.py`:**
- `test_download_uses_notes_cells_html_when_present` — seed `notes_cells` with HTML different from the on-disk xlsx; downloaded file has the cell rendered from HTML.
- `test_download_falls_back_to_disk_workbook_when_no_cells` — empty `notes_cells` for the run → existing behaviour preserved.
- `test_download_flattens_table_html_to_pipe_form` — HTML table in DB → cell content is `"H1 | H2\nA | B"`.

**Implementation:**
- Extend `download_filled_endpoint` (server.py:2652): before streaming, if `notes_cells` has rows for this `run_id`, copy the on-disk xlsx to a temp file, overlay the rendered cells, stream the temp file.
- Overlay logic in `notes/persistence.py::overlay_notes_cells_into_workbook(xlsx_path: Path, run_id: int, conn) -> Path`.
- Use `notes/html_to_text.html_to_excel_text` for the HTML→string conversion.

**Verify:** all three tests green; manual download of a re-edited run opens cleanly in Excel.

**Dependencies:** Steps 1, 2, 6.

**Complexity:** M (need a temp-file dance to avoid mutating the canonical xlsx; openpyxl write touches need to mirror writer column rules per filing level).

---

### Phase 3: Editor UI

#### Step 8: API for listing + patching notes cells 🟩 Done
**Why eighth:** the editor needs `GET` and `PATCH` endpoints. This step
is pure backend so we can wire the UI in Step 9 against a contract
already proven by tests.

**Failing tests (RED) — `tests/test_server_notes_cells_api.py`:**
- `test_get_notes_cells_returns_grouped_by_sheet`.
- `test_get_notes_cells_returns_404_for_unknown_run`.
- `test_get_notes_cells_returns_empty_for_run_with_no_notes`.
- `test_patch_notes_cell_updates_html_and_updated_at`.
- `test_patch_notes_cell_sanitises_input_html`.
- `test_patch_notes_cell_404_for_unknown_cell`.
- `test_patch_notes_cell_413_when_rendered_text_over_30k`.
- `test_patch_notes_cell_does_not_touch_evidence`.

**Implementation:**
- `GET /api/runs/{run_id}/notes_cells` — returns `{ sheets: [{ sheet, label, rows: [{row, label, html, evidence, source_pages, updated_at}] }] }`.
- `PATCH /api/runs/{run_id}/notes_cells/{sheet}/{row}` body `{ html: string }` — sanitises (Step 5), enforces cap, upserts.
- Both go through the existing `_open_audit_conn` shim.

**Verify:** all eight tests green.

**Dependencies:** Steps 2, 5.

**Complexity:** S (two endpoints, well-bounded contracts).

---

#### Step 9: TipTap editor scaffold + read-only render in Run Detail 🟩 Done
**Why ninth:** smallest possible UI vertical slice that proves the
plumbing end-to-end before we add edit + copy.

**Failing tests (RED) — `web/src/__tests__/`:**
- `NotesReviewTab.test.tsx::renders_one_section_per_sheet`.
- `NotesReviewTab.test.tsx::renders_one_row_per_cell_with_label_on_left_html_on_right`.
- `NotesReviewTab.test.tsx::renders_html_as_rich_dom_not_escaped_text` — asserts `<strong>` survives as a `STRONG` element in the DOM.
- `NotesReviewTab.test.tsx::shows_empty_state_when_no_cells_for_run`.

**Implementation:**
- `npm i @tiptap/react @tiptap/starter-kit @tiptap/extension-table @tiptap/extension-table-row @tiptap/extension-table-cell @tiptap/extension-table-header` in `web/`.
- New `web/src/components/NotesReviewTab.tsx` — read-only `EditorContent` per cell with `editable={false}`, two-column grid (label | rich-rendered HTML).
- New `web/src/lib/notesCells.ts` — `fetchNotesCells(runId)` + types.
- Wire into `RunDetailView.tsx` as a new tab between "Validator" and existing tabs.
- All styles inline (gotcha #7).

**Verify:** vitest green; manual run in dev: open a run with notes, see the tab, see the formatted content.

**Dependencies:** Step 8.

**Complexity:** M (new tab integration into the existing run-detail layout; TipTap setup; type plumbing).

---

#### Step 10: Edit mode + PATCH save (debounced) 🟩 Done
**Why tenth:** turn the read-only viewer into an editor. Save on blur
or after a 1.5s debounce; show a small "Saved" indicator.

**Failing tests (RED):**
- `NotesReviewTab.test.tsx::edit_button_makes_editor_editable`.
- `NotesReviewTab.test.tsx::changing_html_calls_patch_after_debounce` — fake timers, mocked fetch.
- `NotesReviewTab.test.tsx::failed_patch_shows_error_and_keeps_dirty_state`.
- `NotesReviewTab.test.tsx::evidence_column_is_never_editable`.

**Implementation:**
- TipTap formatting toolbar above each editor (Bold, Italic, BulletList, OrderedList, Heading 3, Table). All inline styles.
- `useDebouncedSave(html, runId, sheet, row)` hook in `web/src/lib/notesCells.ts`.
- "Saved" / "Saving…" / "Save failed" status badge per cell.

**Verify:** vitest green; manual: edit a cell, watch the network tab show one PATCH after typing stops; refresh page and see the change persist.

**Dependencies:** Step 9.

**Complexity:** M (debounce + error handling + accessibility on the toolbar).

---

#### Step 11: Copy-as-rich-text button + clipboard contract 🟩 Done
**Why eleventh:** the entire feature exists for this button. Renders
the cell's HTML into a hidden DOM node, selects it, and writes both
`text/html` and `text/plain` to the clipboard via the modern
`ClipboardItem` API. Falls back to `document.execCommand('copy')` on
older browsers.

**Failing tests (RED):**
- `web/src/__tests__/clipboard.test.ts::copyHtmlAsRichText_writes_html_and_plain_to_clipboard` — mocked `navigator.clipboard.write`.
- `web/src/__tests__/clipboard.test.ts::copyHtmlAsRichText_falls_back_when_clipboard_api_unavailable`.
- `NotesReviewTab.test.tsx::copy_button_invokes_copy_helper_with_cell_html`.
- `NotesReviewTab.test.tsx::copy_button_shows_copied_confirmation_briefly`.

**Implementation:**
- New `web/src/lib/clipboard.ts::copyHtmlAsRichText(html: string): Promise<boolean>`.
- Per-cell `<button>` rendered top-right of the editor (small, unobtrusive — brainstorm decision).
- After success: green "Copied" text appears for 2s.

**Verify:** vitest green; manual: copy a cell, paste into M-Tool, verify formatting survives (the original problem statement).

**Dependencies:** Step 9.

**Complexity:** S (small helper + one button + a tiny state machine).

---

### Phase 4: Re-run Safety

#### Step 12: Re-run confirm dialog + clobber wiring 🟩 Done
**Why twelfth:** the brainstorm explicitly required a confirm before
agent re-runs destroy edits. Keep the wipe atomic with the new agent
output (both already covered by Step 6's clobber semantics; this step
is purely the UX guard).

**Failing tests (RED):**
- `tests/test_server_notes_api.py::test_rerun_endpoint_includes_warning_when_notes_cells_exist`.
- `web/src/__tests__/NotesReviewTab.test.tsx::regenerate_button_opens_confirm_dialog_when_edits_exist`.
- `web/src/__tests__/NotesReviewTab.test.tsx::regenerate_button_skips_dialog_when_no_edits`.
- `web/src/__tests__/NotesReviewTab.test.tsx::confirm_dialog_clobbers_via_rerun_endpoint`.

**Implementation:**
- New `GET /api/runs/{run_id}/notes_cells/edited_count` returning `{count: number}` (rows with `updated_at > <run.ended_at>`).
- "Regenerate notes" button in NotesReviewTab fetches the count first; if > 0, modal: "This will overwrite N edited cell(s). Continue?". Confirm → POST to existing rerun endpoint.
- Modal uses inline styles (gotcha #7).

**Verify:** all four tests green; manual: edit a cell, click regenerate, confirm dialog appears.

**Dependencies:** Steps 6, 8, 10.

**Complexity:** S (one new endpoint + small modal).

---

### Phase 5: Polish + Documentation

#### Step 13: NOTES-PIPELINE.md update + CLAUDE.md gotcha 🟩 Done
**Why last:** keep docs in lock-step with the new contract.

**Failing tests (RED):**
- `tests/test_docs_invariants.py::test_notes_pipeline_doc_mentions_html_contract` (grep-style assertion).
- `tests/test_docs_invariants.py::test_claude_md_has_notes_html_gotcha` (grep-style).

**Implementation:**
- Update `docs/NOTES-PIPELINE.md`: new section on the HTML contract, the `notes_cells` table, the editor, and the download regeneration.
- New `CLAUDE.md` gotcha #16: "Notes cells are HTML; Excel download is regenerated from `notes_cells` if present; agent re-run clobbers edits."

**Verify:** docs tests green; quick read-through.

**Dependencies:** all prior steps.

**Complexity:** S.

---

## Cross-cutting verification (after Step 12 lands)

- [ ] 🟥 Full e2e on FINCO MFRS Company filing: agents emit HTML, UI renders it, edit one cell, copy to M-Tool, paste survives, re-run shows the confirm dialog.
- [ ] 🟥 Full e2e on FINCO MPERS Group filing: same, with `[text block]` suffix labels still resolving.
- [ ] 🟥 Excel download for an edited run opens cleanly in Excel; tables show as pipe-flattened text.
- [ ] 🟥 `pytest tests/ -v` and `cd web && npx vitest run` both green.

## Rollback Plan

If something goes badly wrong:

- **Revert path is cheap because the schema bump is additive.** Roll
  back the code to a pre-v3 commit; v3 DBs simply carry an unused
  `notes_cells` table (no FK from anything that would care). Set
  `CURRENT_SCHEMA_VERSION = 2` if needed; `init_db` is forward-only so
  the version row will sit at 3 — harmless because nothing reads it.
- **Excel download fallback** in Step 7 already covers "no cells in DB"
  → identical to today's behaviour, so reverting the UI alone leaves
  the download path correct.
- **Agent prompts** can be reverted to the plaintext form independently
  of the schema; the writer's HTML sanitiser is a no-op on plain text
  (Step 5 wraps unwrapped strings in `<p>`).

## Resolved Decisions (locked from Q&A 2026-04-24)

- **Q1 — TipTap table styling:** ship a scoped CSS file
  `web/src/components/NotesReviewTab.css` with all selectors prefixed
  by `.notes-review-tab` (e.g. `.notes-review-tab table`,
  `.notes-review-tab th`, `.notes-review-tab td`). Imported only by
  `NotesReviewTab.tsx` so it cannot leak elsewhere. This is the
  robust path: TipTap's render-hook approach forces inline `style=`
  on every cell of every table on every keystroke, which is fragile
  and misses pseudo-classes (`:hover`, `:focus`). A single scoped
  stylesheet is the standard solution and respects gotcha #7 because
  it doesn't reintroduce a global utility framework like Tailwind.
- **Q2 — Sheet 12 fan-out persistence:** persist the **combined**
  post-`_combine_payloads` HTML in `notes_cells`. One row = one cell.
  Sub-agent fragments are already merged at write time; the editor
  should see the same content the workbook holds.
- **Q3 — UI placement:** Notes Review tab appears on the **History
  run-detail view only**. Live run page stays focused on the
  extraction stream.
- **Q4 — Drag-to-reorder:** out of scope. Rows are anchored to
  template concept IDs; reordering would break the XBRL filing.
- **Q5 — `colspan` / `rowspan` in the Excel flattener:** ignored.
  Each `<td>` becomes one pipe-separated cell regardless of the
  span attribute. Pipe-flattened text is informal anyway, and the
  rich version is always available in the editor.
