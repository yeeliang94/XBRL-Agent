# Implementation Plan: Notes Formatting Sidecar + House-Style Floor

**Overall Progress:** `90%` — all code + tests + docs landed 2026-07-06
(new tests in `tests/test_notes_format_sidecar.py`; full backend suite green,
0 failures). Remaining: the two live-run validation subtasks in
Step 10 (need a real LLM run + v27 telemetry capture).
**PRD Reference:** none — shaped in-session 2026-07-06 (brainstorm: reduce AI
notes-formatter token cost + wall-clock). Context: CLAUDE.md gotcha #16
(notes formatter agent), `notes/formatting_agent.py`,
`prompts/notes_formatter.md`, `notes/format_patch.py`.
**Last Updated:** 2026-07-06

## Summary

The standalone AI notes formatter re-buys context the extraction agent
already paid for: it re-views every source PDF page as vision images and
runs up to 4 serial passes per sheet. Instead, notes **extraction** agents —
who already have the pages in front of them — emit a small structured
**formatting sidecar** (`format_ops`, the SAME constrained op schema the
formatter already produces) alongside each payload, applied deterministically
at write time through the existing `apply_sheet_patch` gates. Cells with no
usable ops fall back to a deterministic **house-style floor** (accountant
convention: borderless, right-aligned numerics, summation rules under amount
columns of total rows) — zero LLM tokens. The manual formatter agent survives
unchanged as the repair/re-do path.

## Key Decisions

- **Ops channel, not styled HTML** — extraction agents STILL emit style-free
  content HTML (gotcha #16 invariant preserved). Formatting travels as a
  separate structured `format_ops` field on `NotesPayload`, applied by
  deterministic code through the existing sanitise + `verify_format_only`
  gates. The sanitiser contract ("agent-emittable ⊆ sanitiser-permitted") is
  untouched because ops are validated server-side, never free-form CSS.
- **Reuse the formatter's patch vocabulary verbatim** (`notes/format_patch.py`
  targets + style keys) — no second schema, no second validator. The
  formatter agent, its CAS writes, snapshots, and revert stay exactly as they
  are (they become the repair tool, not the default path).
- **Formatting failure never blocks content** — invalid/unusable ops are
  logged and dropped; the cell falls to the house-style floor. Extraction
  accuracy and write success are never gated on formatting.
- **House-style floor is a synthesized ops patch**, not a new styling
  mechanism — it produces the same op shapes and flows through the same
  apply gate, so there is exactly ONE code path that mutates cell styling.
- **No DB schema change** — ops are applied at write time and not persisted.
  "Reset cell to theme" (existing) and the manual formatter (existing) cover
  undo/repair. Rollback = config flip, no migration.
- **Fidelity hierarchy (user decision 2026-07-06):** mirror-the-PDF is the
  goal; house-style deviation is always acceptable. So: agent ops (fidelity)
  → floor (house style) → theme defaults (unstyled), in that order.
- **Plan file location:** `docs/PLAN.md` is the ACTIVE mTool plan (75%) — this
  plan lives at `docs/PLAN-notes-format-sidecar.md` instead of replacing it.

## Cost Model (why this wins)

Per formatter click today (`notes/formatting_agent.py`): every source page of
every filled cell rendered to PNG via `view_pdf_pages` (vision tokens the
extraction agent already spent), the full HTML of every cell in the user
prompt, and up to 4 serial `agent.run` passes (initial → output-rejection
retry → validation repair → self-check, self-check re-sending patch +
rendered appearance of every row). The sidecar's marginal cost is ~100–300
output tokens per note payload on a context the agent already holds; the
floor is free. Local telemetry is pre-v27 (no token columns yet) — Phase 6
validates the saving on a real run with v27 columns populated.

## Pre-Implementation Checklist

- [x] 🟩 Approach agreed (A with B as floor; fidelity preferred, house style
      acceptable)
- [x] 🟩 Seams mapped: `NotesPayload` (payload.py), `write_notes` tool
      (agent.py:1295), `write_notes_workbook` / `_combine_payloads` /
      `_sanitize_payload` (writer.py), `apply_sheet_patch` /
      `_apply_operations` (format_patch.py), `persist_notes_cells`
      (persistence.py)
- [x] 🟩 No conflicting in-progress work on `notes/` (formatter hardening is
      on main — `formatting_agent.py` carries the v27 taxonomy/telemetry)

## Tasks

### Phase 1: Deterministic foundation (no agent changes yet)

- [x] 🟩 **Step 1: Public per-cell apply entry point** — expose a
  `apply_cell_operations(html, ops) -> str` in `notes/format_patch.py` that
  wraps the existing private `_apply_operations` + `sanitize_notes_html` +
  `verify_format_only` gates for ONE cell's HTML (the sheet-level
  `apply_sheet_patch` keeps its contract untouched).
  - [x] 🟩 Raises `FormatPatchError` on any invalid op / content change
        (caller decides fallback)
  - [x] 🟩 Validate ops shape early (list of `{target, style}` objects) with
        the same target/style vocabulary — no new keys
  - **Verify:** unit tests in `tests/test_notes_format_sidecar.py`: valid ops
    style a table cell; content-changing ops raise; disallowed style keys
    raise. Existing `tests/test_notes_format_patch.py` still green.

