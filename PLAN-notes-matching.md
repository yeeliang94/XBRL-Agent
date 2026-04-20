# PLAN — Notes Matching Feature

**Branch:** `worktree-notes-matching`
**Worktree:** `.claude/worktrees/notes-matching/`
**Status:** Drafted, rebased onto `origin/main` @ `4df5ae9`, awaiting human review

**Rebase notes (2026-04-16):** branch rebased clean onto the peer-review fix
commit. Two small downstream impacts on the plan:

- New notes agent factory must explicitly set `model_settings=ModelSettings(temperature=1.0)`
  in the same pattern that `create_extraction_agent` and `create_scout_agent` now use
  (CLAUDE.md gotcha #5).
- The DB recorder now caps SSE payloads at 16 KB per event. Cell content is written
  to xlsx directly through `notes/writer.py` and is unaffected, but the History page
  replay will show truncated previews for any notes write event whose JSON payload
  exceeds 16 KB. Acceptable — the canonical record lives in the merged xlsx.

---

## 1. Goal

Extend the XBRL extraction pipeline to fill the five MBRS notes templates (10–14) that
are currently shipped empty. Use a **PDF-first** approach: scout discovers what notes
actually exist in the PDF, then per-template agents read those notes and copy/format
their content into the matching template rows. No deterministic matching, no OCR, no
synonym dictionaries — pure LLM judgement at every matching decision.

The 5 notes templates:

| File | Sheet | Rows | Shape |
|---|---|---|---|
| `10-Notes-CorporateInfo.xlsx` | `Notes-CI` | 5 fields | Short text fields (domicile, restatement reason, …) |
| `11-Notes-AccountingPolicies.xlsx` | `Notes-SummaryofAccPol` | 53 fields | One paragraph per accounting-policy area |
| `12-Notes-ListOfNotes.xlsx` | `Notes-Listofnotes` | 138 fields | One text block per disclosure topic — **the big one** |
| `13-Notes-IssuedCapital.xlsx` | `Notes-Issuedcapital` | 27 fields | Structured numeric movement table |
| `14-Notes-RelatedParty.xlsx` | `Notes-RelatedPartytran` | 33 fields | Structured numeric transactions table |

---

## 2. Confirmed design decisions (from spec discussion)

1. All 5 notes templates implemented this phase.
2. **PDF-first** discovery; scout produces an inventory `[(note_num, title, page_range), …]`.
3. **Notes have a separate agent backend** from the face-statement agents — different
   prompts, different write tool, different coordinator entry point. Generic helpers
   (`view_pdf_pages`, `read_template`) are shared.
4. **No deterministic matching, no OCR, no synonym dictionaries.** Scout's text-parsing
   helpers are deterministic only for navigation (locating the notes section, splitting
   pages by note headers). Matching itself is pure LLM.
5. **Cell format:** plain text, paragraphs separated by `\n` (renders as Alt+Enter in
   Excel), tables as ASCII-aligned text. Adjustable later when a real mTool sample is
   available.
6. **Group vs Company:** notes content always describes the entity the FS is about
   (group filing → group narrative). Write to **Group columns only** (B/C) on Group
   filings; leave Company columns (D/E) empty. For Notes 13/14 (numeric), separate
   Group vs Company values are extracted and written to both column pairs.
7. **No verifier for prose notes.** Quality comes from (a) mandatory evidence/source
   citation per cell, (b) post-run reconciliation log of unmatched/uncovered notes.
8. **UI:** five new checkboxes in the existing pre-run statement-selection panel,
   one per notes sheet. Independently selectable from the 5 face-statement checkboxes.
9. **Sub-agent fan-out for Sheet 12 only.** Sheet 12 splits its inventory across **5
   parallel sub-agents** (~6 notes each). Sheets 10/11/13/14 each run as a single
   agent.
10. **Retry budget:** max 1 retry per failed sub-agent and per failed sheet.
11. **Evidence column** mandatory per cell — same scheme as face-statement agents
    (col D for Company, col F for Group).

### Edge cases explicitly in scope

- **Unmatched notes** → written to row 112 ("Disclosure of other notes to accounts")
  with concatenation if multiple, side-logged in `notes12_unmatched.json`.
- **One PDF note → multiple template rows** → handled in agent prompt; sub-agent
  emits multiple payloads from one input.
- **Sub-agent / sheet failure** → retry once, then ship partial with failure log.
- **Multi-page note continuation** → scout uses note headers as boundaries; agent
  prompt allows extending the read window when content runs off-page.

### Out of scope this phase

- Collision resolution agent / multi-pass reconciliation.
- Embedded chart/figure handling beyond a one-line description.
- Cell-character-limit (32,767) handling beyond plain truncation with a footer.
- Cross-language QA (Malay/English).
- mTool round-trip verification (deferred until a sample filed mTool workbook is on hand).
- Notes-aware cross-checks against face statements.

---

## 3. Architecture

### 3.1 Component overview

```
                       ┌─────────────────────────┐
                       │   server.py / run.py    │
                       │   (entry points)        │
                       └────────────┬────────────┘
                                    │ RunConfig (extended with notes_to_run)
                                    ▼
                       ┌─────────────────────────┐
                       │  scout/agent.py (ext)   │ ──► Infopack
                       │  + notes_inventory      │     + notes_inventory: list[NoteInventoryEntry]
                       └────────────┬────────────┘
                                    │
                                    ▼
                       ┌─────────────────────────┐
                       │ orchestrator (ext)      │
                       │ existing face flow +    │
                       │ NEW notes coordinator   │
                       └────┬────────────────┬───┘
                            │                │
              face-statement│                │notes
                  flow      │                │flow
                  (existing)│                ▼
                            │   ┌─────────────────────────────┐
                            │   │ notes/coordinator.py (NEW)  │
                            │   │ fans out per template       │
                            │   └────┬────────┬────────┬──────┘
                            │        │        │        │
                            │   sheet 10 ─ sheet 11 ─ sheet 13/14    sheet 12
                            │   single     single     single         sub-coord
                            │   agent      agent      agents         + 5 sub-agents
                            │        │        │        │             │
                            │        ▼        ▼        ▼             ▼
                            │   ┌──────────────────────────────────────┐
                            │   │ notes/writer.py (NEW)                │
                            │   │ payload → xlsx with evidence + char  │
                            │   │ guard + Group/Company column rules   │
                            │   └────────────────┬─────────────────────┘
                            ▼                    ▼
                      per-statement         per-notes-template
                      xlsx files            xlsx files
                            │                    │
                            └──────────┬─────────┘
                                       ▼
                       ┌─────────────────────────┐
                       │ workbook_merger.py (ext)│
                       └────────────┬────────────┘
                                    ▼
                              filled.xlsx
```

### 3.2 New files

| Path | Purpose |
|---|---|
| `notes_types.py` | Enum `NotesTemplateType` (CORP_INFO / ACC_POLICIES / LIST_OF_NOTES / ISSUED_CAPITAL / RELATED_PARTY) and registry analogous to `statement_types.py` (sheet name, template filename, agent class hint). |
| `notes/__init__.py` | Package marker. |
| `notes/payload.py` | `NotesPayload` dataclass (chosen_row_label, content, evidence, source_pages, sub_agent_id). |
| `notes/coordinator.py` | Fans out one task per requested notes template. Mirrors the `coordinator.py` pattern (asyncio.gather, per-template result, retry budget). |
| `notes/agent.py` | Single-agent factory analogous to `extraction/agent.py` but with a different system prompt template, different tools (text-write instead of numeric fill), no verifier wiring. |
| `notes/listofnotes_subcoordinator.py` | Sheet-12-specific: takes scout's inventory, splits into 5 batches, fans out 5 sub-agents in parallel, collects payloads, hands to writer. Concatenates unmatched notes into row 112. |
| `notes/writer.py` | Accepts `list[NotesPayload]` + template path + filing level → writes the xlsx. Handles char-limit guard (truncate at ~30K with footer), evidence-column placement (D for company, F for group), Group/Company column rules per Section 2 #6. |
| `prompts/notes_corporate_info.md` | System prompt for sheet 10 agent. |
| `prompts/notes_accounting_policies.md` | System prompt for sheet 11 agent. |
| `prompts/notes_listofnotes.md` | System prompt for sheet 12 sub-agent (used by all 5 sub-agents in parallel; differ only in their input batch). |
| `prompts/notes_issued_capital.md` | System prompt for sheet 13 agent. |
| `prompts/notes_related_party.md` | System prompt for sheet 14 agent. |
| `prompts/_notes_base.md` | Shared persona + Group/Company column rules + cell-format guidance + evidence requirement + multi-page continuation rule. |
| `tests/test_notes_*.py` | Unit + integration tests per slice (see tasks). |

### 3.3 Files extended (not rewritten)

| Path | Change |
|---|---|
| `scout/notes_discoverer.py` | Add `build_notes_inventory(pdf_path, notes_start_page) → list[NoteInventoryEntry]`. Uses note-header regex on text-PDF; falls back to LLM vision tool calls in the scout agent loop for scanned PDFs. |
| `scout/infopack.py` | Add `notes_inventory: list[NoteInventoryEntry]` field. Extend `to_json` / `from_json`. |
| `scout/agent.py` | Add tool `discover_notes_inventory` that the scout LLM can call after locating the notes start page. Updates `deps.infopack.notes_inventory`. |
| `coordinator.py` | No change to face-statement path. Extract a thin orchestrator above it (or have `server.py`/`run.py` call both `coordinator.run_extraction` and `notes.coordinator.run_notes` in parallel). |
| `workbook_merger.py` | Iterate notes workbooks alongside face-statement workbooks. Stable sort: face statements first, then notes 10–14. |
| `server.py` | `RunConfigRequest.notes_to_run: list[str]`. Plumb through to `RunConfig`. New SSE `agent_id` namespace `notes:<template>`. New PHASE_MAP entries for notes tools. |
| `run.py` | `--notes <list>` CLI flag. |
| `db/schema.py` | No schema change required if notes runs are recorded as additional rows in `agents` table with a `kind` column (or a new column `agent_kind` defaulting to `face`). Decision: piggyback on existing `agents` table with agent_id naming convention `notes_<template>`. No schema migration needed. |
| `db/repository.py` | Update agent query helpers if necessary to include notes agents in run-detail responses. |
| `web/src/lib/types.ts` | `NotesTemplateType` enum + `RunConfigPayload.notes_to_run`. |
| `web/src/components/PreRunPanel.tsx` | Render 5 new checkboxes labelled by template, default OFF. |
| `web/src/components/AgentTabs.tsx` | Render notes tabs alongside face-statement tabs. |
| `web/src/lib/buildToolTimeline.ts` | Already handles arbitrary agent_ids — no change expected. |
| `web/src/components/RunDetailView.tsx` | Render notes-agent results; surface unmatched/failure logs. |

### 3.4 Dependency graph

```
[A] notes_types.py + prompts/_notes_base.md
        │
        ├─► [B] notes/payload.py
        │           │
        │           └─► [C] notes/writer.py (depends on A, B)
        │
        ├─► [D] scout inventory extension (independent of B/C)
        │
        ├─► [E] notes/agent.py base factory (depends on A, B, C, D for hints)
        │           │
        │           ├─► [F] sheet 10 (CorporateInfo) prompt + integration
        │           ├─► [G] sheet 11 (AccountingPolicies) prompt + integration
        │           ├─► [H] sheet 13 (IssuedCapital) prompt + integration
        │           ├─► [I] sheet 14 (RelatedParty) prompt + integration
        │           └─► [J] sheet 12 sub-coordinator + sub-agent prompt
        │                       (depends on E plus its own batch-split logic)
        │
        ├─► [K] notes/coordinator.py (depends on E, F, G, H, I, J)
        │
        ├─► [L] server.py / run.py wiring (depends on K)
        │           │
        │           ├─► [M] workbook_merger integration (depends on K, L)
        │           ├─► [N] DB / history wiring (depends on L)
        │           └─► [O] UI checkboxes + tabs (depends on L's HTTP API)
        │
        └─► [P] Edge-case hardening + tests (final pass over everything)
```

### 3.5 Vertical slicing rationale

Each task ships one *complete vertical path* — config flag → agent → write → merge →
visible in output xlsx. This means even Phase A delivers a usable feature
(`--notes corporate_info` returns a filled Notes 10 sheet in `filled.xlsx`).
Subsequent phases extend coverage by adding more templates, not by adding more
horizontal layers.

---

## 4. Phases & checkpoints

### Phase A — Foundation + Notes 10 (smallest sheet, ships end-to-end)

Builds the entire spine: notes_types, payload, writer, agent factory, scout inventory
extension, single-agent path. Notes 10 is intentionally the first slice because it has
only 5 fields and minimal matching complexity — proves the wiring without LLM noise.

**Checkpoint A:**
- `python3 run.py data/FINCO.pdf --notes corporate_info` produces a `filled.xlsx`
  containing a populated `Notes-CI` sheet with evidence citations.
- Server endpoint accepts `notes_to_run=["CORP_INFO"]` and returns SSE events under
  `agent_id=notes_corporate_info`.
- All new tests in `tests/test_notes_writer.py`, `tests/test_notes_payload.py`,
  `tests/test_scout_notes_inventory.py` pass.
- **Human review pause** — confirm cell content rendering looks copy-paste OK before
  proceeding.

### Phase B — Single-agent notes sheets (11, 13, 14)

Adds prompts and integration for the three remaining single-agent sheets. No new
infrastructure — just plugging into the agent factory built in Phase A. Sheet 13/14
deliberately reuse the structured-numeric mindset (label-based row matching,
numeric values), but through the new notes write path (so evidence column uses notes
conventions). Sheet 11 exercises the "read once, write many sub-policies" pattern.

**Checkpoint B:**
- All four single-agent notes sheets fill correctly on the FINCO sample PDF.
- `--notes accounting_policies issued_capital related_party` works in CLI.
- `tests/test_notes_e2e_single_agent.py` passes against a mocked PDF.
- **Human review pause** — sample fills validated visually before sub-agent fan-out.

### Phase C — Sheet 12 sub-agent fan-out

Builds the `listofnotes_subcoordinator`: takes scout's inventory, splits into 5 batches,
fans out 5 parallel agents using the `notes_listofnotes.md` prompt, collects payloads,
runs concatenation logic for unmatched-notes-into-row-112, hands to writer.

**Checkpoint C:**
- `--notes list_of_notes` fills Notes-12 sheet on FINCO sample with realistic coverage
  (typical 20–40 of 138 rows populated).
- Unmatched log file `notes12_unmatched.json` written when applicable.
- Failure of one sub-agent produces partial coverage, not a whole-sheet failure.
- `tests/test_notes12_subcoordinator.py` passes.
- **Human review pause** — inspect coverage and confirm matching quality before UI.

### Phase D — UI + history integration

Frontend pre-run panel checkboxes, tabs in live timeline, history-page rendering of
notes agents. DB persistence via existing `agents` table.

**Checkpoint D:**
- Web UI shows 5 notes checkboxes; selecting them triggers their agents on submit.
- Live run page shows notes-sheet tabs alongside face-statement tabs.
- Past runs in History page replay notes agents correctly.
- `web/src/__tests__/notes_pre_run_panel.test.tsx` and the existing test suite pass.

### Phase E — Edge-case hardening + final tests

Retry-budget enforcement, multi-page continuation prompt tuning, char-limit guard,
unmatched-sink concatenation, partial-coverage badge rendering, evidence-mandatory
contract enforcement.

**Checkpoint E:**
- Failure-injection tests for sub-agent and sheet retry verify max-1-retry contract.
- Char-limit-guard tests confirm truncation and footer behaviour.
- `tests/test_notes_e2e_full_pipeline.py` passes against FINCO sample for all 5 sheets.
- CLAUDE.md updated with notes-feature reference + "Files That Must Stay in Sync" entries.
- **Human review pause** — final sign-off before merge to main.

---

## 5. Tasks

Each task lists: **deliverable**, **acceptance criteria**, **verification steps**.
Tasks are designed to land as individual commits.

### Phase A — Foundation + Notes 10

#### A.1 — Notes registry + payload + writer scaffolding

**Deliverable:**
- `notes_types.py` with `NotesTemplateType` enum (CORP_INFO, ACC_POLICIES, LIST_OF_NOTES,
  ISSUED_CAPITAL, RELATED_PARTY) and `notes_template_path(type, level)` function.
- `notes/payload.py` with `NotesPayload` dataclass.
- `notes/writer.py` with `write_notes_workbook(template_path, payloads, output_path,
  filing_level)` — handles char-limit truncation, evidence column placement,
  Group-only narrative writes per Section 2 #6.

**Acceptance criteria:**
- Registry resolves to the correct file under `XBRL-template-MFRS/{Company,Group}/`.
- Writer never overwrites formula cells (none expected in notes templates, but check).
- Writer truncates content >30K chars with `\n\n[truncated — see PDF pages X-Y]` footer.
- Writer writes evidence to col D (Company) or col F (Group).
- Group-filing prose-only payloads write to col B/C only; numeric payloads (sheets 13/14)
  may write to all four numeric columns.

**Verification:**
- `pytest tests/test_notes_types.py tests/test_notes_payload.py tests/test_notes_writer.py -v`
- Manual: load output xlsx in Excel; cells render with line breaks correctly.

#### A.2 — Scout notes-inventory extension

**Deliverable:**
- `scout/notes_discoverer.py`: new `build_notes_inventory(pdf_path, notes_start_page)`
  → `list[NoteInventoryEntry(note_num, title, page_range)]`.
- `scout/infopack.py`: `notes_inventory: list[NoteInventoryEntry]` field with
  to_json/from_json round-trip.
- `scout/agent.py`: new tool `discover_notes_inventory` callable by the scout LLM after
  it's located the notes section.

**Acceptance criteria:**
- For a text-PDF with N numbered notes, the inventory has N entries with correct page
  ranges (verified against a fixture).
- For a scanned PDF, scout's LLM tool path produces a non-empty inventory by sampling
  pages with vision (no OCR fallback).
- Page ranges are bounded by next-note-header detection, not heuristic offset.
- Existing scout tests still pass.

**Verification:**
- `pytest tests/test_scout_notes_inventory.py tests/test_scout_infopack_roundtrip.py -v`
- Run scout against `data/FINCO.pdf` and dump inventory; eyeball for plausibility.

#### A.3 — Notes agent factory + Notes-10 prompt + end-to-end wiring

**Deliverable:**
- `notes/agent.py` with `create_notes_agent(template_type, pdf_path, scout_inventory,
  filing_level, model)` returning `(Agent, NotesDeps)`.
- `prompts/_notes_base.md` (shared persona, cell-format rules, evidence contract,
  Group/Company column rules, multi-page continuation guidance).
- `prompts/notes_corporate_info.md`.
- `notes/coordinator.py` with `run_notes(config, infopack, event_queue, session_id)`
  returning `NotesCoordinatorResult` analogous to face-statement coordinator.
- `server.py` and `run.py`: `notes_to_run` config field; CLI `--notes` flag.
- `workbook_merger.py`: include notes workbooks.

**Acceptance criteria:**
- `python3 run.py data/FINCO.pdf --notes corporate_info --statements SOFP` runs both
  face-statement and notes pipelines and produces a single `filled.xlsx` with both a
  populated `SOFP-CuNonCu` and a populated `Notes-CI` sheet.
- Notes-CI cells contain plain text with evidence in col D.
- Server SSE accepts `notes_to_run=["CORP_INFO"]` and emits events under `agent_id=notes_corporate_info`.

**Verification:**
- `pytest tests/test_notes_agent_factory.py tests/test_notes_coordinator.py tests/test_notes_e2e_corp_info.py -v`
- Manual: open merged xlsx, confirm Notes-CI populated and merge order intact.

**🔵 CHECKPOINT A — human review.**

### Phase B — Single-agent notes sheets

#### B.1 — Notes 11 (Accounting Policies)

**Deliverable:**
- `prompts/notes_accounting_policies.md` covering: read PDF Note 2 fully, enumerate
  sub-policies from headings, match each to one of 53 template rows (label list
  embedded in prompt), write paragraph per matched row.
- Wiring through `notes/coordinator.py` — no new agent infra.

**Acceptance criteria:**
- `--notes accounting_policies` produces a populated Notes-SummaryofAccPol sheet on
  FINCO sample, with at least 15 rows filled and correct evidence citations.
- Unmatched policies (i.e., policies in PDF that don't fit any of the 53 rows) are
  skipped — they are NOT redirected to row 112 (that row is in Notes-12 only).

**Verification:**
- `pytest tests/test_notes_e2e_accounting_policies.py -v`
- Manual: visually inspect 5 random rows for fidelity to PDF Note 2.

#### B.2 — Notes 13 (Issued Capital)

**Deliverable:**
- `prompts/notes_issued_capital.md` covering: identify share-capital note in PDF, extract
  the structured movement table (numbers + balance lines), write to template's 27 rows
  using the standard label-match flow.
- Confirm writer.py handles numeric values + dual Group/Company columns when
  filing_level == "group".

**Acceptance criteria:**
- `--notes issued_capital` populates the structured Notes-Issuedcapital table.
- Group filings write Group CY/PY (B/C) and Company CY/PY (D/E) values.
- Company filings write only CY/PY (B/C).

**Verification:**
- `pytest tests/test_notes_e2e_issued_capital.py -v`
- Manual: open output, confirm numeric values match the PDF share-capital note.

#### B.3 — Notes 14 (Related Party)

**Deliverable:**
- `prompts/notes_related_party.md` similar pattern to B.2.

**Acceptance criteria & verification:** parallel to B.2.

**🔵 CHECKPOINT B — human review.**

### Phase C — Notes 12 sub-agent fan-out

#### C.1 — Sub-coordinator skeleton + batch splitter

**Deliverable:**
- `notes/listofnotes_subcoordinator.py` with: input = scout's notes inventory; output =
  `list[NotesPayload]` ready for writer.
- Batch splitter: round-robin or page-contiguous split into 5 batches.
- Asyncio fan-out using `notes/agent.py` factory with the
  `prompts/notes_listofnotes.md` system prompt.
- Payload aggregator + unmatched-row-112 concatenation.

**Acceptance criteria:**
- Splitter divides any inventory N≥1 into ≤5 non-empty batches; ≤6 notes per batch
  is the soft cap.
- Sub-agent failure isolation: if 1 of 5 sub-agents fails (after one retry), the other
  4 batches still write payloads.
- Unmatched payloads (chosen_row_label == "Disclosure of other notes to accounts" with
  multiple sources) concatenate with section headers in writer.

**Verification:**
- `pytest tests/test_notes12_subcoordinator.py -v` covers: split logic, parallel
  fan-out (mocked agents), unmatched concatenation, sub-agent failure isolation.

#### C.2 — Sheet-12 prompt + end-to-end

**Deliverable:**
- `prompts/notes_listofnotes.md` covering: input is a batch of `(note_num, title,
  page_range)` entries; for each, view the pages, decide template row(s), emit
  payloads. Includes one-note-multi-row guidance and multi-page continuation rules.
- Wiring in `notes/coordinator.py` to dispatch sheet 12 through the sub-coordinator.

**Acceptance criteria:**
- `--notes list_of_notes` fills Notes-12 on FINCO sample with at least 15 rows populated
  (real-world expectation 20–40).
- `notes12_unmatched.json` written if any notes ended up in row 112 with multiple sources.
- Each filled row has evidence citing PDF page numbers.

**Verification:**
- `pytest tests/test_notes12_e2e.py -v`
- Manual: spot-check 5 rows for correct topic mapping.

**🔵 CHECKPOINT C — human review.**

### Phase D — UI + history integration

#### D.1 — Backend API + types

**Deliverable:**
- `server.py`: `RunConfigRequest.notes_to_run: list[str]`.
- `web/src/lib/types.ts`: `NotesTemplateType` enum + `RunConfigPayload.notes_to_run`.
- `web/src/lib/api.ts`: pass through.
- DB persistence verified — notes agents stored in `agents` table with `agent_id`
  prefix `notes_` (no schema migration needed).

**Acceptance criteria & verification:**
- `pytest tests/test_server_notes_api.py tests/test_db_notes_persistence.py -v`.
- Manual curl: POST `/api/run/<sid>` with notes_to_run, observe SSE events.

#### D.2 — Pre-run panel checkboxes

**Deliverable:**
- `web/src/components/PreRunPanel.tsx`: render 5 new checkboxes (default OFF) under
  a "Notes" section heading.

**Acceptance criteria:**
- Checkboxes are independent of face-statement checkboxes.
- Submitting with no notes selected runs face-statement extraction only (current
  behaviour — no regression).

**Verification:**
- `web/src/__tests__/PreRunPanel.notes.test.tsx`.
- Manual: toggle checkboxes, submit, observe expected agents.

#### D.3 — Live timeline + history rendering

**Deliverable:**
- `web/src/components/AgentTabs.tsx`: render notes tabs alongside face statements,
  using the same status-badge logic.
- `web/src/components/RunDetailView.tsx`: include notes agents in past-run replay.

**Acceptance criteria:**
- Live run page shows notes tabs in stable order after face-statement tabs.
- History detail modal renders notes agents identically to face-statement agents.

**Verification:**
- `web/src/__tests__/AgentTabs.notes.test.tsx`,
  `web/src/__tests__/RunDetailView.notes.test.tsx`.
- Manual: complete a run with notes, browse to History, open the run.

**🔵 CHECKPOINT D — human review.**

### Phase E — Edge-case hardening + final tests

#### E.1 — Retry budget enforcement

**Deliverable:**
- Coordinator retry-once logic for both sub-agent (Notes-12) and whole-sheet
  (Notes-10/11/13/14) failures.
- `notes12_failures.json` side-file when sub-agent retry exhausts.

**Acceptance criteria:**
- Failure-injection test confirms exactly 1 retry, then partial-coverage path.
- Sheet-level failure does not block other sheets.

**Verification:**
- `pytest tests/test_notes_retry_budget.py -v`.

#### E.2 — Multi-page continuation prompt tuning + char-limit guard

**Deliverable:**
- Prompt language for multi-page continuation finalized (and stop-condition for
  reaching the next note's header).
- Writer char-limit guard verified against >30K-char payload.

**Acceptance criteria:**
- Test with synthetic 35K-char payload yields ≤30K cell + truncation footer.
- Test with note that physically spans pages 28→30 (with intervening page 29) confirms
  the agent can fetch additional pages on its own.

**Verification:**
- `pytest tests/test_notes_continuation.py tests/test_notes_char_limit.py -v`.

#### E.3 — Final E2E + docs

**Deliverable:**
- `tests/test_notes_e2e_full_pipeline.py` running all 5 notes sheets against FINCO
  sample (mocked LLM with a recorded fixture).
- `CLAUDE.md` updated:
  - New section on notes feature.
  - "Files That Must Stay in Sync" rows for notes templates, agents, prompts, UI.
  - Known issues entry if mTool format remains unverified.

**Acceptance criteria:**
- Full pytest suite green.
- CLAUDE.md changes pass `claude-md-improver` lint (or equivalent visual review).

**Verification:**
- `pytest -v` (full suite).
- Manual review of CLAUDE.md diff.

**🔵 CHECKPOINT E — final human sign-off before merging worktree to `main`.**

---

## 6. Risks the plan does not eliminate

These remain accepted risks that the human reviewer should be aware of:

1. **mTool cell-format ground truth unverified.** Plan ships plain text + Alt+Enter
   line breaks + ASCII tables. If a real filed mTool sample shows different
   conventions, writer.py needs revisiting (small change but real).
2. **No verifier for prose notes.** Quality gate is manual review + evidence
   citation discipline.
3. **Large scanned PDFs may stress the per-run token budget**, especially with the 5
   sub-agent fan-out for Notes 12. Concurrency tunable at one location
   (`SUBCOORD_PARALLEL = 5`) for emergency reduction.
4. **Group-vs-Company column rule for prose notes** assumes mTool tolerates empty
   Company columns when the value is in Group columns. Pending mTool sample
   confirmation.
5. **Notes 13/14 numeric extraction** reuses face-statement-style label matching but
   through a new write path; if the templates' label vocabulary differs subtly from
   what the existing fuzzy-match thresholds expect, the prompts may need extra
   guidance (would be caught at Checkpoint B).

---

## 7. Out-of-band questions still open

Will not block planning, but worth answering before/during Phase A:

- Got a real filed mTool workbook to validate cell format? (See risk #1.)
- Should the Group-filing rule for prose notes (10/11) write to **only Group columns**,
  **only Company columns**, or **both** (duplicate)? Currently planned: Group only.

---

## 8. Todo list (chronological)

```
[ ] A.1 — notes_types + payload + writer scaffolding
[ ] A.2 — scout notes-inventory extension
[ ] A.3 — notes agent factory + Notes-10 + end-to-end wiring
🔵 CHECKPOINT A — human review

[ ] B.1 — Notes 11 (Accounting Policies)
[ ] B.2 — Notes 13 (Issued Capital)
[ ] B.3 — Notes 14 (Related Party)
🔵 CHECKPOINT B — human review

🟩 C.1 — Notes-12 sub-coordinator skeleton + batch splitter (done — 13 tests green)
🟩 C.2 — Notes-12 prompt + end-to-end (done — 6 tests green)
🔵 CHECKPOINT C — human review

🟩 D.1 — backend API + types (notes_to_run already wired server-side; frontend RunConfigPayload + NotesTemplateType types added)
🟩 D.2 — pre-run panel checkboxes (5 notes checkboxes, default OFF; notes-only runs allowed)
🟩 D.3 — live timeline + history rendering (AgentTabs notes bucket + notesInRun gate + NOTES_TAB_LABELS)
🔵 CHECKPOINT D — human review

🟩 E.1 — retry-budget enforcement (SINGLE_AGENT_MAX_RETRIES + notes_<TEMPLATE>_failures.json side-log)
🟩 E.2 — multi-page continuation prompt tuning + char-limit guard (test_notes_continuation.py + test_notes_char_limit.py)
🟩 E.3 — final E2E + CLAUDE.md docs (test_notes_e2e_full_pipeline.py + §14 Notes Feature + Files-to-sync rows)
🔵 CHECKPOINT E — final sign-off → merge worktree
```
