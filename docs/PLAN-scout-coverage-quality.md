# Implementation Plan: Scout Coverage + Quality Push

**Overall Progress:** `0%`
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

- [ ] 🟥 **Step 1: Extend Infopack schema with FaceLineRef** — defines the new data shape without wiring anything yet.
  - [ ] 🟥 Add `FaceLineRef` dataclass to `scout/infopack.py`: `label: str`, `note_num: Optional[int]`, `section: Optional[str]`.
  - [ ] 🟥 Extend `StatementPageRef` with `face_line_refs: list[FaceLineRef] = field(default_factory=list)` and `face_read_in_detail: bool = False`.
  - [ ] 🟥 Extend `Infopack.to_json` and `Infopack.from_json` to round-trip the new fields. Empty list / `False` are the safe defaults.
  - [ ] 🟥 Validate in `StatementPageRef.__post_init__`: `face_line_refs` entries with a `note_num` must have `note_num >= 1`; entries with empty `label` are rejected.
  - **Verify:** new test `tests/test_scout_face_line_refs_schema.py` round-trips an Infopack with populated `face_line_refs` through `to_json` / `from_json` and asserts existing tests `tests/test_scout_end_to_end.py` still pass.

- [ ] 🟥 **Step 2: Add deterministic `read_face_structure` parser** — text-PDF path, no LLM call.
  - [ ] 🟥 Create `scout/face_structure.py` with `read_face_structure(page_text: str) -> list[FaceLineRef]`.
  - [ ] 🟥 Regex covers: leading label, optional "Note N" reference, section-header detection (e.g. "ASSETS", "Non-current assets", "EQUITY AND LIABILITIES" header lines that classify subsequent lines until the next header).
  - [ ] 🟥 Returns `[]` on empty input — this is the explicit hand-off to the vision path.
  - **Verify:** unit test `tests/test_face_structure_parser.py` covers the FINCO SOFP face page from `data/FINCO-Audited-Financial-Statement-2021.pdf` (extract its text once into a fixture) and asserts at least `Property, plant and equipment → Note 4`, `Trade receivables → Note 7`, plus correct section classification.

- [ ] 🟥 **Step 3: Wire `read_face_structure` into scout agent as a tool** — text path active.
  - [ ] 🟥 Add `_read_face_structure_impl(deps, statement_type, face_page) -> list[dict]` helper in `scout/agent.py` that pulls page text from PyMuPDF and runs `read_face_structure`.
  - [ ] 🟥 Register `read_face_structure` as an `@agent.tool` in `create_scout_agent`.
  - [ ] 🟥 Update `_SYSTEM_PROMPT` step 3 to add substep: "After confirming face page, call `read_face_structure(statement_type, face_page)` to capture the label→note map."
  - **Verify:** test `tests/test_scout_face_structure_tool.py` runs the scout agent end-to-end with a `TestModel` scripted to call the new tool, asserts the resulting Infopack carries non-empty `face_line_refs` on a text PDF.

- [ ] 🟥 **Step 4: Extend `_save_infopack_impl` to accept face_line_refs from the LLM** — vision-path population channel.
  - [ ] 🟥 Update the JSON schema in the system prompt example to show `face_line_refs` and `face_read_in_detail` keys.
  - [ ] 🟥 Update `_save_infopack_impl` (`scout/agent.py:489`) to read `ref_data.get("face_line_refs", [])`, validate each entry (label non-empty, note_num int or None, section str or None), and construct `FaceLineRef` objects.
  - [ ] 🟥 Resolution rule: if `deps` carries a cached non-empty list from `read_face_structure` for that statement, prefer it; else use what the LLM submitted; else empty.
  - [ ] 🟥 Set `face_read_in_detail=True` iff at least one source produced ≥1 `FaceLineRef`.
  - [ ] 🟥 Cache `face_line_refs` on `ScoutDeps` per statement so the resolution rule can compare.
  - **Verify:** test `tests/test_save_infopack_accepts_face_line_refs.py` covers three cases: text-only population (regex), vision-only population (LLM-submitted JSON, regex empty), both populated (regex wins). All round-trip through serde.

