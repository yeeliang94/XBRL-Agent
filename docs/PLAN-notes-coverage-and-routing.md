# Implementation Plan: Notes Coverage Checklist & Top-Line Routing

**Overall Progress:** `100%` (all 8 phases complete)
**PRD Reference:** [docs/PRD-notes-coverage-and-routing.md](PRD-notes-coverage-and-routing.md)
**Last Updated:** 2026-07-04

> **DONE 2026-07-04.** Phases 4–8 landed: schema v28 (`notes_coverage_rows` +
> repo helpers), the reviewer verdict tools (`resolve_coverage_note` /
> `verify_subnote`) + checklist in the packet, `_finalize_coverage` persistence
> on every reviewer-pass exit path + run-status tipping
> (`_notes_coverage_tips_status`) + `XBRL_NOTES_COVERAGE` gate,
> `GET /api/runs/{id}/notes-coverage` + `NotesCoveragePanel.tsx`, and the E2E
> pass→persist→API test. CLAUDE.md gotcha #27 + the v28 schema bullet record
> the invariants.

## Summary

Two coupled hardenings for notes extraction: (1) a holistic, human-visible
**coverage checklist** — every top-level note from the scout inventory
reconciled against placements on ALL notes sheets, auto-resolved by the notes
reviewer before the human sees it, with per-sub-ref accounting; (2) a
**top-line routing rule** (notes stay whole; only explicitly-labelled
material/significant accounting-policy sections are carved out to the
policies sheet) enforced at prompt tier and by a new split detector the
reviewer acts on. Approach: maximum reuse — the inventory, provenance,
detectors, and acting reviewer all exist; we add one pure builder module, one
DB table, reviewer packet/tools wiring, one API route, and one UI panel.

## Key Decisions

- **Human sees the post-reviewer checklist** — draft checklist is reviewer
  input only; UI fallback with "not yet reviewed" banner if the pass fails.
- **Reviewer auto-resolves** missing rows and hunts suspected numbering gaps
  (records "confirmed absent" for PDF-native holes).
- **Unresolved `missing` rows / uninvestigated suspected gaps →
  `completed_with_errors`.** `not_verified` sub-refs warn only.
- **Sub-note accounting is universal** (cited / verified / missing /
  not_verified per sub-ref); always-visible child rows only for policies
  fan-out + carve-outs; other notes single-row with expandable roll-up.
