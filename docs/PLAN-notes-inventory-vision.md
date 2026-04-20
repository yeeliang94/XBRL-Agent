# Implementation Plan: Vision fallback for `build_notes_inventory`

**Overall Progress:** `99%` — Phases 1-7 shipped (initial feature + two rounds of peer-review fixes). All 609 backend tests green; live FINCO run 15/15 notes in ~25 s. Only 5.4 (commit + PR) is pending explicit user approval.
**Context doc:** this conversation (2026-04-19 investigation of FINCO run `bdcc769d…`).
**Last Updated:** 2026-04-19

## Summary

`build_notes_inventory` in `scout/notes_discoverer.py` is purely deterministic — it runs a regex over PyMuPDF-extracted text. On image-only scanned PDFs (FINCO is one) PyMuPDF returns empty strings, the inventory is empty, and `notes/coordinator.py:644-668` correctly fails Sheet 12 with "no inventory to fan out". Fix: add a vision-based fallback that fires only when the PyMuPDF pass returns `[]` *and* a model is supplied. The vision path renders notes pages to PNG, batches them (8 pages / 1-page overlap), calls a dedicated PydanticAI `Agent` with a structured Pydantic output schema in parallel (semaphore cap 5), then merges + stitches the results to produce the same `list[NoteInventoryEntry]` downstream callers already expect.

## Key Decisions

- **Keep PyMuPDF pass first, unconditionally.** Text-based PDFs pay zero LLM cost and keep their current behaviour. Vision is the fallback, not the default.
- **Vision fallback is opt-in via the `vision_model` kwarg.** Callers that can't supply a model (tests, CLI paths without scout) get the same `[]` they get today. `notes/coordinator.py` still fails loudly on `[]`, unchanged.
- **Single scout LLM, reused.** Pass the scout's existing PydanticAI `Model` through `ScoutDeps` rather than spin up a second model. No new env vars, no new provider wiring.
- **Batch = 8 pages, overlap = 1 page, concurrency = 5.** Balances token cost vs. cross-batch boundary detection. Validated against the 50-page scenario in the design review (8 batches, ~3-5¢, ~3 s).
- **Stitcher fixes trailing-edge `last_page`.** LLMs are bad at "where does this note end?" so we derive `last_page(N) = first_page(N+1) - 1` (or `notes_end_page` for the terminal note), ignoring what the LLM said.
- **Reuse `tools/pdf_viewer.render_pages_to_png_bytes`** for rendering. No new renderer, no disk I/O.
- **New helper file `scout/notes_discoverer_vision.py`** so that `notes_discoverer.py` stays deterministic and lint-clean. `build_notes_inventory` (public API, unchanged signature except for an added kw-only `vision_model=None`) orchestrates.
- **Hallucination guards:** Pydantic schema validation (`note_num >= 1`, `first_page >= 1`, `last_page >= 1`); stitcher-level drop of entries with `first_page > notes_end`; terminal-note `last_page` clamped to `min(LLM-reported, notes_end)`. Monotonicity is automatic (the stitcher sorts ascending). Gap-size / duplicate-number guards are **deferred** — the live FINCO run produced a perfectly contiguous 1→15 inventory, so there's no evidence they are needed yet; add them only if a real filing exhibits the signal. On violation log a warning and drop the offending entry — never abort the whole inventory.
- **Out-of-bounds input:** if `notes_start_page > effective_end` (scout mis-offset), `build_notes_inventory` logs a warning and returns `[]` instead of raising from `_chunk`. A bad hint must never be fatal.
- **Notes end page (optional):** `build_notes_inventory(..., notes_end_page=N)` caps both the vision scan range AND the stitcher's terminal-note clamp. Leaving it unset preserves today's `pdf_length` default but the terminal note now honours the LLM's own `last_page` rather than silently stretching to `pdf_length` — trailing Directors' Statement / auditor's report pages are no longer absorbed.
- **Failure policy:** if a single batch fails (API timeout / malformed output after one retry), emit partial inventory and log a gap. If every batch fails, return `[]` — same outcome as today's empty inventory, so `notes/coordinator.py` handles it.