- [ ] 🟥 **Step 5: Vision-path prompt instruction** — scanned PDFs get the same coverage.
  - [ ] 🟥 Add to `_SYSTEM_PROMPT` after the variant rules: "For each face statement, capture every line-item label visible on the face page, the note number cited next to it (or null), and the section header it sits under. Include this in `save_infopack` under `statements[<STMT>].face_line_refs`. On scanned PDFs where `read_face_structure` returns empty, this LLM-emitted list is the only source."
  - **Verify:** integration test `tests/test_scout_face_line_refs_via_vision.py` runs scout with a scripted vision-model `TestModel` that returns empty regex results but a populated LLM JSON for `face_line_refs` — asserts the final Infopack carries the LLM list with `face_read_in_detail=True`.

- [ ] 🟥 **Step 6: Coordinator forwards face_line_refs in page_hints** — bridge to extraction agents.
  - [ ] 🟥 In `coordinator.py:311`, extend the `page_hints` dict built per statement: add `face_line_refs: list[dict]` and `face_read_in_detail: bool` keys when scout populated them.
  - [ ] 🟥 Keep `face_page` / `note_pages` keys unchanged — backward compatible with anything that already reads them.
  - **Verify:** test `tests/test_coordinator_forwards_face_line_refs.py` runs `run_extraction` with a mock infopack carrying face_line_refs, intercepts the `_run_single_agent` call, asserts the `page_hints` dict contains the new keys.

- [ ] 🟥 **Step 7: Render face_line_refs in extraction prompt** — agent-visible win.
  - [ ] 🟥 Extend `_build_scoped_navigation` in `prompts/__init__.py:122` to render a `face line items with note references (scout-observed — VERIFY against the PDF)` block when `face_line_refs` is non-empty.
  - [ ] 🟥 Fall back to today's bare navigation block when empty/missing — no regression on text-PDF runs scout couldn't enrich.
  - [ ] 🟥 Add a one-line "soft advisory" rule to `prompts/_base.md`: "Scout's face-line map is a starting index, not a substitute for reading the linked note pages."
  - **Verify:** test `tests/test_prompts_render_scout_face_refs.py` calls `render_prompt` with and without `face_line_refs`, asserts the rendered system prompt contains/omits the new block, asserts the soft-advisory rule is present in both cases.

- [ ] 🟥 **Step 8: Phase 1a end-to-end smoke test** — full path on real fixture.
  - [ ] 🟥 Run `python3 run.py` against the FINCO test PDF with scout enabled (Web UI path via test harness).
  - [ ] 🟥 Inspect saved Infopack JSON: `face_line_refs` populated for every face statement, `face_read_in_detail=True`.
  - [ ] 🟥 Inspect any one face agent's saved trace at `output/<run>/SOFP_conversation_trace.json`: system prompt contains the new block; agent's first `view_pdf_pages` call targets the right note page (not a sweep).
  - **Verify:** before/after comparison: SOFP agent's iteration count from a Phase 1a run vs an equivalent run before this change. Target: same or fewer iterations to first `verify_totals` call.

### Phase 1b: Sub-Note Hierarchy

Goal: notes inventory carries sub-note structure (2.1, 2.2, (a), (b)) without changing Sheet-12 fan-out semantics. Sheet-12 still iterates top-level `note_num: int` entries; sub-notes are read by `_render_inventory_preview` for prompt context only.