- **Carve-out trigger is the closed label set** ("material|significant
  accounting policy|policies"), judged by the LLM against the PDF — no
  deterministic content matching (gotcha #14 preserved: detectors report by
  refs/coordinates only).
- **Kill switch:** `XBRL_NOTES_COVERAGE` (default on) gates the checklist
  pass + run-status tipping, mirroring `XBRL_SPOT_CHECK` conventions, so
  rollback is a config flip, not a revert.
- **Plan filename deviates from the /create-plan template** (`docs/PLAN.md`
  is the live notes-reviewer plan) — repo convention `PLAN-<feature>.md`.

## Pre-Implementation Checklist

- [x] 🟩 Brainstorm questions resolved (routing rule confirmed with worked
  example; checklist mechanics confirmed with mockup)
- [x] 🟩 PRD approved — all decisions locked 2026-07-04
- [x] 🟩 No conflicting in-progress work (main is clean; the deferred
  `notes/validator_agent.py` deletion doesn't collide — detectors live in
  `notes/detectors.py`)

## Tasks

### Phase 1: Routing rules (prompt tier) — fixes the splitting complaint at the source

- [x] 🟩 **Step 1: Top-line + carve-out rules in the notes prompts** (2026-07-04)
  - [x] 🟩 `prompts/_notes_base.md`: new "ACCOUNTING-POLICY CARVE-OUT"
    section (explicit-label trigger both directions, non-triggers, the
    confirmed investment-properties worked example) + a
    partition-not-duplication clarifier appended to the NO CROSS-SHEET
    DUPLICATION section so the carve-out can't be read as violating it
  - [x] 🟩 `prompts/notes_accounting_policies.md`: new "CARVED-OUT POLICY
    SUB-SECTIONS" section — the policies agent SWEEPS via
    `search_pdf_text` for labelled sub-sections embedded in topical notes
    (it is the only agent that can write to Sheet 11; Sheet-12 agents
    cannot cross-write, so exclude+collect is a two-sided contract)
  - [x] 🟩 `prompts/notes_listofnotes.md`: new "EMBEDDED POLICY
    SUB-SECTIONS" section — exclude ONLY explicitly-labelled sections,
    keep "Policy on X" / topic mentions whole; note still "written" in
    the coverage receipt
  - [x] 🟩 MPERS: rule lives in the shared base + both sheet prompts
    (which already carry the "significant" wording); rendered-prompt test
    covers both standards
  - [x] 🟩 `tests/test_notes_prompt_routing_rules.py` — 8 pins (file-level
    + rendered MFRS/MPERS)
  - **Verified:** 8/8 new tests pass; `test_notes_prompt_phase1.py` +
    `test_extraction_hardening_prompts.py` + `test_prompt_residual_plug_rule.py`
    (48) and the wider prompt/notes-agent sweep (290) all green.

### Phase 2: Split detector + reviewer routing enforcement

- [x] 🟩 **Step 2: `detect_topline_splits` in `notes/detectors.py`** (2026-07-04)
  - [x] 🟩 Pure function: one finding per (note, sheet) listing ALL rows —
    not pairwise noise; refs + labels only (gotcha-#14-safe)
  - [x] 🟩 Policies sheet exempt in both roles (fan-out + carve-out
    placements never counted); same-row multi-payload = one placement
  - [x] 🟩 8 unit tests incl. the PP&E/leases failure mode and the
    carve-out pair
  - **Verified:** `tests/test_notes_detectors_splits.py` 8/8 detector pins.
- [x] 🟩 **Step 3: Wire splits into the reviewer** (2026-07-04)
  - [x] 🟩 `notes/reviewer_agent.py`: `topline_splits` in `_build_context`
    (flows to `recompute_notes_findings` automatically) + `finding_keys`
    (`("topline_split", note, sheet, rows)`) + a `[TOP-LINE SPLIT]` packet
    block with the three verdict paths (peer disclosures → leave;
    topic-mention split → merge back; labelled policy fragment → move to
    Sheet 11)
  - [x] 🟩 **Carve-out false-positive defence** (found during
    implementation): `detect_cross_sheet_duplicates_by_ref` flags a
    legitimate Direction-1 carve-out (same note ref on Sheets 11+12) as a
    duplicate, and the old packet wording ordered a clear. The
    `[CROSS-SHEET DUPLICATION]` packet block + `prompts/notes_reviewer.md`
    now present the carve-out PARTITION as a legitimate shape to leave
    intact before any clear
  - [x] 🟩 `prompts/notes_reviewer.md`: new "Top-line split" handling entry
  - **Verified:** 4 wiring pins in `tests/test_notes_detectors_splits.py`
    (packet block, carve-out wording, finding key, clean packet);
    reviewer suites `test_notes_reviewer_tools.py` +
    `test_notes_reviewer_self_verify.py` green (35 total); 200-test
    reviewer/validator/detector/prompt sweep green.
  - [x] 🟩 **Codex review fixes (2026-07-04):** (1) the server's reviewer
    skip gate (`_run_notes_reviewer_pass` n_items) didn't count
    `topline_splits`, so a split-only run skipped the reviewer — fixed
    structurally with a shared `FINDING_FAMILIES` tuple in
    `notes/reviewer_agent.py` that the skip gate, the packet clean-check,
    and a per-family consistency test all derive from; (2) the detector
    over-scoped to every non-policies sheet — now Sheet-12-only (mirrors
    `detect_same_sheet_row_collisions`), since Corporate Info and the
    numeric sheets legitimately multi-row one note. Pinned by the
    family-coverage + CI/numeric non-flag tests in
    `tests/test_notes_detectors_splits.py`.

### Phase 3: Checklist builder (pure core, no I/O)

- [x] 🟩 **Step 4: `notes/coverage_checklist.py`** (2026-07-04)
  - [x] 🟩 `build_draft_checklist(inventory_rows, provenance_entries,
    skip_receipts, policies_sheet) -> Checklist` — statuses
    placed/missing/skipped(+reason)/suspected_gap; placements deduped per
    (sheet, row) with labels
  - [x] 🟩 Contiguity: INTERNAL numbering holes only (before-first /
    after-last are the documented blind spot) → suspected_gap rows sorted
    in sequence; empty inventory → `Checklist(inventory_available=False)`
    (loud, never empty-but-green)
  - [x] 🟩 Sub-ref states via the same `_subnote_key`/`_top_note_nums`
    helpers the detectors use (cannot drift): `cited` vs `not_verified`;
    reviewer verdict values (`verified`/`missing`) reserved in the
    vocabulary, never emitted by the builder
  - [x] 🟩 Placement kinds: `primary` / `fan_out` (≥2 rows, all on the
    policies sheet) / `carve_out` (policies-sheet placement while the note
    also lives elsewhere) — PRD Decision 5's two child-row cases
  - [x] 🟩 `to_dict()` + `counts()` for Phase 4 persistence / Phase 7 API
  - **Verified:** `tests/test_coverage_checklist.py` 12/12 (all statuses,
    the cross-sheet "not on 12 but on 11" case, holes, loud emptiness,
    blob-write blind spot, fan-out/carve-out classification, round-trip
    shape). Full backend suite: 2831 passed / 0 failed.

### Phase 4: Schema v28 + persistence

- [x] 🟩 **Step 5: `notes_coverage_rows` table + repository helpers**
  - [x] 🟩 `db/schema.py`: v27 → v28, pure `CREATE TABLE IF NOT EXISTS`
    walk-forward (no ALTER): run_id, note_num, subnote_ref (nullable —
    NULL = top-level row), status, reason, placements_json, reviewer_added,
    title, updated_at; unique index on (run_id, note_num,
    coalesced subnote_ref)
  - [x] 🟩 `db/repository.py`: `replace_notes_coverage_for_run` (delete +
    insert in one transaction — checklist is recomputed wholesale) +
    `fetch_notes_coverage`
  - [x] 🟩 `tests/test_db_schema_v28.py`: fresh-init + v27→v28 walk-forward
    (copy the v25/v26 test shape)
  - **Verify:** `./venv/bin/python -m pytest tests/test_db_schema_v28.py -v`;
    full `tests/test_db_schema_*` suite still green.

### Phase 5: Reviewer auto-resolve integration

- [x] 🟩 **Step 6: Checklist into the reviewer packet**
  - [x] 🟩 `_build_context` gains `coverage_checklist` (supersedes the bare
    `coverage_gaps` list in the packet rendering; the detector stays for
    `verify_findings` regression checks); packet renders: missing rows
    (with page ranges), suspected gaps (with the numbering hole), uncited
    sub-refs pending verification — each with an explicit mandate
  - [x] 🟩 `finding_keys` extended so reviewer-introduced coverage
    regressions still surface
  - **Verify:** packet-rendering unit test shows the three mandate blocks.
- [x] 🟩 **Step 7: Reviewer verdict tools**
  - [x] 🟩 `resolve_coverage_note(note_num, verdict, reason, source_pages)` —
    verdicts: `confirmed_absent` (suspected gap is a PDF numbering skip) /
    `not_applicable`; grounding-guarded like `_ground_evidence` (must have
    viewed the relevant pages)
  - [x] 🟩 `verify_subnote(note_num, subnote_ref, verdict, reason,
    source_pages)` — verdicts: `verified` (content present / folded-in) /
    `missing` (then the reviewer must fix via `edit_note_cell` /
    `author_note_cell`, which flips it on recompute)
  - [x] 🟩 Verdicts accumulate on `NotesReviewerDeps`; `verify_findings`
    recompute merges them so the reviewer sees remaining open rows
  - [x] 🟩 Tests: verdict guards (no grounding → rejected); author flips
    missing→placed on recompute; unresolved rows survive
  - **Verify:** `./venv/bin/python -m pytest tests/test_notes_reviewer_coverage.py -v`

### Phase 6: Server orchestration + run status

- [x] 🟩 **Step 8: Draft → reviewer → final sequence in `server.py`**
  - [x] 🟩 At the `reviewing_notes` stage (near the existing inventory
    persistence, ~line 5335): build the draft checklist, thread it into
    `_run_notes_reviewer_pass`; after the pass returns, recompute the FINAL
    checklist (provenance + verdicts), persist via
    `replace_notes_coverage_for_run`, emit a `notes_coverage` SSE event
  - [x] 🟩 Reviewer-pass failure → persist the DRAFT checklist with a
    `not_reviewed` marker (UI banner state)
  - [x] 🟩 Manual re-review path (`api/notes_reviewer.py`, durable task
    pattern) recomputes + re-persists the final checklist on completion
  - [x] 🟩 Gate the whole pass on `XBRL_NOTES_COVERAGE` (default on; suite
    default per `tests/conftest.py` conventions — decide ON with
    deterministic fixtures vs OFF like spot-check, based on how many
    pipeline-count tests it perturbs)
  - **Verify:** mocked-pipeline test asserts event order (`reviewing_notes` →
    reviewer → `notes_coverage`) and that the persisted rows are
    post-reviewer state.
- [x] 🟩 **Step 9: Run status + loud empty inventory**
  - [x] 🟩 Unresolved `missing` / uninvestigated `suspected_gap` after the
    reviewer pass → run lands `completed_with_errors` (respect gotcha #10:
    status set through the existing terminal-status path, never a second
    writer); `not_verified` sub-refs never tip status
  - [x] 🟩 Empty/failed inventory on a notes-targeting run → structured
    warning event (gotcha #20 pattern) + `inventory_unavailable` checklist
    state + `completed_with_errors` — replaces the silent
    degrade-to-no-gaps in the `_inv_nums` try/except
  - [x] 🟩 Tests: status tipping matrix (missing→error; confirmed_absent→no
    error; not_verified→no error; empty inventory→error + warning event)
  - **Verify:** `./venv/bin/python -m pytest tests/test_notes_coverage_run_status.py -v`;
    `tests/test_server_run_lifecycle.py` + `tests/test_e2e.py` still green.

### Phase 7: API + frontend Coverage panel

- [x] 🟩 **Step 10: `GET /api/runs/{id}/notes-coverage`**
  - [x] 🟩 In `api/notes.py`: returns checklist rows (nested: top-level rows
    with sub-ref children + placement coords), summary counts, and the
    banner state (`reviewed` / `not_reviewed` / `inventory_unavailable`);
    404-safe on runs without notes
  - [x] 🟩 Tests: shape, nesting, banner states, legacy run (no rows) →
    `pre_feature` response
  - **Verify:** `./venv/bin/python -m pytest tests/test_notes_coverage_api.py -v`
- [x] 🟩 **Step 11: `NotesCoveragePanel.tsx`**
  - [x] 🟩 New section inside the Notes tab (a section, NOT a new
    `role="tab"` — avoids the gotcha #7 tablist collision), rendered from
    the endpoint: summary line, table with status badges, expandable
    sub-ref detail, always-visible children for fan-out/carve-out,
    "not yet reviewed" / "inventory unavailable" banners
  - [x] 🟩 Inline styles from `theme.ts` tokens only (gotcha #7); placement
    click-through scrolls the notes editor to that sheet/row (reuse the
    existing row-focus mechanism in `NotesReviewTab`)
  - [x] 🟩 Web tests: statuses render, expansion works, banner states,
    click-through fires
  - **Verify:** `cd web && npx vitest run NotesCoveragePanel` green; manual
    check via `./start.sh` on a sample run.

### Phase 8: End-to-end + docs

- [x] 🟩 **Step 12: E2E + documentation sync**
  - [x] 🟩 Extend `tests/test_e2e.py` (mocked): notes run produces a
    persisted post-reviewer checklist; splitting fixture resolves to one
    field + carve-out
  - [x] 🟩 CLAUDE.md: new gotcha entry (coverage checklist invariants:
    post-reviewer visibility, status tipping, gotcha-#14-safe detectors,
    kill switch) + schema v28 bullet under gotcha #11
  - [x] 🟩 `docs/NOTES-PIPELINE.md` walkthrough section; PRD status → final
  - **Verify:** full backend suite `./venv/bin/python -m pytest tests/ -v`
    + `cd web && npx vitest run` both green.

## Rollback Plan

- **Config-level:** set `XBRL_NOTES_COVERAGE=0` — checklist pass, status
  tipping, and SSE events all skip; extraction/reviewer behave as today.
  Prompt routing rules (Phase 1) are independent and low-risk; revert the
  prompt commit alone if agent behaviour regresses.
- **Schema:** `notes_coverage_rows` stays as an inert table if the feature
  is rolled back (the `doc_conversions` precedent — never delete a migration
  step; gotcha #11).
- **State to check after rollback:** runs stuck `completed_with_errors`
  purely from coverage tipping re-land correctly on rerun; no reviewer task
  rows left `running` (existing startup reconciliation covers this).

## Ordering rationale

Phases 1–2 ship the routing fix (the user-reported splitting complaint)
independently and early. Phases 3–4 are pure + schema foundations testable in
isolation. Phase 5 makes the reviewer able to act before Phase 6 asks it to.
UI last (Phase 7) because it renders whatever the API serves. Each phase
leaves `main` green and shippable.
