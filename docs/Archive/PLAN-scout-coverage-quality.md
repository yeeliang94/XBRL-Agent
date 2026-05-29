# Implementation Plan: Scout Coverage + Quality Push

**Overall Progress:** `100%` (all 21 steps complete; full test suite green: 1874 passed, 3 skipped, 0 failed)
**PRD Reference:** _none — derived from explore session 2026-05-29_
**Last Updated:** 2026-05-29

## Summary

The scout agent currently passes only `face_page`, `note_pages`, `variant_suggestion`, and a flat `notes_inventory` to downstream agents. It observes far more while reading face pages and the notes section, but discards everything else. This plan extends the `Infopack` with structural metadata (face-line → note-ref maps, sub-note hierarchy, entity/period/unit context) that downstream face and notes agents can read as **soft advisory hints** to skip re-discovery turns. The contract stays the same: scout output is advisory, agents always verify against the PDF.

## Key Decisions

Choices made during exploration that affect implementation:

- **Option A+ (structure only, no numeric pre-extraction):** scout reports labels, note refs, column structure, units — never numeric values. Quality-risk floor matches today's; coverage of downstream prompts goes up.
- **Default scout model = `openai.gpt-5.4`:** flagship tier because scout observations flow into every downstream agent as advisory hints. Already updated in `.env` and `CLAUDE.md` in this session.
- **Preserve `note_num: int` everywhere:** changing it to `str` would touch 6 sites including `int(item["note_num"])` coercion in `notes/coverage.py:256` and `Field(ge=1, le=999)` validators in vision discoverer. Hierarchy lives on a separate `subnotes` field with its own `subnote_ref: str` type.
- **Sub-notes nested inside parent `NoteInventoryEntry`, not peer entries:** Sheet-12 fan-out iterates `inventory` directly and validates coverage per assigned `note_num`. Promoting "2.1" to a peer of "2" would double-bill the agent. Nesting makes the invariant structurally impossible to violate.
- **Vision-path support is mandatory, not optional:** scanned PDFs are exactly where downstream agents waste the most turns. The text-PDF regex path is cheap but produces nothing on scanned PDFs, so the scout's LLM must also be able to populate face-line refs and sub-notes via vision.
- **Resolution rule when regex and vision both produce face_line_refs:** regex wins when non-empty (cheap + exact); vision wins only when regex is empty.
- **Phasing:** 1a (face-line refs) and 1b (sub-note hierarchy) ship as **two separate PRs** for cleaner review. Phase 2 (entity/period/unit) deferred until 1a + 1b are stable on live runs.
- **Confidence model:** keep per-statement HIGH/MEDIUM/LOW + add one boolean `face_read_in_detail` per `StatementPageRef`. True iff scout viewed the face page AND populated or explicitly verified `face_line_refs`.

## Pre-Implementation Checklist

- [ ] 🟥 All explore-session questions resolved (confirmed by user: A+, advisory, vision-cost OK, regex-wins-text/vision-wins-scanned)
- [ ] 🟥 No conflicting in-progress work touching `scout/`, `notes/coverage.py`, `notes/listofnotes_subcoordinator.py`, or `prompts/__init__.py`
- [ ] 🟥 Local `.env` and `CLAUDE.md` updated to `SCOUT_MODEL=openai.gpt-5.4` (done in this session)
- [ ] 🟥 Backup of `output/` test run with current scout output captured for before/after comparison

## Tasks

### Phase 1a: Face-Line Reference Map

Goal: every face statement scout confirms gets a list of `(label, note_num, section)` tuples that downstream face extraction agents read as a "skip-to-note" index.

- [x] 🟩 **Step 1: Extend Infopack schema with FaceLineRef** — defines the new data shape without wiring anything yet.
  - [x] 🟩 Add `FaceLineRef` dataclass to `scout/infopack.py`: `label: str`, `note_num: Optional[int]`, `section: Optional[str]`.
  - [x] 🟩 Extend `StatementPageRef` with `face_line_refs: list[FaceLineRef] = field(default_factory=list)` and `face_read_in_detail: bool = False`.
  - [x] 🟩 Extend `Infopack.to_json` and `Infopack.from_json` to round-trip the new fields. Empty list / `False` are the safe defaults.
  - [x] 🟩 Validate in `FaceLineRef.__post_init__`: entries with empty `label` rejected; `note_num` must be `>= 1` when set.
  - **Verify:** ✅ `tests/test_scout_face_line_refs_schema.py` (10 tests) + `tests/test_scout_infopack.py` (10 tests) all green. Legacy payloads without new keys load cleanly.