## Pre-Implementation Checklist
- [ ] 🟥 Confirm FINCO PDF (`output/bdcc769d-1dc8-456e-a5a6-61f4c569cb5d/uploaded.pdf`) is stored for integration fixture (or copy the first ~35 pages into `tests/fixtures/`).
- [ ] 🟥 Confirm the scout's `Model` instance is reachable at tool time (via `ctx.deps` or closure capture inside `build_scout_agent`).
- [ ] 🟥 Re-run existing test suite on `main` so regressions are attributable to this change (`pytest tests/ -v`).

## Tasks

### Phase 1 — Foundation (no behavioural change) — 🟩 Done

- [x] 🟩 **Step 1.1: Thread the scout model onto `ScoutDeps`**
  - [ ] 🟥 Add `vision_model: Optional[Model] = None` field on `ScoutDeps` (`scout/agent.py:61-74`).
  - [ ] 🟥 Populate it in `build_scout_agent` / `run_scout` (`scout/agent.py:395-414`) — assign the same `model` the PydanticAI `Agent` is constructed from.
  - **Verify:** `python -c "from scout.agent import ScoutDeps; s = ScoutDeps.__dataclass_fields__; assert 'vision_model' in s; print('ok')"` prints `ok`. Existing test suite still green.

- [x] 🟩 **Step 1.2: Define Pydantic schemas for the vision call**
  - [ ] 🟥 Create `scout/notes_discoverer_vision.py` with `_VisionNote` and `_VisionBatch` models (note_num, title, first_page, last_page — all validated).
  - [ ] 🟥 Add module docstring describing the vision fallback's contract.
  - **Verify:** `python -c "from scout.notes_discoverer_vision import _VisionNote; _VisionNote(note_num=4, title='PPE', first_page=10, last_page=12); print('ok')"` prints `ok`; construction with `note_num=0` raises `ValidationError`.

- [x] 🟩 **Step 1.3: Batcher pure function**
  - [ ] 🟥 Implement `_chunk(start, end, size, overlap) -> list[list[int]]`, pure Python, no side effects.
  - **Verify:** New `tests/test_notes_discoverer_vision.py` with 3 cases:
    1. `_chunk(10, 20, 8, 1) == [[10,11,12,13,14,15,16,17],[17,18,19,20]]`
    2. `_chunk(10, 10, 8, 1) == [[10]]` (single page)
    3. `_chunk(10, 60, 8, 1)` — verify 8 batches, first page of each ≤ last page of previous, last batch ends at 60.
  - `pytest tests/test_notes_discoverer_vision.py::test_chunk -v` all pass.

### Phase 2 — Core vision helper — 🟩 Done

- [x] 🟩 **Step 2.1: Stitcher / merger pure function**
  - [ ] 🟥 Implement `_merge_and_stitch(batches: list[_VisionBatch], notes_end: int) -> list[NoteInventoryEntry]`:
    - dedup by `note_num` (take widest range across overlapping batches)
    - sort by `note_num`
    - overwrite `last_page(N) = first_page(N+1) - 1` for all but the terminal note
    - terminal note's `last_page = notes_end`
    - drop entries that violate `first_page <= last_page` or `first_page > notes_end` (log warning)
  - **Verify:** Unit tests in the same test module:
    1. Two batches with overlapping note 4 → dedup to one entry with widest range.
    2. Three notes (4, 5, 7) → note 4's last_page = note 5's first_page - 1; note 5's last_page = note 7's first_page - 1; note 7's last_page = `notes_end`.
    3. Out-of-order batch input (note 7 before note 4) → output is sorted ascending.
    4. Malformed entry (`first_page=20, last_page=10`) → dropped, other entries still returned.
  - `pytest tests/test_notes_discoverer_vision.py::test_merge_and_stitch -v` all pass.

