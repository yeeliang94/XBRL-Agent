# Implementation Plan: MPERS abstract-row fills (parity with MFRS)

**Overall Progress:** `100%` _(local; user-side live smoke run on Windows still owed)_
**Issue:** The Bug A abstract-row write guard (2026-04-26) is silently
MFRS-only because `scripts/generate_mpers_templates.py` does not paint the
dark-navy `1F3864` fill that MFRS templates carry on XBRL abstract concept
rows. Discovery is fill-driven, so on MPERS the guard is a no-op.
**Last Updated:** 2026-04-26

## Summary

Update the MPERS template generator to paint dark-navy fill (+ white bold
font) on every row whose underlying concept is XBRL-abstract, mirroring the
hand-curated MFRS template convention. Regenerate all 30 MPERS templates
(2 levels × 15 sheets). Flip the existing `test_mpers_templates_lack_header_fills_known_gap`
xfail-style pin into a positive abstract-rows-found assertion. Add
end-to-end tests proving the writer's abstract-row guard now refuses
header writes on MPERS templates the same way it does on MFRS.

## Key Decisions

- **Fill convention matches MFRS exactly.** Abstract rows: dark navy
  `1F3864` background, white bold font in col A. This is what
  `tools.section_headers._HEADER_FILL_RGB` already detects, so no other
  code change is needed beyond the generator.
- **Pale-blue Total-row fill is OUT OF SCOPE.** MFRS paints `EEF2F8`
  on Total rows for visual consistency, but the abstract-row guard does
  not depend on it. Adding it would expand template diffs and require
  Total-row detection logic in the generator. Defer.
- **Generator detection is via `_is_abstract_concept(concept_id)`** —
  already present at line 379-386. The `(depth, concept_id, label,
  is_abstract)` tuple is already produced; the layout helpers just need
  to consume the 4th element instead of discarding it.
- **Pre-change snapshot is taken manually** (NOT via `--snapshot`).
  CLAUDE.md gotcha #15 says "Always run with --snapshot so the previous
  version lands in backup-originals" but the snapshot function actually
  copies the JUST-EMITTED files (the new ones), not the previous. To
  preserve a real diff baseline we copy the existing templates to a
  separate `backup-originals-pre-fill-paint/` directory before running
  the generator, then run the generator, then run --snapshot to refresh
  the canonical baseline.
- **No prompt or writer changes needed.** The existing abstract-row
  guard in `tools/fill_workbook.py` and the `[ABSTRACT]` marker in
  `_summarize_template` automatically light up on MPERS templates the
  moment they carry the fill.

## TDD Phases

### Phase 1 — RED: pin the desired post-change MPERS state

| Step | Status | Description |
|------|--------|-------------|
| 1.1 | 🟩 | RED→GREEN: `test_mpers_templates_carry_header_fills_like_mfrs` (replaced the gap-pinning xfail-style test). |
| 1.2 | 🟩 | RED→GREEN: `test_abstract_rows_marked_in_mpers_group_sopl`. |
| 1.3 | 🟩 | RED→GREEN: `test_writer_refuses_abstract_writes_on_mpers_sopl_analysis` (used "Other expenses" — same family of catch-all rows as the screenshot bug). |

### Phase 2 — GREEN: update the MPERS generator

| Step | Status | Description |
|------|--------|-------------|
| 2.1 | 🟩 | Manual snapshot taken (15 Company + 15 Group files). |
| 2.2 | 🟩 | Added `_HEADER_FILL_ARGB = "FF1F3864"` + `_apply_abstract_row_styling(cell)` helper. |
| 2.3 | 🟩 | `_apply_company_sheet_layout` consumes `is_abstract` and calls the helper. |
| 2.4 | 🟩 | `_apply_group_sheet_layout` likewise. |
| 2.5 | 🟩 | `_apply_group_socie_layout` likewise. |
| 2.6 | 🟩 | Generator run for both levels (15 + 15 = 30 files). Cell-content diff vs pre-snapshot: **0 label/formula changes** across all 30 templates — pure style change. |

### Phase 3 — Verify + refresh baseline

| Step | Status | Description |
|------|--------|-------------|
| 3.1 | 🟩 | Phase 1 tests now GREEN (3 ex-RED + the existing 16). |
| 3.2 | 🟩 | Full suite: **1259 passed, 2 skipped, 4 deselected, 0 failed**. |
| 3.3 | 🟩 | `--snapshot` run for both levels — `backup-originals/` refreshed. Temporary diff snapshot removed. |
| 3.4 | 🟩 | CLAUDE.md gotcha #17 updated — known-gap subsection replaced with a positive "MPERS parity" note + don't-drop guidance for future generator edits. |
| 3.5 | 🟩 | Memory note converted from gap-tracker to closed-postmortem; MEMORY.md index updated. |

### Phase 4 — Hand-off

| Step | Status | Description |
|------|--------|-------------|
| 4.1 | ⬜ | Live smoke run on Mac with an MPERS PDF (any sample) to confirm the agent's `read_template()` summary now shows `[ABSTRACT]` for MPERS section-header rows. |
| 4.2 | ⬜ | Hand off to user for Windows live smoke. |

## Files Touched

- `scripts/generate_mpers_templates.py` — paint dark-navy fill in 3 layout helpers
- `XBRL-template-MPERS/Company/*.xlsx` — regenerated (15 files)
- `XBRL-template-MPERS/Group/*.xlsx` — regenerated (15 files)
- `XBRL-template-MPERS/backup-originals/*` — refreshed via --snapshot
- `XBRL-template-MPERS/backup-originals-pre-fill-paint/*` — manual diff baseline (kept for audit)
- `tests/test_template_reader.py` — flip xfail-style test to positive
- `tests/test_fill_workbook_abstract_guard.py` — new MPERS end-to-end test
- `CLAUDE.md` — update gotcha #17 to remove the gap note
- `memory/abstract_row_guard_mpers_gap.md` — close out the project memory

## Out of Scope

- Pale-blue Total-row fill (`EEF2F8`) — cosmetic, not load-bearing.
- Any change to the writer / reader / prompts / verifier — they already
  handle MPERS once the templates carry fills.
- MPERS notes templates — verify they don't currently contain abstract
  rows that would render as filled headers (notes are mostly
  text-block concepts; the MPERS notes templates may be unaffected).