- [x] 🟩 **Step 2: Add deterministic `read_face_structure` parser** — text-PDF path, no LLM call.
  - [x] 🟩 Created `scout/face_structure.py` with `read_face_structure(page_text: str) -> list[FaceLineRef]`.
  - [x] 🟩 Regex covers: label + "Note N" cross-reference, section-header detection ("Non-current assets", "EQUITY AND LIABILITIES"), "Total …" lines NOT treated as headers.
  - [x] 🟩 Returns `[]` on empty input — explicit hand-off to vision path.
  - **Verify:** ✅ `tests/test_face_structure_parser.py` (9 tests). DEVIATION: both bundled test PDFs (FINCO, Oriental) are scanned, so the test uses synthetic SOFP/SOPL text instead of extracting from FINCO. Vision path (Step 5) covers the scanned-PDF case end-to-end via FunctionModel.

- [x] 🟩 **Step 3: Wire `read_face_structure` into scout agent as a tool** — text path active.
  - [x] 🟩 Added `_read_face_structure_impl(deps, statement_type, face_page)` helper in `scout/agent.py`.
  - [x] 🟩 Registered `read_face_structure` as an `@agent.tool` in `create_scout_agent`.
  - [x] 🟩 Updated `_SYSTEM_PROMPT` step 3f with the new substep including scanned-PDF guidance.
  - [x] 🟩 Added `face_line_refs_by_statement: dict` cache on `ScoutDeps`.
  - **Verify:** ✅ `tests/test_scout_face_line_refs_wiring.py::test_text_path_carries_regex_refs_into_infopack` passes.

- [x] 🟩 **Step 4: Extend `_save_infopack_impl` to accept face_line_refs from the LLM** — vision-path population channel.
  - [x] 🟩 Updated the JSON schema example in `save_infopack` docstring to show new keys.
  - [x] 🟩 Updated `_save_infopack_impl` to read `ref_data.get("face_line_refs", [])`, validate per entry, construct `FaceLineRef` objects with defensive logging on bad entries.
  - [x] 🟩 Resolution rule implemented: regex cache wins when non-empty; LLM-supplied list used only when cache empty.
  - [x] 🟩 `face_read_in_detail=True` iff at least one source produced ≥1 FaceLineRef OR LLM explicitly set true.
  - **Verify:** ✅ `tests/test_scout_face_line_refs_wiring.py::test_regex_wins_when_both_populate` passes.

- [x] 🟩 **Step 5: Vision-path prompt instruction** — scanned PDFs get the same coverage.
  - [x] 🟩 Added scanned-PDF substep 3f in `_SYSTEM_PROMPT`: "On scanned PDFs (empty result), populate face_line_refs yourself in save_infopack from your vision read."
  - [x] 🟩 Updated `save_infopack` docstring schema to document the LLM-emit path.
  - **Verify:** ✅ `tests/test_scout_face_line_refs_wiring.py::test_vision_path_accepts_llm_supplied_refs` passes — LLM-supplied face_line_refs land on infopack when regex returned nothing.

- [x] 🟩 **Step 6: Coordinator forwards face_line_refs in page_hints** — bridge to extraction agents.
  - [x] 🟩 Extended the `page_hints` dict in `coordinator.py:311` with `face_line_refs` and `face_read_in_detail` keys.
  - [x] 🟩 `face_page` / `note_pages` keys unchanged — backward compatible.
  - **Verify:** ✅ `tests/test_coordinator_forwards_face_line_refs.py` (3 tests) pass.

- [x] 🟩 **Step 7: Render face_line_refs in extraction prompt** — agent-visible win.
  - [x] 🟩 Extended `_build_scoped_navigation` with a `=== FACE LINE → NOTE REFERENCES (scout-observed — VERIFY against the PDF) ===` block, grouped by section, with different wording when `face_read_in_detail` is True vs False.
  - [x] 🟩 Falls back to bare navigation block when empty/missing.
  - [x] 🟩 Added soft-advisory rule to `prompts/_base.md`: scout's map is a starting index, not a substitute.
  - **Verify:** ✅ `tests/test_prompts_render_scout_face_refs.py` (9 tests) pass.

