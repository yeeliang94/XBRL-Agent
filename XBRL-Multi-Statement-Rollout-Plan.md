# Implementation Plan: Multi-Statement XBRL Extraction (Sheets 01–09)

**Overall Progress:** `~85%` (Phase 0–9 complete, 5 peer reviews done)
**Last Updated:** 2026-04-06
**Test count:** 274 passed (backend) + 109 passed (frontend), 1 deselected (regression marker)
**Methodology:** Red/Green/Refactor TDD — every step writes a failing test first, then minimal code to pass, then refactor.

---

## Summary

Expand the current single-agent SOFP-only pipeline to handle all 5 statement types (SOFP, SOPL, SOCI, SOCF, SOCIE) across variants. A scout agent reads the PDF's Table of Contents, calibrates page-number offsets against actual PDF content, and produces a validated "infopack" (statement → page ranges). A Python coordinator then runs 5 extraction sub-agents concurrently, each scoped to its statement's pages. A deterministic cross-validator runs 5 MFRS reconciliation checks. Results merge into one workbook. A SQLite event store captures per-agent activity for auditing. The frontend gains a tab-based multi-agent progress view, a variant-selection pre-run page, a settings page with per-agent model overrides, and a run-history view.

No LLM orchestrator in this phase — coordinator is plain Python. LLM orchestrator with conversation-continuation corrections is deferred to a later phase.

---

## Key Decisions

- **No LLM orchestrator in Phase 1** — scout + Python coordinator + deterministic validator cover all needs. LLM orchestrator deferred until correction loops are needed.
- **Scout is a vision mini-agent** — reads TOC page(s) visually, extracts stated page numbers, then probes actual pages to calibrate offset. Validates each statement's header visible on the candidate page before locking it in.
- **Scout is toggleable per run** — user can disable scout in the pre-run UI. When OFF, sub-agents receive no infopack and fall back to full-PDF exploration (they use `view_pdf_pages` freely to find their own sections). When ON (default), sub-agents receive page hints as soft guidance (recommended starting points) but can freely view any page in the PDF. Toggle is exposed in pre-run UI and in settings as a default.
- **Page-offset calibration is required** — TOC-stated page numbers typically differ from actual PDF page indices (cover pages, prefaces, etc.). Scout must validate every page before sub-agents use it.
- **Variant selection: user pre-selects OR auto-detect via scout** — scout returns recommendations, user confirms/overrides before run starts.
- **Parallelism via `asyncio.gather`** — 5 sub-agents run concurrently, each writing to its own workbook file; merged at the end (zero write contention).
- **SQLite local database** — single-user, file-based, works identically on Mac and Windows. Event store granularity = coarse (tool calls, status, tokens, errors + full conversation_trace.json as blob).
- **Cross-statement checks are deterministic Python** — no LLM. 5 P0 checks, RM 1 absolute tolerance.
- **Models user-configurable per-agent per-run** — defaults in settings, overridable pre-run. Starter pinned list: Gemini 3 Flash Preview, Gemini 3 Pro, Claude Sonnet 4.6, Claude Opus 4.6, Claude Haiku 4.5, GPT-5.4, GPT-5.4 mini.
- **PDFs retained per-run** for auditing; no purge in Phase 1.
- **Scout model is user-configurable**, defaults to extraction model.
- **Frontend stays inline-styles React** (per CLAUDE.md §6).
- **Phase-2 deferrals**: LLM orchestrator for corrections, post-extraction field-level review UI, YoY sanity checks, completeness checks, authentication, multi-tenancy.

---