- [ ] 🟥 **Step 9: Extend NoteInventoryEntry with subnotes** — schema change, no behaviour yet.
  - [ ] 🟥 Add `SubNoteInventoryEntry` dataclass to `scout/notes_discoverer.py`: `subnote_ref: str`, `title: str`, `page_range: tuple[int, int]`.
  - [ ] 🟥 Extend `NoteInventoryEntry` with `subnotes: list[SubNoteInventoryEntry] = field(default_factory=list)`. Keep `note_num: int` unchanged.
  - [ ] 🟥 Extend `Infopack.to_json` / `from_json` in `scout/infopack.py` to round-trip subnotes.
  - [ ] 🟥 Validate: `subnote_ref` must be non-empty; `page_range` must be a 2-tuple of ints ≥ 1.
  - **Verify:** test `tests/test_subnote_inventory_schema.py` round-trips an Infopack with subnotes through serde and asserts the existing `tests/test_scout_notes_inventory.py` still passes.

- [ ] 🟥 **Step 10: Extend regex-based discoverer to detect sub-notes** — text-PDF path.
  - [ ] 🟥 In `scout/notes_discoverer.py`, after the top-level header detection that builds `_TOP_HEADER_RE` matches, scan the lines between consecutive top-level headers for sub-note headers (`\d+\.\d+`, `\d+\.\d+\.\d+`, `\([a-z]\)` patterns).
  - [ ] 🟥 Sub-notes are attached to the preceding parent's `subnotes` list with their own page (first detected page).
  - [ ] 🟥 No change to existing top-level `NoteInventoryEntry` construction.
  - **Verify:** unit test `tests/test_subnote_regex_discoverer.py` covers the FINCO accounting-policies pages (Note 2.x cascades): asserts Note 2 carries subnotes `["2.1", "2.2", ..., "2.14"]` with reasonable page ranges; asserts a numbered note with no sub-numbering carries `subnotes=[]`.

- [ ] 🟥 **Step 11: Extend vision discoverer to detect sub-notes** — scanned-PDF parity.
  - [ ] 🟥 In `scout/notes_discoverer_vision.py`, extend `_VisionNote` schema (`scout/notes_discoverer_vision.py:55`) with `subnotes: list[_VisionSubNote] = []` where `_VisionSubNote` carries `subnote_ref: str`, `title: str`, `first_page: int`.
  - [ ] 🟥 Update the vision prompt to explicitly request sub-numbered headings: "For each top-level note, also identify any sub-numbered headings (e.g. 2.1 Basis of preparation, (a) Short term benefits) and list them under `subnotes`."
  - [ ] 🟥 Update the merger that builds `NoteInventoryEntry` from `_VisionNote` results to carry subnotes through.
  - **Verify:** test `tests/test_scout_subnotes_via_vision.py` runs the vision discoverer with a scripted model that returns `_VisionNote` JSON containing subnotes, asserts the resulting `NoteInventoryEntry` list carries them.

- [ ] 🟥 **Step 12: Extend save_infopack to accept LLM-submitted subnotes** — agent-emitted population path.
  - [ ] 🟥 Update `_save_infopack_impl` in `scout/agent.py:525` to read `raw.get("subnotes", [])` per inventory entry, validate each (`subnote_ref` non-empty string, `title` string, `page_range` 2-tuple), construct `SubNoteInventoryEntry` list.
  - [ ] 🟥 Update the JSON schema example in the system prompt to show the `subnotes` key.
  - [ ] 🟥 Update the `_populate_inventory_via_vision` post-scout safety net (`scout/agent.py:307`) to forward subnotes when it runs.
  - **Verify:** test `tests/test_save_infopack_accepts_subnotes.py` covers LLM-submitted subnotes round-tripping through `_save_infopack_impl`. Malformed entries are skipped silently (existing behaviour for inventory entries with bad page_range).