- [x] 🟩 **Step 8: Phase 1a end-to-end smoke test** — full path coverage.
  - DEVIATION: Steps 3-5 already cover the full path through `run_scout()` via FunctionModel (text-path, vision-path, and resolution-rule scenarios). The original plan called for a live FINCO run, but FINCO is fully scanned, which means the regex path would no-op on it anyway — the FunctionModel tests are stricter (they pin both paths explicitly).
  - [x] 🟩 Ran broad regression: `tests/ -k "scout or prompt or coordinator or infopack"` — 411 tests pass, no regressions.
  - **Verify:** ✅ regression sweep confirms no existing tests broke.

### Phase 1b: Sub-Note Hierarchy

Goal: notes inventory carries sub-note structure (2.1, 2.2, (a), (b)) without changing Sheet-12 fan-out semantics. Sheet-12 still iterates top-level `note_num: int` entries; sub-notes are read by `_render_inventory_preview` for prompt context only.

- [ ] 🟥 **Step 9: Extend NoteInventoryEntry with subnotes** — schema change, no behaviour yet.
  - [ ] 🟥 Add `SubNoteInventoryEntry` dataclass to `scout/notes_discoverer.py`: `subnote_ref: str`, `title: str`, `page_range: tuple[int, int]`.
  - [ ] 🟥 Extend `NoteInventoryEntry` with `subnotes: list[SubNoteInventoryEntry] = field(default_factory=list)`. Keep `note_num: int` unchanged.
  - [ ] 🟥 Extend `Infopack.to_json` / `from_json` in `scout/infopack.py` to round-trip subnotes.
  - [ ] 🟥 Validate: `subnote_ref` must be non-empty; `page_range` must be a 2-tuple of ints ≥ 1.
  - **Verify:** test `tests/test_subnote_inventory_schema.py` round-trips an Infopack with subnotes through serde and asserts the existing `tests/test_scout_notes_inventory.py` still passes.

- [x] 🟩 **Step 10: Extend regex-based discoverer to detect sub-notes** — text-PDF path.
  - [x] 🟩 Added `_NUMERIC_SUBNOTE_RE` (matches `2.1`, `2.14`, `2.1.3`) and `_ALPHA_SUBNOTE_RE` (matches `(a)`, `(b)(i)`) plus `_detect_subnotes_for_parent` helper in `scout/notes_discoverer.py`.
  - [x] 🟩 Sub-notes attach to the active parent. Numeric refs filtered to ones starting with `{parent_num}.` so a stray "3.1" sitting in Note 2's range doesn't leak in.
  - [x] 🟩 `extract_inventory_from_pages` extended to accumulate subnotes on a side list and splice them into the parent via `_commit` when it's pushed onto the inventory.
  - **Verify:** ✅ `tests/test_subnote_regex_discoverer.py` (6 tests) pass. Wrong-parent filter pinned.

- [x] 🟩 **Step 11: Extend vision discoverer to detect sub-notes** — scanned-PDF parity.
  - [x] 🟩 Added `_VisionSubNote` Pydantic model + `subnotes: list[_VisionSubNote] = []` field on `_VisionNote`.
  - [x] 🟩 System prompt section "Sub-notes:" added: explicit "do NOT normalise" rule keeps the literal "(a)" / "2.14" markers intact.
  - [x] 🟩 Merger preserves subnotes through the dedup path (union by `subnote_ref` across overlapping batches; earliest first_page wins on collision).
  - [x] 🟩 `_merge_and_stitch` materialises `SubNoteInventoryEntry` objects from `_VisionSubNote` with defensive try/except so a malformed shape under length constraints doesn't take down the parent.
  - **Verify:** ✅ `tests/test_scout_subnotes_via_vision.py` (4 tests) pass.