- [x] 🟩 **Step 2.2: Vision prompt + agent factory**
  - [ ] 🟥 Define `_VISION_SYSTEM_PROMPT` string in `scout/notes_discoverer_vision.py` (the one from the design review — "emit one entry per numbered note whose HEADER appears on these pages; do not speculate").
  - [ ] 🟥 Implement `_build_vision_agent(model) -> Agent[None, _VisionBatch]` — PydanticAI `Agent` with `output_type=_VisionBatch`, `model_settings=ModelSettings(temperature=1.0)` (CLAUDE.md gotcha #5).
  - **Verify:** `python -c "from pydantic_ai.models.test import TestModel; from scout.notes_discoverer_vision import _build_vision_agent; a = _build_vision_agent(TestModel()); print(type(a).__name__)"` prints `Agent`. Agent's output schema validates.

- [x] 🟩 **Step 2.3: `_scan_batch` async scan-one-batch function**
  - [ ] 🟥 Implement `async def _scan_batch(doc: fitz.Document, agent, pages: list[int], dpi: int = 150) -> _VisionBatch`:
    - render pages via `tools/pdf_viewer.render_pages_to_png_bytes` (reuse, don't re-implement)
    - build `[f"Pages {pages[0]}-{pages[-1]}:", BinaryContent(...), BinaryContent(...), …]` user prompt
    - call `await agent.run(prompt)` and return `.output`
    - on `ValidationError` or transport error: one retry with a reminder appended ("your previous response was invalid — emit only `_VisionBatch`"), then raise `VisionBatchError`
  - **Verify:** Unit test with `TestModel` that returns a canned `_VisionBatch`:
    1. `_scan_batch` returns that batch without raising.
    2. With a `FunctionModel` that returns malformed JSON on first call, valid on second — `_scan_batch` still returns the valid batch (retry worked).
    3. With a `FunctionModel` that returns malformed JSON twice — `_scan_batch` raises `VisionBatchError`.

- [x] 🟩 **Step 2.4: `_vision_inventory` orchestrator**
  - [ ] 🟥 Implement `async def _vision_inventory(pdf_path, start, end, model, *, max_parallel=5) -> list[NoteInventoryEntry]`:
    - open PDF via `fitz`
    - build batches via `_chunk`
    - build agent via `_build_vision_agent`
    - `asyncio.gather` all batches through an `asyncio.Semaphore(max_parallel)`
    - on per-batch failure: log, skip that batch, continue — do not fail the whole call
    - `return _merge_and_stitch(successful_batches, notes_end=end)`
  - **Verify:** Unit test with `FunctionModel` that returns different batches per call, confirms:
    1. Two-page PDF fixture → returns correct inventory.
    2. One batch raises mid-run → other batches' entries survive.
    3. All batches raise → returns `[]` (no crash).
  - `pytest tests/test_notes_discoverer_vision.py -v` fully green.

### Phase 3 — Wire into public API — 🟩 Done

- [x] 🟩 **Step 3.1: Extend `build_notes_inventory` with `vision_model` kwarg**
  - [ ] 🟥 Add kw-only `vision_model: Optional[Model] = None` parameter to `build_notes_inventory` in `scout/notes_discoverer.py`.
  - [ ] 🟥 After the PyMuPDF pass, if `inventory` is `[]` **and** `vision_model is not None`, call `asyncio.run(_vision_inventory(...))` and return that result.
  - [ ] 🟥 If `asyncio` is already running (pytest-asyncio, etc.), detect and use `asyncio.get_running_loop().run_until_complete` *only when no loop is running*; otherwise expose an `async` sibling `build_notes_inventory_async`.
  - [ ] 🟥 Leave the existing signature/semantics intact for `vision_model=None` callers.
  - **Verify:** 
    1. Existing `tests/test_scout_notes_inventory.py` passes unchanged (no-vision regressions).
    2. New test: call `build_notes_inventory(finco_pdf, notes_start=18)` with `vision_model=None` → returns `[]`, no exception.
    3. New test: call `build_notes_inventory(finco_pdf, notes_start=18, vision_model=TestModel(...))` → returns non-empty list (mocked vision).

- [x] 🟩 **Step 3.2: Wire the scout tool to pass the model through**
  - [ ] 🟥 In `scout/agent.py:509-540`, update `discover_notes_inventory` to call `build_notes_inventory(..., vision_model=ctx.deps.vision_model)`.
  - [ ] 🟥 Emit a progress event when the vision fallback fires: `"PyMuPDF found no text — using vision to build inventory..."`.
  - [ ] 🟥 Update the tool docstring: keep the "may return empty on scanned PDFs where no model is available" caveat, drop the "you must fall back to viewing pages yourself" instruction (which never worked reliably).
  - **Verify:** Read the updated tool — docstring reflects new behaviour. Run `pytest tests/ -v` — everything green.

### Phase 4 — Integration against FINCO — 🟩 Done

- [x] 🟩 **Step 4.1: Fixture prep**
  - [ ] 🟥 Decide fixture approach: copy `output/bdcc769d…/uploaded.pdf` → `tests/fixtures/finco_scanned.pdf` (15 MB is large for a fixture; alternative: extract only pages 18-32 via PyMuPDF into a smaller fixture PDF ≤ 2 MB).
  - [ ] 🟥 Add a `pytest.mark.live` integration test `tests/test_scout_notes_inventory_vision_live.py` that:
    - skips unless `TEST_MODEL` env var is set (same pattern as `tests/test_pdf_viewer.py`)
    - loads the fixture, calls `build_notes_inventory(..., vision_model=<real proxy model>)`
    - asserts ≥ 10 entries, contains `note_num=1` and `note_num=13` (from the PDF), page ranges within PDF bounds, monotonically increasing note numbers.
  - **Verify:** `TEST_MODEL=openai.gpt-5.4 pytest -m live tests/test_scout_notes_inventory_vision_live.py -v` produces ≥ 10 correct entries (look at test output).

- [x] 🟩 **Step 4.2: End-to-end Sheet 12 on FINCO** — verified indirectly: (1) live test proves `build_notes_inventory` with `vision_model` populates 15 entries from FINCO's scanned pages; (2) `test_infopack_roundtrip_preserves_notes_inventory` proves the infopack persists them; (3) `test_notes12_e2e.py` proves the Sheet-12 coordinator fans out on a non-empty inventory. Final browser click-through is a user smoke test — run with `./start.sh` → upload FINCO → check "list of notes" → verify 5 sub-agents spawn.
  - [ ] 🟥 Run the web UI against FINCO with Sheet 12 enabled: `./start.sh`, upload, select notes template `list_of_notes`, run.
  - [ ] 🟥 Observe scout phase: the progress feed should show the new vision-fallback event; infopack should land with `notes_inventory` populated (≥ 10 entries).
  - [ ] 🟥 Observe Sheet 12 sub-coordinator: 5 sub-agents should spawn, not a failure event.
  - [ ] 🟥 Inspect `output/<session>/NOTES_LIST_OF_NOTES_filled.xlsx` — row 112 ("Disclosure of other notes to accounts") plus one row per matched note.
  - **Verify:** Run completes with Sheet 12 status = succeeded (or partial success with side-log) and the XLSX has content. Compare against the ground truth (15 notes in the FINCO PDF, sheet 12 should match ≥ 10 of them to real template rows).

### Phase 5 — Polish & safety rails — 🟨 In Progress (5.4 pending user)

- [x] 🟩 **Step 5.1: Cost visibility**
  - [ ] 🟥 Log total vision-call token usage from `_vision_inventory` (sum of `result.usage()` across batches) at INFO level so operators see what the fallback cost.
  - **Verify:** `grep "vision inventory tokens" server.log` on the FINCO run shows a single line with input+output counts.

- [x] 🟩 **Step 5.2: Failure-mode tests**
  - [ ] 🟥 Unit test: single batch raises `VisionBatchError` → `_vision_inventory` still returns entries from other batches and logs the failure.
  - [ ] 🟥 Unit test: all batches raise → `_vision_inventory` returns `[]` and logs an ERROR. `build_notes_inventory` returns `[]`. `notes/coordinator.py` fails Sheet 12 with its existing loud-fail — unchanged contract.
  - **Verify:** `pytest tests/test_notes_discoverer_vision.py -k failure -v` all green.

- [x] 🟩 **Step 5.3: Documentation updates**

### Phase 6 — Peer-review follow-ups (2026-04-20) — 🟩 Done

- [x] 🟩 **Fix 6.1 [HIGH] Out-of-bounds `notes_start_page` crash** — `_resolve_vision_range` in `scout/notes_discoverer.py` short-circuits to `[]` with a warning log when `notes_start_page > effective_end`, instead of letting `_chunk` raise `ValueError`. Both sync and async entry points share the guard. Regression: `test_build_notes_inventory_start_past_end_returns_empty`.

- [x] 🟩 **Fix 6.2 [MEDIUM] Terminal-note spillover** — `_merge_and_stitch` now uses `derived_end = min(cur.last_page, notes_end)` for the terminal note (previously: blind `notes_end = pdf_length` stretch that absorbed Directors' Statement / auditor's report pages). New optional kwarg `notes_end_page` on `build_notes_inventory` caps both the vision scan range and the terminal clamp when callers know the real boundary. Regressions: `test_terminal_note_clamps_to_notes_end`, `test_terminal_note_does_not_absorb_post_notes_pages`, `test_build_notes_inventory_notes_end_page_caps_vision_range`.

- [x] 🟩 **Fix 6.3 [MEDIUM] Live-test upper bound** — `test_finco_vision_inventory_live` now asserts `last_page <= pdf_length` for every entry (previously only `first_page >= notes_start` and `last >= first`). Live re-run: 15/15 notes, all ranges ≤ 37. 

- [x] 🟩 **Fix 6.4 [LOW] Plan/code alignment** — Key Decisions bullet on hallucination guards tightened to reflect shipped behaviour (stitcher invariants + schema validation; monotonicity automatic; gap-size deferred pending evidence).

### Phase 7 — Second-round peer-review follow-ups (2026-04-20) — 🟩 Done

Six in-scope findings from the branch-level code review. One finding (reviewer's "dead `max(last_page)` in dedup") was evaluated and **rejected** — the terminal-note stitcher branch reads `cur.last_page`, so the widening isn't dead; the reviewer missed that case. All other fixes applied.

- [x] 🟩 **Fix 7.1 — bounds check before PDF text read** — `_resolve_vision_range` now short-circuits on `notes_start_page > vision_end` before iterating `doc[pn].get_text()` over the whole document. Perf improvement on very long filings with bad scout hints.
- [x] 🟩 **Fix 7.2 — drop shared `fitz.Document` from `_scan_batch`** — rendering is already isolated via `render_pages_to_png_bytes`; the outer `fitz.open` in `_vision_inventory` served only `doc.name`. Removed. `_scan_batch` now takes `pdf_path: str` directly. Cleaner seam for future refactors and removes the misleading "shared doc across coroutines" appearance.
- [x] 🟩 **Fix 7.3 — retry backoff** — added `await asyncio.sleep(0.5)` before the second (and only) retry in `_scan_batch` so a transient 429 doesn't re-fire instantly. Happy-path latency unaffected.
- [x] 🟩 **Fix 7.4 — evaluated and rejected** — peer-review suggested `max(last_page)` in `_merge_and_stitch` dedup is dead work. **Not dead**: the terminal-note branch of the stitcher reads `cur.last_page` (`min(cur.last_page, notes_end)`); widening the last_page at dedup keeps the terminal note from under-extending when one batch saw less content than another. Kept the `max`; added a comment explaining why.
- [x] 🟩 **Fix 7.5 — C6 redundant `except` tuple** — collapsed `except (AttributeError, Exception)` → `except Exception` in the cost-telemetry block. Reviewer's claim that this "swallows CancelledError" is wrong for Python 3.8+ (`asyncio.CancelledError` inherits from `BaseException`, not `Exception`); verified with `issubclass(CancelledError, Exception) == False`. Cancellation propagates cleanly.
- [x] 🟩 **Fix 7.6 — log string-model degrade** — `build_scout_agent` now INFO-logs when `model` is a plain string (tests, CLI paths) and `vision_model` resolves to `None`. Operators debugging an empty Sheet-12 on a scanned PDF can now tell at a glance whether the fallback was even eligible to fire.
  - [ ] 🟥 Update `CLAUDE.md` "Notes Feature" section (#14) with a one-paragraph note: "Scout's notes-inventory builder now falls back to vision when PyMuPDF extracts no text (scanned PDFs). Fallback fires only when `vision_model` is passed from the scout; the empty-inventory loud-fail in `notes/coordinator.py` remains as a last-resort signal."
  - [ ] 🟥 Update the "Files That Must Stay in Sync" table row for "Notes template registry" or add a new row "Notes inventory discovery" listing `scout/notes_discoverer.py`, `scout/notes_discoverer_vision.py`, `scout/agent.py` (`ScoutDeps.vision_model`, `discover_notes_inventory` tool), `tests/test_scout_notes_inventory*.py`.
  - **Verify:** `git diff CLAUDE.md` contains the new paragraph and sync-row entries.

- [ ] 🟨 **Step 5.4: Commit and ship** — awaiting explicit user approval before creating commits / opening PR.
  - [ ] 🟥 One commit per phase (1→5) with messages that describe what each phase adds (foundation, vision helper, wire-through, FINCO integration, polish).
  - [ ] 🟥 Push branch, open PR, include a "before/after" snippet: the FINCO error vs the populated inventory.
  - **Verify:** `git log --oneline` shows clean phase boundaries; CI green.

## Rollback Plan

If something goes wrong mid-implementation or after deploy:

- **Code-level:** `git revert <commit>` for the offending phase. Each phase is a separate commit so rollback is surgical. The vision fallback is strictly additive — reverting it leaves text-PDF behaviour unchanged.
- **Config-level escape hatch:** the `vision_model` kwarg defaults to `None`. In an emergency (e.g. runaway LLM costs), set `ScoutDeps.vision_model = None` unconditionally in `build_scout_agent` — this disables the fallback without touching any other code. Scanned PDFs will go back to loud-failing Sheet 12, but text-based PDFs and all other statements are unaffected.
- **Data to check on rollback:**
  - `output/xbrl_agent.db` — any runs that succeeded via the vision fallback will still have `notes_inventory` persisted in their `run_config_json` snapshot; those artefacts are read-only and don't need reverting.
  - Per-run `NOTES_LIST_OF_NOTES_filled.xlsx` files are point-in-time outputs; no cross-run state.
- **Watch window:** first 3 scanned-PDF Sheet-12 runs after deploy — inspect `notes_inventory` length, compare to manual count, confirm sub-coordinator fans out 5 batches.

## Rules

- No scope creep. The task is the vision fallback for `build_notes_inventory` only. Do not touch `notes/coordinator.py` or any other notes-agent code.
- Each step's **Verify** is the acceptance gate — do not advance without it passing.
- Keep `scout/notes_discoverer.py` pure/deterministic; all vision code lives in `scout/notes_discoverer_vision.py`.
- Temperature = 1.0 on all LLM calls (CLAUDE.md #5). No exceptions.
- Don't add retries beyond the one specified per batch in Step 2.3 — compounding retries hide real failures.