## Pre-Implementation Checklist
- [ ] 🟥 All questions from /explore resolved
- [ ] 🟥 OpenAI GPT-5.4 / 5.4 mini exact model-ID strings confirmed (user fills in after reading OpenAI docs)
- [ ] 🟥 Verify all 9 templates in `XBRL-template-MFRS/` have stable sheet names and row structures (they're flagged as non-final in CLAUDE.md)
- [ ] 🟥 Collect 2–3 representative Malaysian annual-report PDFs for end-to-end testing (need variety: CuNonCu vs OrderOfLiquidity, different TOC offsets)

---

## Post-Phase-2 Peer Review (2026-04-06)

A code review was performed after Phase 0–2 completion. 4 of 5 findings were confirmed and fixed in-place:

| # | Sev | Finding | Fix |
|---|-----|---------|-----|
| 1 | HIGH | `final_status = "succeeded"` set from save_result tool START (premature); workbook path never harvested from terminal event | Derive success + `excel_path` only from terminal `complete` event |
| 2 | HIGH | Per-event SQLite transactions + all events persisted (including thinking/text deltas) | Filter to 5 coarse event types; single long-lived connection with WAL + `busy_timeout=5000` |
| 3 | MED | `matches_pdf=True` for non-SOFP = false-positive verification signal | Changed to `Optional[bool]`; non-SOFP returns `None` |
| 4 | MED | Regression test: `XBRL_OUTPUT_DIR` not read by `run.py`; globbed stale artefacts; not excluded by default | Added `--output-dir` CLI arg; test uses isolated `tmp_path`; `addopts = -m "not regression"` |
| 5 | LOW | Section-header tests could have more per-template snapshots | Deferred — current coverage (non-empty × 9 + spot-check + legacy compat) is adequate for Phase 1 |

## Post-Phase-3 Peer Review (2026-04-06)

A code review was performed after Phase 3 completion. All 4 findings were confirmed and fixed:

| # | Sev | Finding | Fix |
|---|-----|---------|-----|
| 1 | HIGH | `run_scout()` never calls `extract_toc_via_vision()` for scanned PDFs — returns empty Infopack | Wired vision fallback: when `parse_toc_entries_from_text()` returns empty, calls `extract_toc_via_vision()` and converts results via `_vision_entries_to_toc_entries()` |
| 2 | HIGH | When calibration fails (`actual_page=0`), `stated_page` promoted to `face_page` — unvalidated guess in "validated" infopack | LOW-confidence entries now omitted from infopack entirely; logged with warning for user resolution |
| 3 | MED→LOW | Note discovery uses single "typical" offset; text-only extraction fails on scanned PDFs | Changed to per-statement calibrated offset (`cal_page.offset`). Text-only gap accepted: note pages are heuristic hints, sub-agents navigate freely |
| 4 | MED | Tests only cover text-based path; vision.py untested; image-only run_scout() untested | Added `TestRunScoutImageOnly` (vision fallback, empty-vision), `TestLowConfidenceOmission`, `TestExtractTocViaVision`, `TestVisionEntriesToTocEntries` — 11 new tests |

## Post-Phase-4 Peer Review (2026-04-06)

A code review was performed after Phase 4 completion. All 5 findings were confirmed and fixed:

| # | Sev | Finding | Fix |
|---|-----|---------|-----|
| 1 | HIGH | Coordinator ignores `infopack.variant_suggestion`, falls back to first registered variant | Added variant_suggestion as middle fallback: `config.variants[stmt]` → `infopack.variant_suggestion` → first registered variant |
| 2 | HIGH | `save_result()` writes shared `result.json` / `cost_report.txt` — concurrent agents overwrite | Namespaced: `{STMT}_result.json`, `{STMT}_cost_report.txt` |
| 3 | HIGH | SOFP prompt hardcoded to CuNonCu sheet names; wrong for OrderOfLiquidity | Created `prompts/sofp_orderofliquidity.md` with correct sheet names; `prompts/__init__.py` prefers variant-specific prompt files |
| 4 | MED | `fill_workbook` tool docstring doesn't document explicit row+col mode (needed for SOCIE matrix) | Updated docstring to document both label-matching and explicit coordinate modes; updated SOCIE prompt |
| 5 | MED | SOPL prompt claims `verify_totals()` checks internal consistency (it doesn't) | Softened to "reports verification status (balance checks are SOFP-only for now)" |

## Post-Phase-5 Peer Review (2026-04-06)

A code review was performed after Phase 5 completion. All 5 findings were confirmed and fixed:

| # | Sev | Finding | Fix |
|---|-----|---------|-----|
| 1 | CRITICAL | `find_value_by_label` uses `data_only=False` + `float()` on formula cells → returns None on real templates | Added `wb` param; formula cells evaluated via verifier's `_resolve_cell_value()`. Also fixed greedy substring matching (exact match preferred over containment) |
| 2 | HIGH | SOCI sheet name is `SOCI-BeforeOfTax` (not `SOCI-BeforeTax`) | Added `"SOCI-BeforeOfTax"` as first candidate in `find_sheet()` calls |
| 3 | HIGH | SOCIE closing label is `*Equity at end of period` (not `*Balance at end of period`) | Changed to `"equity at end of period"` |
| 4 | HIGH | `run_all()` crashes on missing workbook paths (agent failed → no workbook → KeyError) | Added workbook-path guard (→ failed) + try/except around `.run()` |
| 5 | MED | Tests use synthetic workbooks only — missed real-template regressions | Added 6 smoke tests against real MBRS templates |

**Post-review refinements (user-initiated):**
- `sopl_to_socie_profit.py` and `soci_to_socie_tci.py` updated to handle NCI (Non-controlling interests): when SOCIE column W has data, compare against Total column (X=24) instead of Retained earnings (C=3). This ensures the group-level figure matches SOPL/SOCI for consolidated entities.

## Post-Phase-7 Peer Review (2026-04-06)

A code review was performed after Phase 6–7 completion. 5 of 7 findings were confirmed and fixed in-place:

| # | Sev | Finding | Fix |
|---|-----|---------|-----|
| 1 | HIGH | Scout endpoint passes raw model string, bypassing enterprise proxy wiring via `_create_proxy_model()` | Added `api_key`/`proxy_url` loading + `_create_proxy_model()` call in `scout_pdf()` before dispatching to `run_scout()` |
| 2 | HIGH | Per-agent model overrides stored as strings bypass proxy/direct wiring | Each model override now resolved through `_create_proxy_model()` before building `RunConfig`. Coordinator `RunConfig.model` and `.models` widened from `str` to `Any` to accept Model objects |
| 3 | HIGH | `run_complete.success` and DB `run_status` ignore merge failure — merge can fail silently while reporting success | `success = all_succeeded AND merge_result.success`. DB status uses 3 levels: `completed` / `completed_with_errors` (agents OK, merge failed) / `failed` |
| 4 | MED→LOW | No live SSE multiplexing — events emitted only after `asyncio.gather()` completes | Already documented as known deferral in plan. True interleaved streaming deferred to Phase 10 (frontend tabs need it) |
| 5 | MED | Multi-agent runs don't persist `agent_events` — audit trail missing | Added coarse event logging (`status` + `complete`) per agent during DB persistence phase |
| 6 | MED→LOW | Integration tests too mocked to catch findings 1–3 | Added `tests/test_multi_agent_integration.py` with 2 integration tests exercising real `run_multi_agent_stream()` with mocked coordinator. Validates SSE payloads, DB side effects, and merge-failure degradation |
| 7 | LOW | `coordinator.py` uses `logger.error(f"...")` dropping traceback | Changed to `logger.exception()` with structured `extra` fields |

## Post-Phase-9 Peer Review (2026-04-06)

A code review was performed after Phase 8–9 completion. 4 of 6 findings were confirmed and fixed in-place:

| # | Sev | Finding | Fix |
|---|-----|---------|-----|
| 1 | HIGH | Multi-agent SSE emits per-agent `complete` + final `run_complete`, but frontend treats first `complete` as terminal — drops subsequent agents and aggregate | Added `run_complete` SSE event type to frontend; `createMultiAgentSSE` only terminates on `run_complete`/`error`; `appReducer` distinguishes per-agent `complete` (has `agent_id`) from legacy terminal `complete` |
| 2 | HIGH | PreRunPanel reads `info.variant` but backend sends `variant_suggestion`; confidence is uppercase `HIGH/MEDIUM/LOW` but frontend expects lowercase | Changed to `info.variant_suggestion`; added `.toLowerCase()` normalization; added integration test with real scout payload shape |
| 3 | HIGH | `scout_pdf()` ignores `default_models.scout` setting, always uses `TEST_MODEL` | Added `_load_extended_settings()` call; resolves scout model from `default_models.scout` with fallback to global model; new test verifies |
| 4 | MED | Failed `getSettings()` leaves PreRunPanel stuck on "Loading settings..."; auto-detect doesn't check `response.ok` | Added `loadError`/`scoutError` state; `.catch()` on settings fetch; `response.ok` check + error message extraction in auto-detect |
| 5 | MED→LOW | POST /api/settings uses untyped `dict` body | Deferred — existing key-by-key pattern works for single-user deployment; invalid keys ignored safely. Typed model is a nice-to-have |
| 6 | LOW | OpenAI placeholder model IDs in `models.json` | Already tracked in pre-implementation checklist as open item. Not a new finding |

---

## Learnings

- **MBRS template sheet names are quirky** — `SOCI-BeforeOfTax` (not `BeforeTax`), `SOFP-OrdOfLiq` (not `OrderOfLiquidity`). Always verify against actual template files, never assume from variant names.
- **Total rows are always formulas** in MBRS templates. Any code that reads totals must evaluate formulas, not just `float()` the cell value.
- **Substring label matching is dangerous** — `"assets" in "total assets"` is True, so section headers match before total rows. Two-pass (exact first, substring fallback) is required.
- **Empty templates return 0.0 for formula cells, None for data-entry cells** — smoke tests must account for this distinction.
- **DB schema was forward-planned well** — `cross_checks` table and `save_cross_check()`/`fetch_cross_checks()` were built in Phase 2, so Phase 5 persistence was free.
- ~~**Coordinator→DB wiring is deferred**~~ — Done in Phase 7.4. Server orchestrates the full pipeline: coordinator → merger → cross-checks → DB persistence.
- **Cross-sheet formulas only span within a single template** — e.g. SOFP-CuNonCu references SOFP-Sub-CuNonCu, but never a different statement's sheet. This made the merger trivially correct: copy sheets from each workbook into the merged file, and all formulas resolve because source and target sheets live together. No cross-statement formula remapping was needed.
- **Model objects must flow through the entire pipeline on enterprise proxy** — passing raw model-name strings works for direct Google API, but bypasses the enterprise proxy wiring (`_create_proxy_model`). Every model reference — default, per-agent overrides, scout — must be resolved through the proxy factory. The coordinator's `RunConfig.model` and `.models` fields were widened from `str` to `Any` to carry provider-backed Model objects.
- **Merge failure is a distinct state from extraction failure** — all agents can succeed but the merge can still fail (e.g. disk full, corrupt workbook). DB run status needs three levels: `completed` (all agents + merge OK), `completed_with_errors` (agents OK, merge failed), `failed` (agent failures). The SSE `run_complete.success` flag must be `all_succeeded AND merge_success`.
- **Test mocking granularity matters** — mocking at the SSE stream boundary (replacing `run_multi_agent_stream` entirely) validates API shape but misses internal wiring bugs (proxy bypass, missing DB writes). Integration tests that mock only the LLM-touching layers (coordinator, scout) while exercising real orchestration catch these issues.

---

## TDD Convention

Each step follows **RED → GREEN → REFACTOR**:
1. **RED**: write a test that expresses desired behaviour and fails
2. **GREEN**: write the minimum code to make the test pass
3. **REFACTOR**: clean up without changing behaviour; tests must stay green
4. **Verify**: explicit manual/automated check proving the step works end-to-end

---

## Tasks

### Phase 0: Pre-work & Safety Nets 🟩

- [x] 🟩 **Step 0.1: Baseline test suite** — ensure existing SOFP pipeline tests still pass so we can detect regressions.
  - [x] 🟩 Run `python -m pytest tests/ -v` and document the current baseline (pass/fail list) in `tests/BASELINE.md`
  - [x] 🟩 Run `cd web && npx vitest run` and document baseline
  - **Verify:** ✅ `tests/BASELINE.md` exists; 70 passing + 2 pre-existing failures + 83 frontend passing.
  - **Result:** baseline captured: 70 pass, 2 fail (`.env.example` deleted pre-rollout), 83 frontend pass.

- [x] 🟩 **Step 0.2: Freeze SOFP golden output** — capture a known-good extraction result as the regression oracle.
  - [ ] ⬜ Run the current agent on `data/FINCO-Audited-Financial-Statement-2021.pdf` with `SOFP-Xbrl-template.xlsx` — **deferred: requires LLM API key; test skeleton + golden README written**
  - [ ] ⬜ Save the resulting `filled.xlsx` to `tests/fixtures/golden/SOFP_FINCO_2021_filled.xlsx` — **pending golden generation**
  - [ ] ⬜ Save the `result.json` to `tests/fixtures/golden/SOFP_FINCO_2021_result.json` — **pending golden generation**
  - [x] 🟩 Write a regression test `tests/test_sofp_regression.py::test_sofp_extraction_matches_golden` that is SKIPPED by default (marker `@pytest.mark.regression`) — it runs the whole SOFP pipeline and compares the numeric cells to golden
  - **Verify:** ✅ `pytest -m regression` selects the test (skips because goldens missing); normal `pytest` deselects it entirely (`addopts = -m "not regression"`).
  - **Peer-review fix (Finding 4):** added `--output-dir` CLI arg to `run.py`; test uses isolated tmp_path instead of globbing repo `output/`; `pytest.ini` excludes `regression` marker by default.

---

### Phase 1: Template Abstraction (remove SOFP hardcoding) 🟩

Goal: make `fill_workbook`, `verifier`, and `template_reader` parametric over statement type, driven by the template itself rather than literal strings in Python.

- [x] 🟩 **Step 1.1: Statement-type registry** — central definition of the 5 statement types and their variants.
  - [x] 🟩 **RED**: `tests/test_statement_types.py` — 5 tests covering enum members, variant→file mappings, template-path resolution, unknown-variant error, detection signals.
  - [x] 🟩 **GREEN**: `statement_types.py` — `StatementType` enum, `Variant` frozen dataclass, `VARIANTS` dict (9 entries), `get_variant()`, `template_path()`, `variants_for()`.
  - [x] 🟩 **REFACTOR**: detection signals for scout validation baked into each `Variant`.
  - **Verify:** ✅ `pytest tests/test_statement_types.py -v` — 5 passed.

- [x] 🟩 **Step 1.2: Template-driven section headers** — replace hardcoded `_MAIN_SECTION_HEADERS` / `_SUB_SECTION_HEADERS` in `fill_workbook.py` with runtime discovery from the template.
  - [x] 🟩 **RED**: `tests/test_section_headers.py` — parametric test across all 9 MFRS templates + SOFP-CuNonCu spot-check + legacy backward-compat check.
  - [x] 🟩 **GREEN**: `tools/section_headers.py` — `discover_section_headers(ws)` + `header_set(wb, sheet)`. Detection by fill colour (`FFD6E4F0` new blue, `FFC0C0C0` legacy grey) with keyword fallback.
  - [x] 🟩 **REFACTOR**: `fill_workbook.py` now calls `header_set(...)` instead of hardcoded sets. Old constants renamed to `_LEGACY_*_HEADER_KEYWORDS` and passed as `extra_keywords` safety net. Also detects "analysis" in sheet names as sub-sheet (for `SOPL-Analysis-Function` etc.).
  - **Verify:** ✅ `pytest tests/test_section_headers.py tests/test_workbook_filler.py -v` — 25 passed (11 new + 14 existing).

- [x] 🟩 **Step 1.3: Parametric verifier** — make `verifier.py` work for any statement, not just SOFP.
  - [x] 🟩 **RED**: `tests/test_verify_statement.py` — 7 tests: SOFP delegates to verify_totals, 4× non-SOFP returns `is_balanced=None`, missing-file raises, backward-compat.
  - [x] 🟩 **GREEN**: `verify_statement(path, statement_type, variant)` added. SOFP→`verify_totals()`, others→`is_balanced=None`. `VerificationResult.is_balanced` changed to `Optional[bool]`.
  - [x] 🟩 **REFACTOR**: dispatch on `statement_type.value`, late import to avoid circular deps.
  - **Verify:** ✅ `pytest tests/test_verifier.py tests/test_verify_statement.py -v` — 21 passed (14 existing + 7 new).
  - **Peer-review fix (Finding 3):** `matches_pdf` changed to `Optional[bool]`; non-SOFP returns `matches_pdf=None` ("not compared") instead of `True` (false positive).

---

### Phase 2: Database Layer 🟩

Goal: replace file-only state with SQLite for audit trail + run history.

- [x] 🟩 **Step 2.1: Schema + migrations** — create SQLite database with 5 tables.
  - [x] 🟩 **RED**: `tests/test_db.py` — 6 tests: schema_created, expected_columns, idempotent, schema_version, FK cascade, parent-dir creation.
  - [x] 🟩 **GREEN**: `db/schema.py` — `init_db(path)` with `CREATE TABLE IF NOT EXISTS` for `runs`, `run_agents`, `agent_events`, `extracted_fields`, `cross_checks`, `schema_version`. FK `ON DELETE CASCADE`.
  - [x] 🟩 **REFACTOR**: indexes on all FK columns; `CURRENT_SCHEMA_VERSION = 1`.
  - **Verify:** ✅ `pytest tests/test_db.py -v` — 6 passed.

- [x] 🟩 **Step 2.2: Data-access layer** — typed functions for writing/reading runs, agents, events, fields, checks.
  - [x] 🟩 **RED**: `tests/test_db_repository.py` — 6 tests: create_run_returns_id, log_event_roundtrip, fetch_fields_by_run, fetch_cross_checks_by_run, finish_run_agent, session_rollback.
  - [x] 🟩 **GREEN**: `db/repository.py` — dataclasses (`Run`, `RunAgent`, `AgentEvent`, `ExtractedField`, `CrossCheck`) + CRUD + `db_session()` context manager.
  - [x] 🟩 **REFACTOR**: `db_session()` handles connect + FK pragma + commit/rollback + close.
  - **Verify:** ✅ `pytest tests/test_db_repository.py -v` — 6 passed.

- [x] 🟩 **Step 2.3: Wire existing SOFP pipeline to write to DB** — coarse events now land in SQLite.
  - [x] 🟩 **RED**: `tests/test_integration_db.py` — `test_sofp_run_populates_db` + `test_recorder_filters_non_coarse_events`.
  - [x] 🟩 **GREEN**: `db/recorder.py::SSEEventRecorder` wraps event stream in `server.py`. Creates run + run_agent rows on `.start()`, persists events on `.record()`, closes on `.finish()`.
  - [x] 🟩 **REFACTOR**: recorder extracted to standalone class; agents stay unaware of SQL.
  - **Verify:** ✅ `pytest tests/test_integration_db.py -v` — 2 passed.
  - **Peer-review fix (Finding 1):** `final_status` now derived only from terminal `complete` event (not premature `status.phase="complete"`); `excel_path` harvested instead of `output_path`.
  - **Peer-review fix (Finding 2):** recorder filters to coarse event types only (`status`, `tool_call`, `tool_result`, `error`, `complete`); uses a single long-lived SQLite connection with WAL mode + `busy_timeout=5000` instead of per-event `db_session()` open/commit cycles.

---

### Phase 3: Scout Agent (Page-Offset Calibration) 🟩

Goal: a vision mini-agent that reads the PDF, calibrates TOC offset, and produces a validated infopack.

- [x] 🟩 **Step 3.1: Infopack data model** — typed structure for what scout returns.
  - [x] 🟩 **RED**: `tests/test_scout_infopack.py` — 10 tests: shape, serialisation round-trip, validation (face_page>0, note_pages>0, confidence enum, page-range check).
  - [x] 🟩 **GREEN**: `scout/infopack.py` — `StatementPageRef` + `Infopack` dataclasses with `to_json()` / `from_json()` and `validate_page_range(pdf_length)`.
  - [x] 🟩 **REFACTOR**: validation in `__post_init__` for immediate feedback on construction.
  - **Verify:** ✅ `pytest tests/test_scout_infopack.py -v` — 10 passed.

- [x] 🟩 **Step 3.2: TOC location + parsing (no LLM yet)** — deterministic PyMuPDF pass to find candidate TOC pages.
  - [x] 🟩 **RED**: `tests/test_scout_toc.py` — 7 tests: keyword match, "Contents" variant, dotted-line patterns, image-only heuristic fallback, candidate fields, score sorting, real scanned PDF.
  - [x] 🟩 **GREEN**: `scout/toc_locator.py` — `find_toc_candidate_pages(pdf_path)` with `TocCandidate` dataclass. Scans first 15 pages for header keywords + TOC line patterns. Falls back to heuristic range (pages 2-6) for image-only PDFs.
  - [x] 🟩 **REFACTOR**: `_TOC_LINE_RE` regex extracted as module-level constant.
  - **Verify:** ✅ `pytest tests/test_scout_toc.py -v` — 7 passed. Works on both synthetic text PDFs and real scanned FINCO PDF.
  - **Note:** Both sample PDFs (FINCO, Oriental) are scanned/image-only — no selectable text. Deterministic locator returns heuristic candidates for these; LLM vision (Step 3.3) handles actual TOC reading.

- [x] 🟩 **Step 3.3: TOC entry extraction (LLM vision)** — scout reads TOC pages as images, extracts statement names + stated page numbers.
  - [x] 🟩 **RED**: `tests/test_scout_agent.py` — 8 tests: TocEntry fields, optional type, all 5 statements, page numbers, dotted lines, Malay names, multi-page TOC, empty input.
  - [x] 🟩 **GREEN**: `scout/toc_parser.py` — deterministic `parse_toc_entries_from_text()` with regex patterns for English + Malay statement names. `scout/vision.py` — PydanticAI vision agent with `VisionTocResult` Pydantic model for structured output from scanned PDFs.
  - [x] 🟩 **REFACTOR**: split into `toc_parser.py` (deterministic, testable without LLM) + `vision.py` (LLM wrapper for scanned PDFs). Pydantic model pins response format.
  - **Verify:** ✅ `pytest tests/test_scout_agent.py -v` — 8 passed.

- [x] 🟩 **Step 3.4: Offset calibration + page validation** — CRITICAL: verify TOC-stated pages against actual PDF content.
  - [x] 🟩 **RED**: `tests/test_scout_calibration.py` — 10 tests: search window (stated first, nearby, bounds, no dupes, size cap) + calibration (finds correct page, rejects false positive, variable offset per statement, all entries present).
  - [x] 🟩 **GREEN**: `scout/calibrator.py` — `calibrate_pages()` with `_build_search_window()` (±10 alternating outward) and `_validate_page_via_llm()` (mockable). Each statement calibrated independently with its own offset. LOW confidence when exhausted.
  - [x] 🟩 **REFACTOR**: `_PageValidationResult` Pydantic model for structured LLM output; variant_suggestion returned alongside found status.
  - **Verify:** ✅ `pytest tests/test_scout_calibration.py -v` — 10 passed.
  - **Note:** `pytest-asyncio` installed as new dependency for async test support.

- [x] 🟩 **Step 3.5: Variant detection per statement** — scout determines which variant applies.
  - [x] 🟩 **RED**: `tests/test_scout_variant.py` — 10 tests: SOFP CuNonCu/OrderOfLiquidity, SOPL Function/Nature, SOCF Indirect/Direct, SOCIE Default, ambiguous fallback, all types covered, integration with calibrator.
  - [x] 🟩 **GREEN**: `scout/variant_detector.py` — `detect_variant_from_signals()` scores each variant's `detection_signals` against page text. Best match wins; first variant as fallback.
  - [x] 🟩 **REFACTOR**: already data-driven — uses `detection_signals` from `statement_types.py` registry (set up in Phase 1).
  - **Verify:** ✅ `pytest tests/test_scout_variant.py -v` — 10 passed.

- [x] 🟩 **Step 3.6: Note page discovery** — scout identifies which PDF pages contain notes referenced by each statement.
  - [x] 🟩 **RED**: `tests/test_scout_notes.py` — 10 tests: extract note refs (standard, variants, empty, none, dedup), find note ranges (from TOC, heuristic, no start), end-to-end discovery (basic, no refs).
  - [x] 🟩 **GREEN**: `scout/notes_discoverer.py` — `extract_note_refs_from_text()` regex, `find_note_page_ranges()` heuristic mapping (~3 pages/note), `discover_note_pages()` composing both.
  - [x] 🟩 **REFACTOR**: capped at 30 note pages max to avoid excessive page lists.
  - **Verify:** ✅ `pytest tests/test_scout_notes.py -v` — 10 passed.

- [x] 🟩 **Step 3.7: Full scout end-to-end with subset awareness** — compose all pieces into `run_scout()`.
  - [x] 🟩 **RED**: `tests/test_scout_end_to_end.py` — 4 tests: valid infopack for all 5 statements, correct face pages, subset filtering (SOCF only → 1 statement), JSON serialisation round-trip.
  - [x] 🟩 **GREEN**: `scout/runner.py` — `run_scout(pdf_path, model, statements_to_find)` wires: toc_locator → toc_parser → calibrator → variant_detector → notes_discoverer → Infopack. Handles text-based and image-only PDFs. Subset filtering reduces LLM calls.
  - [x] 🟩 **REFACTOR**: SSE event emission deferred to Phase 7 (server integration).
  - **Verify:** ✅ `pytest tests/test_scout_end_to_end.py -v` — 4 passed. Full test suite: 166 passed, 2 pre-existing failures (`.env.example` deleted).

---

### Phase 4: Per-Statement Fill-Workflow Discovery + Extraction Sub-Agents 🟩

Goal: before writing prompts, derive concrete field-mapping rules for each new statement type by walking through the FinCo sample PDFs against the templates (same exercise that produced `XBRL-SOFP-Fill-Workflow.docx` for SOFP). Then generalise the SOFP agent into a parametric factory, one invocation per statement type.

**Why this matters:** the SOFP agent's accuracy comes largely from rules captured in its system prompt (e.g. "Accrued bonus sums into Accruals, not Other payables"; "Deferred income is sub-sheet row X, not Contract liabilities on main sheet"). These rules were derived from analysing FinCo's actual statements + template together. Each new statement type needs the same discovery pass, or the sub-agent will make the same naïve mis-mappings we already hit on SOFP.

- [x] 🟩 **Step 4.0: FinCo sample-driven fill-workflow discovery** — one workflow document per new statement type.
  - [x] 🟩 **Sub 4.0.a — SOFP refresh:** Converted to `docs/workflows/SOFP-CuNonCu-Fill-Workflow.md` + `docs/workflows/SOFP-OrderOfLiquidity-Fill-Workflow.md`.
  - [x] 🟩 **Sub 4.0.b — SOPL (Function variant):** `docs/workflows/SOPL-Function-Fill-Workflow.md`. Includes broken cross-sheet formula finding.
  - [x] 🟩 **Sub 4.0.c — SOPL (Nature variant):** `docs/workflows/SOPL-Nature-Fill-Workflow.md`.
  - [x] 🟩 **Sub 4.0.d — SOCI (BeforeTax + NetOfTax):** `docs/workflows/SOCI-BeforeTax-Fill-Workflow.md` + `SOCI-NetOfTax-Fill-Workflow.md`.
  - [x] 🟩 **Sub 4.0.e — SOCF (Indirect + Direct):** `docs/workflows/SOCF-Indirect-Fill-Workflow.md` + `SOCF-Direct-Fill-Workflow.md`.
  - [x] 🟩 **Sub 4.0.f — SOCIE:** `docs/workflows/SOCIE-Fill-Workflow.md`. Documented matrix layout, coordinate-based filling strategy, and open questions about fill_workbook tool compatibility.
  - [x] 🟩 **Workflow doc structure:** all 8 files follow the structure: overview strategy, template structure summary, field-by-field mapping table, common mistakes/sign conventions, worked example with FinCo values.
  - **Verify:** ✅ 8 workflow docs created in `docs/workflows/`. Each has overview, template structure, mapping table, rules, and worked example.
  - **Notable findings:** (1) Template 03 SOPL-Function has broken cross-sheet formulas (wrong column/row refs). (2) SOCIE requires coordinate-based filling (matrix layout). (3) FINCO uses direct-method SOCF. (4) FINCO has no OCI items (SOCI is trivial).

- [x] 🟩 **Step 4.1: Statement-specific prompt templates** — 5 system prompts (one per statement) built from templates, incorporating rules captured in 4.0.
  - [x] 🟩 **RED**: `tests/test_prompts.py` — 13 tests covering all 5 statement types, template summary embedding, page hints vs self-navigation.
  - [x] 🟩 **GREEN**: `prompts/` directory with `_base.md` (shared persona), `sofp.md`, `sopl.md`, `soci.md`, `socf.md`, `socie.md`. `prompts/__init__.py` with `render_prompt(statement_type, variant, template_summary, page_hints)`.
  - [x] 🟩 **REFACTOR**: SOFP prompt content lives in `prompts/sofp.md` (original `agent.py` prompt preserved for backward compat).
  - **Verify:** ✅ `pytest tests/test_prompts.py -v` — 13 passed. Full suite: 192 passed.

- [x] 🟩 **Step 4.2: Generic extraction agent factory** — parametric replacement for `create_sofp_agent`.
  - [x] 🟩 **RED**: `tests/test_extraction_agent.py` — 9 tests: creates agent for each statement, has required tools, system prompt contains statement content, deps carry metadata/hints, backward compat.
  - [x] 🟩 **GREEN**: `extraction/agent.py` — `ExtractionDeps` class + `create_extraction_agent()` factory. Tools: `read_template`, `view_pdf_pages`, `fill_workbook`, `verify_totals` (uses `verify_statement`), `save_result`.
  - [x] 🟩 **REFACTOR**: `create_sofp_agent` in `agent.py` preserved as backward-compat wrapper. Added `from __future__ import annotations` to agent.py for Python 3.9 compat.
  - **Verify:** ✅ `pytest tests/test_extraction_agent.py -v` — 9 passed. Full suite: 201 passed.

- [x] 🟩 **Step 4.3: Page hints provide soft guidance to sub-agents** — scout page hints appear in the system prompt as recommended starting points. Sub-agents can view any page in the PDF regardless of hints. When scout is OFF, sub-agents self-navigate via TOC.
  - [x] 🟩 **RED + GREEN**: `tests/test_page_hints.py` — 5 tests: no allowed_pages attribute, no restriction parameter, no restrictive prompt wording, page hints in prompt, self-navigation in prompt.
  - [x] 🟩 **GREEN**: `view_pdf_pages` tool in `extraction/agent.py` validates page range (1-N) but does not restrict based on scout hints. `allowed_pages` mechanism fully removed (2026-04-09).
  - [x] 🟩 **GREEN**: self-navigation section in prompt instructs TOC-first strategy when no hints.
  - **Verify:** ✅ `pytest tests/test_page_hints.py -v` — 5 passed. Full suite: 206 passed.

- [x] 🟩 **Step 4.4: Per-statement workbook isolation** — each sub-agent writes to its own workbook file.
  - [x] 🟩 **RED + GREEN**: `tests/test_workbook_isolation.py` — 3 tests: filename includes statement type, unique per statement, concurrent writes produce separate files.
  - [x] 🟩 **GREEN**: `ExtractionDeps.filled_filename` = `{statement_type}_filled.xlsx`. `fill_workbook` tool uses per-statement path.
  - **Verify:** ✅ `pytest tests/test_workbook_isolation.py -v` — 3 passed. Full suite: 209 passed.

- [x] 🟩 **Step 4.5: Python coordinator** — fan out to 5 sub-agents concurrently, with or without an infopack.
  - [x] 🟩 **RED + GREEN**: `tests/test_coordinator.py` — 5 tests: runs selected statements with infopack, runs without infopack, returns per-agent results, handles agent failure gracefully, all 5 statements.
  - [x] 🟩 **GREEN**: `coordinator.py` with `RunConfig`, `AgentResult`, `CoordinatorResult`, `run_extraction()` using `asyncio.gather`. Resolves template paths, builds page hints from infopack, handles per-agent model overrides.
  - [x] 🟩 **REFACTOR**: agent failures caught and reported as `status="failed"` without crashing other agents.
  - **Verify:** ✅ `pytest tests/test_coordinator.py -v` — 5 passed. Full suite: 214 passed, 1 deselected (regression marker).

---

### Phase 5: Cross-Statement Validator 🟩

Goal: deterministic Python checks that run after all sub-agents finish.

- [x] 🟩 **Step 5.1: Cross-check framework** — pluggable check registry.
  - [x] 🟩 **RED**: `tests/test_cross_checks.py` — 9 tests: framework runs registered checks, multiple results, result fields, pending on missing statement, not_applicable on variant mismatch, variant match runs, SOCIE missing triggers multiple pending, missing workbook returns failed, check exception caught gracefully.
  - [x] 🟩 **GREEN**: `cross_checks/framework.py` — `CrossCheck` protocol (`.name`, `.required_statements`, `.applies_to(run_config)`, `.run(workbook_paths, tolerance) -> CrossCheckResult`) and `run_all(checks, paths, config, tolerance)` function. Runner handles: missing-statement → pending, missing workbook → failed, variant gating → not_applicable, exception in `.run()` → failed with message.
  - [x] 🟩 **REFACTOR**: `CrossCheckResult` carries `name`, `status`, `expected`, `actual`, `diff`, `tolerance`, `message`. `DEFAULT_TOLERANCE_RM = 1.0` in framework.
  - **Verify:** ✅ `pytest tests/test_cross_checks.py -v` — 9 passed.
  - **Peer-review fix (Finding 4):** Added workbook-path guard before `applies_to()` check; wrapped `.run()` in try/except so one broken check can't crash the entire validation pass.

- [x] 🟩 **Step 5.2: Implement 5 P0 checks** — one file per check.
  - [x] 🟩 **RED**: `tests/test_cross_checks_impl.py` — 20 tests: 12 unit tests with synthetic fixtures (2 per check: match + mismatch, plus SOFP tolerance + OrderOfLiquidity variant, plus 2 tolerance tests) + 6 smoke tests against real MBRS templates.
  - [x] 🟩 **GREEN**: implemented each check as a separate file under `cross_checks/`:
    1. `cross_checks/sofp_balance.py` — Total assets = Total equity + liabilities (CY). Handles CuNonCu and OrderOfLiquidity.
    2. `cross_checks/sopl_to_socie_profit.py` — SOPL profit = SOCIE profit row. NCI-aware: uses Total column (X=24) when NCI present, Retained earnings (C=3) otherwise.
    3. `cross_checks/soci_to_socie_tci.py` — SOCI TCI = SOCIE TCI row. NCI-aware (same logic as #2).
    4. `cross_checks/socie_to_sofp_equity.py` — SOCIE closing equity (col X) = SOFP total equity.
    5. `cross_checks/socf_to_sofp_cash.py` — SOCF closing cash = SOFP cash.
  - [x] 🟩 **REFACTOR**: `cross_checks/util.py` extracted with `open_workbook()`, `find_sheet()` (multi-candidate, case-insensitive), `find_value_by_label()` (two-pass: exact match then substring, formula evaluation via verifier's `_resolve_cell_value`, any column).
  - **Verify:** ✅ `pytest tests/test_cross_checks_impl.py -v` — 20 passed. Each check detects known mismatches + labels resolve on real templates.
  - **Peer-review fixes:** (Finding 1) Formula cells now evaluated via `_resolve_cell_value`; (Finding 2) SOCI sheet name `SOCI-BeforeOfTax` added; (Finding 3) SOCIE label corrected to `equity at end of period`; (Finding 5) 6 smoke tests against real MBRS templates.
  - **Post-review refinement:** SOPL→SOCIE and SOCI→SOCIE checks updated to detect NCI column and use Total column (X=24) for consolidated entities.

- [x] 🟩 **Step 5.3: Variant-aware + missing-statement-aware check selection** — built into framework from Step 5.1.
  - [x] 🟩 **RED + GREEN**: tests in `test_cross_checks.py` cover: pending when statement not run, not_applicable on variant mismatch, variant match runs check, SOCIE missing triggers multiple pending.
  - [x] 🟩 **GREEN**: `run_all()` checks `required_statements` against `statements_to_run` → pending; checks `workbook_paths` availability → failed; calls `applies_to()` → not_applicable; then `.run()` with exception guard.
  - **Verify:** ✅ All 9 framework tests pass including selection logic + robustness guards.

- [x] 🟩 **Step 5.4: Absolute tolerance config** — RM tolerance plumbed through framework.
  - [x] 🟩 **RED + GREEN**: `tests/test_cross_checks_impl.py::TestToleranceApplied` — 2 tests: RM 0.50 off passes with tol=1 fails with tol=0.25; tolerance plumbed through `run_all()`.
  - [x] 🟩 **GREEN**: `DEFAULT_TOLERANCE_RM = 1.0` in `cross_checks/framework.py`; all checks receive tolerance as parameter; `run_all()` accepts optional `tolerance` kwarg.
  - **Verify:** ✅ Toggling tolerance changes pass/fail outcomes.

- [x] 🟩 **Step 5.5: Persist cross-check results to DB** — leverages existing `save_cross_check()` / `fetch_cross_checks()` from Phase 2.
  - [x] 🟩 **RED + GREEN**: `tests/test_cross_checks_persistence.py::test_results_saved_to_db` — saves 5 results (passed, passed, pending, failed, not_applicable), verifies all 5 rows returned with correct statuses and numeric fields.
  - [x] 🟩 **GREEN**: `db/repository.py` already had `save_cross_check()` and `fetch_cross_checks()` from Phase 2 schema. Test confirms the persistence round-trip works with `CrossCheckResult` data.
  - **Verify:** ✅ `pytest tests/test_cross_checks_persistence.py -v` — 1 passed. All 5 rows present with correct statuses.
  - **Note:** Wiring into the coordinator (calling `save_cross_check` after `run_all`) is deferred to Phase 7 (server integration), since the coordinator currently doesn't hold a DB connection.

**Phase 5 final test count:** 30 passed (9 framework + 20 impl/smoke + 1 persistence). Full suite: 244 passed, 1 deselected.

---

### Phase 6: Workbook Merger

Goal: combine per-statement workbooks into one merged output.

- [x] 🟩 **Step 6.1: Merger implementation** — copies sheets from each per-statement workbook into a single file.
  - [x] 🟩 **RED**: write `tests/test_merger.py::test_merged_workbook_has_all_sheets` — given 5 per-statement workbook fixtures, merged file contains all expected sheets with values intact
  - [x] 🟩 **RED**: write `test_merger_preserves_formulas` — formulas in per-statement sheets remain formulas in merged output (not evaluated values)
  - [x] 🟩 **GREEN**: create `workbook_merger.py::merge(paths: dict, output_path: str)` using openpyxl — copy each sheet (cells, styles, formulas) to a new workbook
  - [x] 🟩 **REFACTOR**: cross-sheet formula references within same statement (e.g. SOFP-CuNonCu → SOFP-Sub-CuNonCu) work automatically since both sheets come from the same source workbook. No cross-*statement* formulas exist in MBRS templates — no remapping needed.
  - **Verify:** open merged output in Excel, all sheets render correctly, balance-sheet formulas evaluate.

**Phase 6 final test count:** 8 passed (5 sheet correctness + 3 formula/style preservation). Full suite: 252 passed, 1 deselected.

---

### Phase 7: Backend Integration (Wire Scout + Coordinator + Merger + DB into server.py)

- [x] 🟩 **Step 7.1: New `POST /api/scout/{session_id}` endpoint** — runs scout and returns infopack.
  - [x] 🟩 **RED**: write `tests/test_server_scout.py::test_scout_endpoint_returns_infopack` using FastAPI TestClient + mocked LLM (3 tests: success, 404, error handling)
  - [x] 🟩 **GREEN**: add the endpoint in `server.py`; streams scout status over SSE; returns `scout_complete` event with infopack JSON on completion
  - **Verify:** POST PDF, call scout endpoint, receive infopack JSON.

- [x] 🟩 **Step 7.2: New `POST /api/run/{session_id}` accepts `RunConfigRequest`** — user-supplied variants + model overrides + selected statements + optional infopack.
  - [x] 🟩 **RED**: write `tests/test_server_run_config.py::test_run_config_schema` asserting the request body is validated (infopack is optional) (3 tests)
  - [x] 🟩 **RED**: write `test_run_config_rejects_missing_variants_when_no_infopack` — coordinator falls back to first registered variant (by design)
  - [x] 🟩 **GREEN**: added `POST /api/run/{session_id}` with `RunConfigRequest` body (`statements`, `variants`, `models`, `infopack` nullable, `use_scout`). Launches coordinator via `run_multi_agent_stream()`.
  - [x] 🟩 **REFACTOR**: the old `GET /api/run/{session_id}` stays as a compat layer (defaults to SOFP-only using the old pipeline) until the frontend migrates
  - **Verify:** send RunConfig for all 5 statements → all 5 sub-agents start; send for subset → only those start.

- [x] 🟩 **Step 7.3: Multi-agent SSE multiplexing** — one SSE stream carries events from all concurrent agents, tagged by `agent_id`.
  - [x] 🟩 **RED**: write `tests/test_sse_multiplex.py::test_events_tagged_by_agent` asserting each SSE event has `agent_id` and `agent_role` fields (2 tests)
  - [x] 🟩 **GREEN**: `run_multi_agent_stream()` tags each per-agent completion event with `agent_id` (e.g. `sofp_0`) and `agent_role` (e.g. `SOFP`). Coordinator runs agents concurrently via `asyncio.gather`; events emitted post-completion.
  - [x] 🟩 **REFACTOR**: `agent_role` included alongside `agent_id` for UI grouping. `run_complete` event emitted as final summary.
  - **Verify:** run all 5 agents → SSE client receives events with correct `agent_id` tags.
  - **Note:** True per-tool-call streaming from concurrent agents (interleaved during execution) is deferred — current implementation emits per-agent completion events after all agents finish. Adequate for Phase 1; streaming granularity can be added when the frontend tab UI (Phase 10) needs it.

- [x] 🟩 **Step 7.4: Workbook merge + persist results** — at run completion, merge per-statement workbooks and save extracted fields to DB.
  - [x] 🟩 **RED**: write `tests/test_post_run.py::test_merged_workbook_and_fields_persisted` (1 test covering merge + field persistence + cross-check persistence)
  - [x] 🟩 **GREEN**: after coordinator finishes: (1) merge workbooks via `workbook_merger.merge()`, (2) run all 5 cross-checks via `cross_checks.framework.run_all()`, (3) persist run/agent/field/cross-check rows to audit DB, (4) emit `run_complete` SSE event with cross-check results
  - **Verify:** after a full run, `output/{run_id}/filled.xlsx` contains all statements; DB has field rows and cross-check rows.

**Phase 7 final test count:** 11 passed (3 scout endpoint + 3 run config + 2 SSE multiplex + 1 post-run + 2 integration). Full suite after peer review fixes: 263 passed, 1 deselected.

**New files created in Phase 6–7:**
| File | Purpose |
|------|---------|
| `workbook_merger.py` | Merges per-statement workbooks into one file |
| `tests/test_merger.py` | 8 merger tests (sheets, values, formulas, styles) |
| `tests/test_server_scout.py` | 3 scout endpoint tests |
| `tests/test_server_run_config.py` | 3 RunConfig endpoint tests |
| `tests/test_sse_multiplex.py` | 2 SSE multiplexing tests |
| `tests/test_post_run.py` | 1 merge+persist integration test |
| `tests/test_multi_agent_integration.py` | 2 full orchestration integration tests (added in peer review) |

---

### Phase 8: Settings Page (backend) 🟩

- [x] 🟩 **Step 8.1: Extended settings schema** — model list + per-agent-role defaults + scout-enabled default.
  - [x] 🟩 **RED**: wrote `tests/test_settings.py` — 10 tests covering GET (available_models, default_models, scout_enabled_default, tolerance_rm, backward compat), POST (per-agent model, scout toggle, tolerance, legacy model), and config file reload.
  - [x] 🟩 **GREEN**: extended `GET/POST /api/settings` with `available_models`, `default_models`, `tolerance_rm`, `scout_enabled_default`. Extended settings stored as `XBRL_*` keys in `.env`.
  - [x] 🟩 **REFACTOR**: `_load_available_models()` reads `config/models.json` on every call — edits picked up without redeploy. `_load_extended_settings()` merges per-agent defaults with global model fallback.
  - **Verify:** ✅ `pytest tests/test_settings.py -v` — 10 passed. GET returns model list with 7 entries; POST persists per-agent overrides; config file changes reflected immediately.

- [x] 🟩 **Step 8.2: Pinned model list seed** — starter JSON file.
  - [x] 🟩 **GREEN**: created `config/models.json` with 7 entries (Gemini 3 Flash Preview, Gemini 3 Pro, Claude Sonnet 4.6, Claude Opus 4.6, Claude Haiku 4.5, GPT-5.4, GPT-5.4 mini). Each entry: `id`, `display_name`, `provider`, `supports_vision`, `notes`.
  - **Verify:** ✅ Settings endpoint returns all 7 models; frontend dropdowns populated.

---

### Phase 9: Frontend — Pre-Run UI 🟩

Goal: upload → scout → variant confirmation → model selection → Run.

- [x] 🟩 **Step 9.1: Variant selector component** — 5 dropdowns (one per statement) + confidence indicators.
  - [x] 🟩 **RED**: wrote `web/src/__tests__/VariantSelector.test.tsx` — 6 tests: renders per-statement dropdowns, only enabled statements, onChange fires, confidence indicator, no indicator when null, selected value reflects state.
  - [x] 🟩 **GREEN**: created `web/src/components/VariantSelector.tsx` with inline styles, green/amber/red confidence dots from scout.
  - **Verify:** ✅ `npx vitest run VariantSelector` — 6 passed.

- [x] 🟩 **Step 9.2: Scout toggle + auto-detect integration** — master on/off switch for the scout, plus Auto-detect button when enabled.
  - [x] 🟩 **RED**: wrote `web/src/__tests__/ScoutToggle.test.tsx` — 8 tests: toggle renders, default state, toggling calls onToggle, Auto-detect visible/hidden based on enabled, disabled when canAutoDetect=false, spinner during detection, click calls onAutoDetect.
  - [x] 🟩 **GREEN**: created `web/src/components/ScoutToggle.tsx` with visual toggle switch + Auto-detect button. When ON, shows Auto-detect; when OFF, hides it.
  - [x] 🟩 **REFACTOR**: confidence indicators (green/amber/red dots) in VariantSelector render only when scout has returned results.
  - **Verify:** ✅ `npx vitest run ScoutToggle` — 8 passed. Scout integration test deferred (SSE mocking complex — covered at PreRunPanel level).

- [x] 🟩 **Step 9.3: Per-statement run selection + per-agent model override** — checkboxes + nested model dropdowns.
  - [x] 🟩 **RED**: wrote `web/src/__tests__/StatementRunConfig.test.tsx` — 6 tests: renders 5 rows, checkbox state, toggle calls onToggleStatement, model dropdown shows models, model change calls onModelChange, disabled statement greys out dropdown.
  - [x] 🟩 **GREEN**: created `web/src/components/StatementRunConfig.tsx` — table with checkbox + model dropdown per statement.
  - **Verify:** ✅ `npx vitest run StatementRunConfig` — 6 passed.

- [x] 🟩 **Step 9.4: Pre-run page composition** — assemble VariantSelector + ScoutToggle + StatementRunConfig + Run button.
  - [x] 🟩 **GREEN**: created `web/src/components/PreRunPanel.tsx` composing all sub-components. Loads settings on mount, manages variant/model/enabled state, calls `/api/run/{session_id}` with full RunConfig via new `createMultiAgentSSE()` (POST-based SSE).
  - [x] 🟩 **GREEN**: integrated into `App.tsx` — PreRunPanel shows after upload, hidden during run. Header updated from "SOFP Agent" to "XBRL Agent". Added `getExtendedSettings` API function and `RunConfigPayload` type.
  - [x] 🟩 **GREEN**: created `createMultiAgentSSE()` in `sse.ts` — fetch-based SSE reader for POST endpoints (native EventSource only supports GET).
  - **Verify:** ✅ `npx vitest run PreRunPanel` — 5 passed. Full suite: 108 passed (17 test files, 0 failures).

---

### Phase 10: Frontend — Tab-Based Multi-Agent Progress 🟩 DONE

- [x] 🟩 **Step 10.1: Agent tab bar** — top tabs [Scout] [SOFP] [SOPL] [SOCI] [SOCF] [SOCIE] [Validator] with status badges.
  - [x] 🟩 **RED**: write `web/src/components/__tests__/AgentTabs.test.tsx` (6 tests)
  - [x] 🟩 **GREEN**: create `web/src/components/AgentTabs.tsx` — receives per-agent state map, renders tab bar
  - [x] 🟩 **REFACTOR**: skeleton tabs for statements not selected in RunConfig

- [x] 🟩 **Step 10.2: Per-agent event routing** — SSE events with `agent_id` land in the correct tab.
  - [x] 🟩 **RED**: added 6 per-agent routing tests to `web/src/__tests__/appReducer.test.ts`
  - [x] 🟩 **GREEN**: refactored `appReducer` state to hold `agents: Record<agent_id, AgentState>`, auto-creates agent slots from SSE events
  - [x] 🟩 **REFACTOR**: extracted `agentReducer(agentState, event)` — per-agent logic matches existing single-agent reducer exactly

- [x] 🟩 **Step 10.3: Validator tab** — renders cross-check results with pass/fail/pending/not-applicable per check.
  - [x] 🟩 **RED**: write `web/src/components/__tests__/ValidatorTab.test.tsx` (8 tests)
  - [x] 🟩 **GREEN**: create `ValidatorTab.tsx` — table with Check Name | Status | Expected | Actual | Diff | Message
  - [x] 🟩 **GREEN**: pending rows show Run/Skip buttons; `not_applicable` rows styled muted-grey

---

### Phase 11: End-to-End Integration Tests & Cleanup 🟩 DONE

- [x] 🟩 **Step 11.1: Full-pipeline E2E test (mocked LLM)** — 5 sub-agents → merger → cross-checks → DB.
  - [x] 🟩 `tests/test_e2e.py::test_full_extraction_mocked` — asserts 5 sheets in merged workbook + 5 cross-check rows in DB. Runs in <3s.

- [x] 🟩 **Step 11.2: Full-pipeline E2E test (real LLM)** — marked as `@pytest.mark.live`.
  - [x] 🟩 `tests/test_e2e.py::test_full_extraction_live` — runs SOFP against real Gemini key using FINCO PDF.

- [x] 🟩 **Step 11.3: Remove legacy SOFP-only code path**
  - [x] 🟩 `run.py` CLI now accepts `--statements` flag (default all 5); calls coordinator directly
  - [x] 🟩 Deleted `create_sofp_agent` from `agent.py`
  - [x] 🟩 Deleted `iter_agent_events` and `_find_template` from `server.py`
  - [x] 🟩 Removed legacy `GET /api/run/{session_id}` endpoint
  - [x] 🟩 Updated all affected tests (270 backend + 132 frontend tests pass)

- [x] 🟩 **Step 11.4: Update CLAUDE.md** — new architecture, files-in-sync table, testing commands.

- [ ] 🟨 **Step 11.5: Windows smoke test** — manual verification required on Windows machine.

---

## Deferred / Undone Items (as of 2026-04-06)

Items discovered during implementation or peer review that are tracked but not yet addressed:

### ~~Deferred to Phase 7 (server integration)~~ — DONE
- ~~**Wire cross-checks into coordinator:**~~ ✅ Done in Phase 7.4 — `run_all()` called after coordinator finishes; results persisted via `save_cross_check()` in `run_multi_agent_stream()`.
- ~~**Cross-check results in SSE stream:**~~ ✅ Done in Phase 7.4 — cross-check outcomes emitted in the `run_complete` SSE event.

### ~~Deferred to Phase 10 (frontend tabs)~~ — DONE
- ~~**True interleaved SSE multiplexing:**~~ Per-agent state routing implemented in Phase 10.2 — frontend tab UI now shows per-agent thinking/tool timeline. Backend still emits per-agent events post-completion (not interleaved during execution); real-time interleaving via `asyncio.Queue` remains a future optimization.

### ~~Deferred to Phase 11 (cleanup)~~ — DONE
- ~~**Legacy `create_sofp_agent` removal:**~~ ✅ Deleted in Phase 11.3.
- **`prompts/sofp.md` still used for CuNonCu:** the generic SOFP prompt references CuNonCu-specific sheets. Consider renaming for clarity, or keeping as default fallback.
- ~~**Legacy GET `/api/run/{session_id}` removal:**~~ ✅ Deleted in Phase 11.3. Frontend uses `POST /api/run/{session_id}` exclusively.

### Known template quirks to document
- `SOCI-BeforeOfTax` sheet name (not `BeforeTax`) — filed in Learnings section above.
- `*Equity at end of period` (not `Balance at end of period`) for SOCIE closing row.
- SOCF `*Cash and cash equivalents at end of period` at row 132 is data-entry; row 137 is the formula reconciliation version.
- SOPL bottom-line is `*Profit (loss)` (Function, with asterisk) or `Profit (loss)` (Nature, no asterisk).

### Pre-implementation checklist (still open)
- [ ] OpenAI GPT-5.4 / 5.4 mini exact model-ID strings — needed for Phase 8 model list.
- [ ] 2-3 representative Malaysian annual-report PDFs for E2E testing (need variety: CuNonCu vs OrderOfLiquidity, different TOC offsets).
- [ ] Verify all 9 templates have stable sheet names/row structures (partially done via smoke tests; full audit pending).

---

## Rollback Plan

If something goes badly wrong:

- **Phase 0–2 (foundation + DB):** revert commits with `git revert`; existing SOFP pipeline untouched.
- **Phase 3 (scout):** scout is additive — disable the Auto-detect button in the frontend to bypass it; user manually selects variants and page ranges.
- **Phase 4–5 (sub-agents + cross-checks):** keep `create_sofp_agent` alias until Phase 11.3; frontend can fall back to old single-agent `GET /api/run` endpoint.
- **Phase 6–7 (merger + backend wiring):** per-statement workbooks still exist individually in `output/{run_id}/` even if merge fails — users can open them separately.
- **Phase 8–10 (frontend):** frontend changes are isolated in new components; revert the App.tsx reducer changes to restore single-agent view.
- **Database corruption:** SQLite file is per-run metadata only — deleting `output/xbrl_agent.db` and re-initialising loses only the audit trail, not extracted results (still in `filled.xlsx` files).
- **If the scout routinely mis-calibrates on production PDFs:** expose a manual "correct the page numbers" UI as a Phase 11.X escape hatch; user overrides the infopack before coordinator runs.

## State/Data to Check on Rollback

- `output/xbrl_agent.db` (SQLite file — safe to delete and re-init)
- `output/{run_id}/` directories (per-statement workbooks + merged workbook + PDFs + images)
- `.env` (unchanged by this work; should not need rollback)
- `config/models.json` (new file; safe to delete if rolling back Phase 8)