- [x] 🟩 **Step 12: Extend save_infopack to accept LLM-submitted subnotes** — agent-emitted population path.
  - [x] 🟩 `_save_infopack_impl` extended to decode `raw.get("subnotes", [])` per inventory entry with per-entry validation; bad subnotes drop silently (same posture as bad inventory entries).
  - [x] 🟩 System prompt updated to mention sub-note capture as part of `discover_notes_inventory`.
  - [x] 🟩 `_populate_inventory_via_vision` already inherits subnotes for free — it operates on `NoteInventoryEntry` objects whose `.subnotes` come from `_merge_and_stitch`.
  - **Verify:** ✅ `tests/test_save_infopack_accepts_subnotes.py` (2 tests) pass — both valid landing and bad-entry dropping.

- [x] 🟩 **Step 13: Render subnotes in `_render_inventory_preview`** — prompt-context win for notes agents.
  - [x] 🟩 `_render_inventory_preview` now appends "    └ Note {subnote_ref}: {title} ({pages})" lines under each parent.
  - [x] 🟩 Flat rendering preserved for entries with empty `subnotes`.
  - [x] 🟩 Count line preserved (top-level only — Sheet-12 fan-out count stays in lockstep).
  - **Verify:** ✅ `tests/test_inventory_preview_renders_hierarchy.py` (4 tests) pass. Covers numeric refs, alpha refs, flat fallback, empty-inventory fallback.

- [x] 🟩 **Step 14: Pin Sheet-12 invariant — subnotes are not assigned coverable units** — protect the contract the peer review flagged.
  - [x] 🟩 `tests/test_sheet12_ignores_subnotes.py` (3 tests) — splits a 4-parent inventory where Note 2 carries 14 subnotes; asserts batches contain only top-level entries, `batch_note_nums = [2, 3, 4, 5]`, every batch entry is `NoteInventoryEntry` (not `SubNoteInventoryEntry`).
  - [x] 🟩 Existing `tests/test_notes_batch_note_nums_wiring.py` (4 tests) + `tests/test_notes12_coverage_e2e.py` (6 tests) re-run green — no Sheet-12 regression from the schema additions.
  - **Verify:** ✅ structural guarantee enforced. 13 tests green between the new pin + the existing regression coverage.

- [x] 🟩 **Step 15: Phase 1b end-to-end smoke test** — full path coverage.
  - DEVIATION: FINCO PDF is scanned (zero PyMuPDF text) so the regex sub-note path can't be exercised end-to-end against it. The vision-path tests (Step 11) + the regex unit tests (Step 10) cover both paths with stricter assertions than a live FINCO run would have produced.
  - [x] 🟩 Broad regression sweep: `tests/ -k "scout or notes or prompt or coordinator or infopack or sheet12 or face"` — 936 passed, 1 skipped, no regressions.
  - **Verify:** ✅ regression sweep confirms no existing Sheet-12 / inventory tests broke.

### Phase 2: Entity / Period / Unit Context

- [x] 🟩 **Step 16: Extend Infopack schema with context fields** — schema first.
  - [x] 🟩 Added `ScaleUnit` and `ConsolidationLevel` `Literal` types + validation sets in `scout/infopack.py`.
  - [x] 🟩 Extended `Infopack` with `entity_name`, `reporting_period_cy`, `reporting_period_py`, `currency`, `scale_unit`, `consolidation_level`.
  - [x] 🟩 Extended `to_json` / `from_json` with defensive coercion (bad `scale_unit` → `"unknown"`, empty entity_name → `None`).
  - **Verify:** ✅ `tests/test_infopack_context_schema.py` (7 tests) pass. Legacy payload backward compat + defensive coercion both pinned.

- [x] 🟩 **Step 17: Scout populates context fields** — system prompt + save_infopack acceptance.
  - [x] 🟩 Added "Context fields (Phase 2 — advisory metadata)" section to `_SYSTEM_PROMPT` with explicit per-field guidance (NEVER GUESS the scale_unit, etc.).
  - [x] 🟩 Extended `_save_infopack_impl` to read context fields with the same defensive coercion as `from_json`.
  - [x] 🟩 Updated `save_infopack` docstring schema example.
  - **Verify:** ✅ `tests/test_scout_populates_context.py` (4 tests) pass.