- [ ] 🟥 **Step 13: Render subnotes in `_render_inventory_preview`** — prompt-context win for notes agents.
  - [ ] 🟥 Update `_render_inventory_preview` in `notes/agent.py:374` to render parent + child tree:
    ```
    Note 2: Significant accounting policies (pp.15-22)
      └ Note 2.1: Basis of preparation (p.15)
      └ Note 2.14: Employee benefits (p.20)
    ```
  - [ ] 🟥 Preserve flat rendering for entries with empty `subnotes`.
  - [ ] 🟥 Preserve every existing format invariant (lead line, count, paging shorthand `p.X` vs `pp.X-Y`).
  - **Verify:** test `tests/test_inventory_preview_renders_hierarchy.py` calls `_render_inventory_preview` with mixed subnoted/non-subnoted entries, asserts the tree format renders correctly, asserts the count line stays accurate.

- [ ] 🟥 **Step 14: Pin Sheet-12 invariant — subnotes are not assigned coverable units** — protect the contract the review flagged.
  - [ ] 🟥 New test `tests/test_sheet12_ignores_subnotes.py` constructs an inventory with `note_num=2` carrying `subnotes=[2.1, 2.2]`, calls `split_inventory_contiguous` from `notes/listofnotes_subcoordinator.py:165`, asserts the resulting batches carry exactly one `NoteInventoryEntry` for note 2 (subnotes not promoted to peer entries) and `batch_note_nums = [2]` (not `[2, 2.1, 2.2]`).
  - [ ] 🟥 Run existing `tests/test_notes_batch_note_nums_wiring.py` and `tests/test_notes12_coverage_e2e.py` to confirm no regression.
  - **Verify:** all three tests pass. This is the structural guarantee Phase 1b's design rests on.

- [ ] 🟥 **Step 15: Phase 1b end-to-end smoke test** — full path on real fixture.
  - [ ] 🟥 Run scout on FINCO test PDF.
  - [ ] 🟥 Inspect saved Infopack JSON: at least one `notes_inventory` entry carries non-empty `subnotes` (Note 2 expected to have subnotes 2.1–2.14).
  - [ ] 🟥 Inspect any notes-12 agent's saved trace: system prompt's inventory preview renders the tree.
  - [ ] 🟥 Sheet-12 batch coverage receipts still validate per top-level `note_num` only — no spurious "subnote 2.1 not covered" errors.
  - **Verify:** notes-12 fan-out completes without coverage-validation failures introduced by the new structure.

### Phase 2: Entity / Period / Unit Context (deferred)

Goal: top-level Infopack fields for entity name, reporting period dates, currency, scale unit. Surfaced to face + notes prompts as **strictly advisory** with loud "verify" wording — gotcha #17's residual-plug failure mode has a sibling here (silent 1000× errors from a wrong unit).

- [ ] 🟥 **Step 16: Extend Infopack schema with context fields** — schema first, no wiring.
  - [ ] 🟥 Add to `Infopack`: `entity_name: Optional[str]`, `reporting_period_cy: Optional[str]`, `reporting_period_py: Optional[str]`, `currency: str = "RM"`, `scale_unit: Literal["units", "thousands", "millions", "unknown"] = "unknown"`, `consolidation_level: Literal["company", "group", "both", "unknown"] = "unknown"`.
  - [ ] 🟥 Extend `to_json` / `from_json`.
  - **Verify:** test `tests/test_infopack_context_schema.py` round-trips a populated context Infopack through serde, asserts defaults are safe (no field defaults to a number-valued unit guess).

- [ ] 🟥 **Step 17: Scout populates context fields from face-page vision** — already viewed pages.
  - [ ] 🟥 Update `_SYSTEM_PROMPT` to add a context-capture step after face confirmation: "Capture the entity name, reporting period dates (CY and PY), currency, and scale unit (RM '000? RM millions?) from the page headers you've already viewed."
  - [ ] 🟥 Extend `_save_infopack_impl` to accept and validate the new top-level fields.
  - **Verify:** test `tests/test_scout_populates_context.py` runs scout with a `TestModel` that returns realistic JSON, asserts context fields land on the Infopack.