- [x] 🟩 **Step 2: House-style floor synthesizer** — new
  `notes/format_defaults.py::house_style_ops(html) -> list[ops]` producing a
  deterministic accountant-convention patch for each `<table>` in the HTML:
  clear all borders; `numeric_cells` → `text_align: right`; `total_rows`
  restricted to the numeric columns → `border_top` 1px solid +
  `border_bottom` 3px double. Pure function over HTML, no LLM, no I/O.
  - [x] 🟩 Numeric-column detection: a column is "amount" when a majority of
        its body cells match the accountant-number shape (Python twin of the
        clipboard's `_NUMERIC_CELL_RE`: `1,595` / `(95)` / `-` / `1.5`)
  - [x] 🟩 Tables with no numeric columns get borderless + nothing else;
        HTML with no tables returns `[]` (prose untouched)
  - **Verify:** unit tests: 3-col note table → borderless, cols 2-3
    right-aligned, total row rules under cols 2-3 only (label column
    untouched — the formatter prompt's own EXTENT rule); prose-only cell →
    `[]`. Round-trip through `apply_cell_operations` passes the sanitiser.

### Phase 2: Payload + write-tool plumbing

- [x] 🟩 **Step 3: `format_ops` on NotesPayload** — optional
  `format_ops: Optional[list] = None` field on `NotesPayload`
  (`notes/payload.py`), following the `source_note_refs` pattern: optional in
  raw JSON, shape-checked leniently in `__post_init__` (non-list / non-dict
  entries → `ValueError`, same error path as other malformed payloads), and
  IGNORED (forced to None with a warning) on numeric payloads
  (`numeric_values` set — sheets 13/14 are out of formatting scope, mirroring
  the formatter's 422).
  - **Verify:** payload unit tests: accepts None/valid list; rejects garbage
    shapes; numeric payload with ops → ops dropped, payload still valid.

- [x] 🟩 **Step 4: Parse ops in the `write_notes` tool** — thread
  `raw.get("format_ops")` through the payload construction in
  `notes/agent.py::write_notes` (both direct mode and the Sheet-12
  `payload_sink` path — the field rides the payload, so the sub-coordinator
  aggregation carries it for free).
  - **Verify:** tool-level test: a payloads_json carrying `format_ops`
    produces a `NotesPayload` with the ops attached; a payload without it
    behaves byte-identically to today (regression: existing
    `tests/test_notes_agent*.py` green). Covered directly by
    `test_write_notes_tool_threads_format_ops_into_payload` (invokes the
    registered write_notes tool in sink mode — peer-review follow-up).

### Phase 3: Writer integration — the one styling code path

- [x] 🟩 **Step 5: Apply ops-or-floor at cell finalisation** — in
  `notes/writer.py`, after `_inject_headings` + `_sanitize_payload` and
  before the cell lands in `cells_written`: attempt
  `apply_cell_operations(html, payload.format_ops)`; on `FormatPatchError`
  or absent ops, apply `house_style_ops(html)` (when the floor flag is on);
  on floor failure, keep the unstyled HTML. Never raise out of the styling
  step.
  - [x] 🟩 Heading `<h3>` injection happens BEFORE ops application, so table
        indices in ops match what the agent saw (headings add no tables)
  - [x] 🟩 Truncation (`truncate_with_footer`) ordering checked: cap applies
        to RENDERED length (unchanged), styled HTML may be longer raw —
        confirm the existing rendered-length cap logic still governs
  - **Verify:** writer unit tests: payload with valid ops → styled HTML in
    `cells_written`; invalid ops → floor styling + a structured warning in
    the write result (surfaced like `sanitizer_warnings`); flag off → ops
    still apply but no floor. Existing writer tests green.

- [x] 🟩 **Step 6: Sheet-12 combined-cell re-indexing** — when
  `_combine_payloads` concatenates multiple payloads into one row (the
  row-112 unmatched path and multi-payload rows), each payload's ops
  reference ITS OWN tables. Re-index deterministically: offset every op's
  `table` by the cumulative `<table>` count of the preceding chunks. If
  re-indexing is ambiguous (any op missing a `table` key), drop that
  payload's ops → floor.
  - **Verify:** unit test: two payloads (1 table each, each with table-0 ops)
    combined → second payload's ops land on table 1; ambiguous ops → floor
    applied to the whole combined cell.

### Phase 4: Prompts

- [x] 🟩 **Step 7: Sidecar section in `prompts/_notes_base.md`** — add a
  `FORMATTING OBSERVATION (format_ops)` section: while reading each note's
  table in the PDF, also record its visible formatting as `format_ops` using
  a TRIMMED version of the formatter prompt's target/style vocabulary
  (borderless-vs-grid, summation-rule extent under amount columns only,
  fills, alignment — the same rules that already live in
  `prompts/notes_formatter.md`, minus the patch-envelope/confidence
  machinery). State explicitly: content HTML stays style-free; `format_ops`
  is optional — omit it when unsure and the house style applies; NEVER let
  formatting effort reduce content coverage.
  - [x] 🟩 Keep the existing "agents emit style-free HTML" wording intact
        and scope it to the `content` field (invariant preserved, clarified)
  - **Verify:** pinning test alongside `tests/test_notes_prompt_phase1.py`
    asserting the section renders in both MFRS + MPERS notes prompts and
    the style-free-content rule is still present.

### Phase 5: Config + observability

- [x] 🟩 **Step 8: Floor kill switch + coverage counters** —
  `XBRL_NOTES_HOUSE_STYLE` (default ON, read at call time like
  `XBRL_FACT_BASED_CHECKS`; env-only for MVP — no Settings UI). Log one
  per-sheet line at write time: `N cells ops-styled, M floor-styled, K
  unstyled` and mirror the counts into the write result warnings so they
  reach `NotesAgentResult` / history like `write_sanitizer_warnings` do.
  - **Verify:** unit test: flag off → no floor ops applied; counters correct
    on a mixed batch. `tests/conftest.py` decision: leave ON (deterministic,
    no LLM — unlike spot-check there is no cost reason to default the suite
    off); re-check any pipeline-count tests that assert exact HTML.
  - **Deviation (implemented):** the per-sheet COUNTS go to the log line
    only; the warnings channel carries ops-DROP events ("format_ops
    dropped (…)") — mixing routine tallies into the history warnings UI
    would be noise. Full suite confirmed no exact-HTML pipeline tests broke
    (3030 passed).

### Phase 6: Docs + validation

- [x] 🟩 **Step 9: Documentation sync** — update CLAUDE.md gotcha #16 (the
  "agents emit style-free HTML" paragraph gains the ops-channel delta + the
  floor), `docs/NOTES-PIPELINE.md`, and mark the formatter agent's role as
  "repair pass" in both. Update this plan's progress.
  - **Verify:** docs mention the ONE styling code path
    (`apply_cell_operations`) and the fallback order (ops → floor → theme).
  - **Deviation (found during implementation):** `docs/NOTES-PIPELINE.md`
    does NOT exist — CLAUDE.md's "Full walkthrough" pointer to it is stale
    (pre-existing). The gotcha #16 bullet carries the documentation;
    creating the whole walkthrough doc is out of this plan's scope.

- [ ] 🟨 **Step 10: End-to-end + real-run validation** — mocked e2e (extend
  `tests/test_e2e.py` pattern): a notes run whose mocked agent emits
  `format_ops` lands styled HTML in `notes_cells`, visible via the existing
  cells API; overlay/xlsx download unaffected (text overlay flattens styles
  — existing behaviour). Then one live run on
  `data/FINCO-Audited-Financial-Statement-2021.pdf --notes ...`:
  - [ ] 🟥 Confirm Review-panel tables render styled without a formatter click
  - [ ] 🟥 Record v27 token telemetry for one formatter click on the SAME
        sheet before/after (the click should now be needed rarely; when
        used, its cost is unchanged — the saving is in not needing it)
  - **Verify:** live-run notes tables match the PDF's border/alignment
    pattern or a clean house style; extraction content quality unchanged
    (spot-compare against a pre-change run of the same PDF; eval benchmark
    score if one exists for the document).

## Rollback Plan

- **No schema change, no migration** — nothing to walk back in the DB.
- Floor: set `XBRL_NOTES_HOUSE_STYLE=0` → cells persist unstyled exactly as
  today (theme defaults render).
- Sidecar: agents that omit `format_ops` (or emit garbage) already degrade to
  the floor/unstyled path by design — reverting the prompt section alone
  fully disables the feature without code rollback.
- The manual formatter agent is untouched throughout — it remains the repair
  path for any cell the sidecar styles wrongly, and "Reset cell to theme" +
  formatter revert (v27 snapshots) cover per-cell/per-sheet undo.
- State to check after rollback: `notes_cells.html` for affected runs (styles
  are inline on table tags; a re-run of the sheet regenerates clean HTML).

## Invariant Cross-Check (CLAUDE.md)

- Gotcha #16 "agents emit style-free HTML": preserved for the content
  channel; ops are a validated structured channel → gotcha text updated in
  Step 9, pinning tests updated in the same commit.
- Sanitiser whitelist lock-step: unchanged — ops resolve to already-whitelisted
  styles and every application re-runs `sanitize_notes_html`.
- Gotcha #14 (no deterministic matching in notes pipeline): untouched — the
  floor styles ALREADY-EXTRACTED HTML; it does no label/content matching.
- Gotcha #10/#22: no new workbook writers; styling mutates HTML strings
  before the existing atomic write/persist paths.
- Formatter interlocks (review/format task claims, CAS, snapshots): untouched.