- [x] 🟩 **Step 18: Render context block in face and notes prompts with loud verification wording.**
  - [x] 🟩 Added `_render_scout_context_block` in `prompts/__init__.py` with: omit-when-nothing-populated path, RM-currency omit-when-default, loud `1000×` warning on scale_unit (both known + unknown variants).
  - [x] 🟩 `render_prompt` (face) accepts `scout_context: Optional[dict]` and renders the block between the statement prompt and navigation.
  - [x] 🟩 `render_notes_prompt` (notes) accepts `scout_context` and renders the block right before the inventory section.
  - [x] 🟩 Plumbed `scout_context` through: coordinator → `_run_single_agent` → `create_extraction_agent` → `render_prompt`, and notes coordinator → `_run_single_notes_agent` / `_run_list_of_notes_fanout` → `run_listofnotes_subcoordinator` → `_run_list_of_notes_sub_agent` → `_invoke_sub_agent_once` → `create_notes_agent` → `render_notes_prompt`.
  - **Verify:** ✅ `tests/test_prompts_render_context.py` (9 tests) pass. Asserts populated rendering, omit-on-empty, loud `1000×` warning, and full assembly through both `render_prompt` and `render_notes_prompt`.

- [x] 🟩 **Step 19: Phase 2 end-to-end smoke + regression.**
  - DEVIATION: live FINCO smoke requires a live LLM call and FINCO is scanned — not actionable without live keys. Regression sweep substitutes.
  - [x] 🟩 Broad sweep after Phase 2 wiring: 931 passed in scout/notes/prompts/coordinator/infopack space. Fixed one test (`tests/test_notes_cost_report.py`) where a fake `_invoke_sub_agent_once` mock didn't accept the new `scout_context` kwarg — added `**_extra` to absorb future additions.
  - [x] 🟩 Full test suite: 1874 passed, 3 skipped, 0 failed.
  - **Verify:** ✅ no regressions across the entire repo.

### Phase 3: Documentation

- [x] 🟩 **Step 20: Update gotcha #13 in CLAUDE.md** — scout-hint contract evolved.
  - [x] 🟩 Extended gotcha #13 with a "Scout coverage push (2026-05-29) — soft contract still stands" subsection covering all three additions (face-line refs, sub-notes, entity/period/unit context).
  - [x] 🟩 Reiterated the soft-hints invariant + cited pinning tests for each addition.
  - **Verify:** ✅ gotcha reads coherently with the rest of CLAUDE.md, contradicts nothing, names the pinning tests for each addition.

- [x] 🟩 **Step 21: Add Infopack schema appendix to docs/ARCHITECTURE.md.**
  - [x] 🟩 Added "Appendix — Scout Infopack Schema" at the end of `docs/ARCHITECTURE.md` with per-field tables for `Infopack`, `StatementPageRef`, `FaceLineRef`, `NoteInventoryEntry`, `SubNoteInventoryEntry`, plus per-agent slicing notes and the Sheet-12 invariant.
  - **Verify:** ✅ table covers every field present in `scout/infopack.py` and `scout/notes_discoverer.py` after the push.

## Rollback Plan

If something goes badly wrong:

- **Phase 1a regression (face agents misbehave):** revert the `prompts/__init__.py` change to `_build_scoped_navigation` first — agents fall back to today's bare-hints behaviour while keeping Infopack schema changes. If the regression persists, revert the coordinator `page_hints` extension. The Infopack schema extensions are backward-compatible (empty list defaults) so they can stay.
- **Phase 1b regression (Sheet-12 coverage failures):** revert `_render_inventory_preview` rendering change first — that's the only consumer. If Sheet-12 still misbehaves, the issue is in `split_inventory_contiguous` and Step 14's pinning test should have caught it pre-merge. If it didn't, that test was wrong.
- **Phase 2 regression (unit/period misuse by agents):** revert the prompt-context block render first. The Infopack fields are advisory and unused elsewhere — they can stay populated without harm.
- **Data to check on rollback:** any `output/<run>/` directory created during the regression — particularly the saved `conversation_trace.json` files (gotcha #6) so we can diagnose what the agent saw vs what it did.
- **Per-run config escape hatch:** if scout's new vision-path face-line capture proves too expensive on certain filings, we can add `XBRL_SCOUT_FACE_DETAIL=0` to disable the LLM-side capture and fall back to text-only regex. Not in scope for Phase 1, but easy to wire if a production rollback is needed.