- [ ] 🟥 **Step 18: Render context block in face and notes prompts with loud verification wording** — the dangerous step.
  - [ ] 🟥 In `prompts/__init__.py` `render_prompt`, prepend a context block when fields are populated:
    ```
    === SCOUT-OBSERVED CONTEXT (VERIFY EACH BEFORE USING) ===
    Entity (scout claim — verify against PDF cover/header): FINCO Berhad
    Reporting period CY (scout claim — verify against page 1 header): 01/01/2022 - 31/12/2022
    Reporting period PY: 01/01/2021 - 31/12/2021
    Currency: RM
    Scale: thousands (RM '000) — VERIFY against statement header before writing any number. A wrong unit produces a 1000× error.
    ```
  - [ ] 🟥 In `notes/agent.py`, prepend the same block before the inventory.
  - [ ] 🟥 Block is omitted entirely when all fields are `None`/`"unknown"` — no clutter on degraded runs.
  - **Verify:** test `tests/test_prompts_render_context.py` covers populated and empty cases. Asserts the literal phrase "VERIFY" appears at least twice (once per loud field) and "1000× error" appears in the scale block.

- [ ] 🟥 **Step 19: Phase 2 end-to-end smoke + manual review** — last sanity check before merge.
  - [ ] 🟥 Run scout + face extraction on FINCO test PDF.
  - [ ] 🟥 Inspect any face agent's trace: does the agent verify the unit visually before writing? Does it cite the unit in evidence?
  - [ ] 🟥 Look for any case where the agent silently trusted scout's unit claim without checking — if observed, escalate prompt wording.
  - **Verify:** face extraction on a known-good filing still produces a balanced SOFP (`verify_totals` passes). No unit-mismatch errors in the workbook.

### Phase 3: Documentation & CLAUDE.md updates

- [ ] 🟥 **Step 20: Update gotcha #13 in CLAUDE.md** — scout-hint contract evolved.
  - [ ] 🟥 Note that scout now passes structural metadata (face_line_refs, subnotes) in addition to page hints.
  - [ ] 🟥 Reiterate: still soft hints; no `allowed_pages` enforcement; agents still verify.
  - **Verify:** read the updated gotcha — it's accurate to the merged code and doesn't contradict the existing "soft hints only" rule.

- [ ] 🟥 **Step 21: Add the Infopack schema appendix to docs/ARCHITECTURE.md** — single source of truth for downstream consumers.
  - [ ] 🟥 Document each Infopack field, who populates it, who consumes it, what the empty-state contract is.
  - **Verify:** doc lists all fields present in `scout/infopack.py` after Phase 2 — cross-reference by hand.

## Rollback Plan

If something goes badly wrong:

- **Phase 1a regression (face agents misbehave):** revert the `prompts/__init__.py` change to `_build_scoped_navigation` first — agents fall back to today's bare-hints behaviour while keeping Infopack schema changes. If the regression persists, revert the coordinator `page_hints` extension. The Infopack schema extensions are backward-compatible (empty list defaults) so they can stay.
- **Phase 1b regression (Sheet-12 coverage failures):** revert `_render_inventory_preview` rendering change first — that's the only consumer. If Sheet-12 still misbehaves, the issue is in `split_inventory_contiguous` and Step 14's pinning test should have caught it pre-merge. If it didn't, that test was wrong.
- **Phase 2 regression (unit/period misuse by agents):** revert the prompt-context block render first. The Infopack fields are advisory and unused elsewhere — they can stay populated without harm.
- **Data to check on rollback:** any `output/<run>/` directory created during the regression — particularly the saved `conversation_trace.json` files (gotcha #6) so we can diagnose what the agent saw vs what it did.
- **Per-run config escape hatch:** if scout's new vision-path face-line capture proves too expensive on certain filings, we can add `XBRL_SCOUT_FACE_DETAIL=0` to disable the LLM-side capture and fall back to text-only regex. Not in scope for Phase 1, but easy to wire if a production rollback is needed.
