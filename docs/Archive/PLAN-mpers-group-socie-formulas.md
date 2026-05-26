# Implementation Plan: MPERS Group SOCIE template — value cells + per-block formulas

**Status:** 🟩 Done (2026-04-25)
**Progress:** 100% — all phases green; full pytest 1236 passed
**Tracking:** xfail seat in
`tests/test_notes_prompt_phase1.py::test_mpers_group_socie_subtracts_dividends_paid`,
prompt fallback in `prompts/socie_mpers.md` (Group manual-sign caveat),
ADR reference: `docs/ADR-002-socie-dividend-sign.md`,
gotcha #15 breadcrumb in `CLAUDE.md`.
**Last Updated:** 2026-04-25

## Summary

`XBRL-template-MPERS/Group/09-SOCIE.xlsx` is currently emitted with column-A
labels only (`max_col=1`, no value cells, no subtotal formulas). The
generator's `scripts/generate_mpers_templates.py::_apply_group_socie_layout`
deliberately writes labels and skips `_inject_sum_formulas`. As a result,
the new dividend-sign rule from ADR-002 ("dividends paid → POSITIVE
because the formula subtracts it") cannot hold on Group MPERS — there
is no formula to subtract anything.

This plan extends the generator so MPERS Group SOCIE emits the same
formula-driven roll-up as MPERS Company SOCIE, repeated across the four
vertical blocks (Group-CY, Group-PY, Company-CY, Company-PY).

When complete:
- Remove the xfail seat (the test will start passing strict).
- Remove the Group-filing manual-sign bullet from `prompts/socie_mpers.md`.
- Update ADR-002's "Known gap" section to document the closure.
- Re-snapshot `XBRL-template-MPERS/backup-originals/Group/09-SOCIE.xlsx`.

## Key Decisions

These need user sign-off **before** implementation begins. They're judgment
calls, not architecture facts.

### D1. Column model: 2-col-per-block (recommended) vs 24-col MFRS mirror

| Option | Pros | Cons |
|---|---|---|
| **A. 2-col-per-block** (col B = value, col D = source per block, mirrors MPERS Company SOCIE) | MPERS calc role 610000 already drives the same formula on Company SOCIE; no new data model; 4 cleanly separated blocks; matches the MPERS Company semantics agents already understand | Loses the per-equity-component breakdown that MFRS Group SOCIE provides; consolidated/parent split lives entirely in row blocks, not columns |
| **B. 24-col MFRS mirror** (issued capital, retained earnings, reserves, … total) | Fully mirrors MFRS Group SOCIE shape, so cross-checks and downstream tooling don't need to branch by standard | Doubles the implementation cost; no MPERS calc-linkbase entries for component-level decompositions (the component split is an MFRS convention, not MPERS); risks inventing taxonomy structure that doesn't exist in the SSM linkbase |

**Recommendation: Option A.** MPERS calc role 610000 produces single-column
formulas (verified against `XBRL-template-MPERS/Company/09-SOCIE.xlsx`); the
4-block × 1-value-column layout is the smallest correct change. Cross-checks
already read col B per gotcha #15.

### D2. Block layout — keep current row structure or expand?

The existing 4-block layout is `(3-25, 27-49, 51-73, 75-97)` with one-line
headers and blank separators. This stays as-is — the only delta is what
goes in columns B-D.

### D3. Where formulas land per block

Per MPERS Company SOCIE (rows 12 + 23):
- `*Total comprehensive income` row → `=1*B<profit>+1*B<oci>`
- `*Total increase (decrease) in equity` row → `=1*B<TCI>+1*B<acq>+1*B<iculs>+-1*B<div>+1*B<issue>+...`

Both rows must land in **each** of the 4 blocks with row offsets shifted to
the block's own range. The closing `Equity at end of period` row stays
formula-free (the agent enters it explicitly per gotcha #12).

### D4. Snapshot strategy

- Run with `--snapshot` once as the final green step.
- Verify the diff against the prior snapshot (col-A only) only changes
  added cells; no row drift; column-A labels byte-identical.

## Pre-Implementation Checklist

- [ ] 🟥 User picks Option A vs Option B for D1
- [ ] 🟥 User confirms no in-flight refactor of `_inject_sum_formulas` or
      `build_template` that would conflict
- [ ] 🟥 User confirms cross-check `socie_to_sofp_equity` MPERS branch
      reads col B at "Equity at end of period" row in each block (verify
      with `cross_checks/checks/*.py` before red phase)

## TDD Rules for This Plan

Same as `docs/PLAN-mpers-pipeline-wiring.md`: red first (failing for the
right reason), green is minimum, full suite green before next step.

## Phase 1 — Pin the new layout in tests (red)  🟩 Done

### Step 1.1 — Strict-pass the existing xfail seat  🟩 Done

**Red:** Remove the `pytest.mark.xfail` decorator from
`test_mpers_group_socie_subtracts_dividends_paid` in
`tests/test_notes_prompt_phase1.py`. Run it; it must fail with the
existing assertion message ("formula near row N does not subtract B<N>").

**Green:** Comes at the end of Phase 3. Don't restore the decorator —
the seat exists to flip to a real failure when the template lands.

### Step 1.2 — Pin the block layout  🟩 Done

**Red:** Add `tests/test_mpers_group_socie_layout.py`. Assertions:

```python
def test_mpers_group_socie_has_value_cells():
    """After regeneration the template has at least column B populated
    in every block (no longer max_col=1)."""
    wb = openpyxl.load_workbook("XBRL-template-MPERS/Group/09-SOCIE.xlsx")
    ws = wb.active
    assert ws.max_column >= 2

def test_mpers_group_socie_has_per_block_subtotal():
    """Each of the 4 blocks must carry a *Total increase (decrease) in
    equity row with a column-B formula referencing dividends within
    its own block."""
    wb = openpyxl.load_workbook("XBRL-template-MPERS/Group/09-SOCIE.xlsx")
    ws = wb.active
    block_ranges = [(3, 25), (27, 49), (51, 73), (75, 97)]
    for start, end in block_ranges:
        # Find the *Total increase (decrease) in equity row in this block
        total_row = next(
            r for r in range(start, end + 1)
            if str(ws.cell(r, 1).value or "").strip().lower()
            .endswith("total increase (decrease) in equity")
        )
        formula = ws.cell(total_row, 2).value
        assert isinstance(formula, str) and formula.startswith("=")
        # Find the dividends row in this block; formula must subtract it
        div_row = next(
            r for r in range(start, end + 1)
            if str(ws.cell(r, 1).value or "").strip().lower() == "dividends paid"
        )
        assert f"-1*B{div_row}" in formula or f"-B{div_row}" in formula
```

**Green:** Phase 3.

### Step 1.3 — Pin the closing-balance row stays formula-free  🟩 Done

**Red:** Add `test_mpers_group_socie_closing_balance_is_blank` — the
`Equity at end of period` row in each block must have no formula in col
B (per gotcha #12, the agent enters this directly).

**Green:** Phase 3.

## Phase 2 — Refactor `_inject_sum_formulas` to accept a row-offset (green)  🟩 Done

The current implementation maps `concept_id → xlsx_row` using
`_FIRST_BODY_ROW + idx`. The 4-block layout needs the same concept to land
on 4 different rows.

### Step 2.1 — Pull `concept_to_rows` build into a helper that takes a base row

**Red:** Add a unit test in `tests/test_mpers_generator.py`:

```python
def test_inject_sum_formulas_accepts_base_row_offset():
    """When called with base_row=B, formulas reference rows starting at B
    (not _FIRST_BODY_ROW)."""
    # Build a minimal ws + 5-row rows + 1-block calc
    # Call _inject_sum_formulas(ws, rows, calc_blocks, base_row=27)
    # Assert formula on the parent lands in row range [27, 27+len(rows)-1]
```

**Green:** Add a `base_row: int = _FIRST_BODY_ROW` parameter to
`_inject_sum_formulas` and use it in the `concept_to_rows` build.

### Step 2.2 — Backwards compatibility

Existing callers (Company + non-SOCIE Group) pass no `base_row` → still
get `_FIRST_BODY_ROW`. No regression in the existing test suite.

## Phase 3 — Wire the SOCIE-specific layout (green)  🟩 Done

### Step 3.1 — Extend `_apply_group_socie_layout` to take calc_blocks

Currently the signature is `(ws, rows)`. Bump to
`(ws, rows, calc_blocks)`. For each of the 4 block ranges, compute
`base_row = block_start + 1` (header takes the first row) and call
`_inject_sum_formulas(ws, truncated_rows, calc_blocks, value_columns=("B",), base_row=base_row)`.

### Step 3.2 — Add value-column header + Source column in row 1

Following MPERS Company SOCIE row 1 (col B = period placeholder, col D =
"Source"). Each block then has B = value, C = blank, D = source.

Open question (defer to user): should each block carry its OWN
period-placeholder row, or a single global row 1? MFRS Group SOCIE puts
the placeholder once in row 1 col B; mirror that.

### Step 3.3 — Update `build_template` SOCIE-Group branch

Pass `calc` through to `_apply_group_socie_layout`. The existing
`elif level == "group" and filename == "09-SOCIE.xlsx":` branch picks up
the new signature.

### Step 3.4 — Run the tests from Phase 1 — they should now pass

`pytest tests/test_mpers_group_socie_layout.py
tests/test_notes_prompt_phase1.py::test_mpers_group_socie_subtracts_dividends_paid -v`

## Phase 4 — Cleanup  🟩 Done

### Step 4.1 — Remove the xfail seat

Delete `test_mpers_group_socie_subtracts_dividends_paid` (or its
`@pytest.mark.xfail` decorator) from `tests/test_notes_prompt_phase1.py`.
The parametrised `test_live_templates_subtract_dividends_paid` should grow
the new template.

### Step 4.2 — Remove the prompt fallback

Delete the "Group filings — manual-sign fallback" bullet from
`prompts/socie_mpers.md`. Update the prompt-presence test in
`tests/test_notes_prompt_phase1.py::test_equity_prompts_follow_dividend_formula_sign`
if the bullet was being asserted on (currently it isn't).

### Step 4.3 — Update ADR-002

Move the "Known gap" section to "Resolved (date)" with a backref to the
PR/commit that closed it.

### Step 4.4 — Update CLAUDE.md gotcha #15

Remove the "MPERS Group SOCIE template gap" bullet (replaced by ADR-002
historical entry).

### Step 4.5 — Re-snapshot

```bash
python3 scripts/generate_mpers_templates.py --level group --snapshot
```

Verify the diff in `XBRL-template-MPERS/backup-originals/Group/` shows
only added cells; no col-A drift.

## Out of Scope

- **MFRS Group SOCIE 24-col equity-component layout for MPERS.** Deferred to
  a separate plan if/when the SSM MPERS calc linkbase grows component-level
  decompositions. Until then, the 2-col-per-block model from MPERS Company
  SOCIE is the closest authentic representation.
- **Cross-check changes.** `socie_to_sofp_equity` already reads col B per
  gotcha #15; no branch update needed.
- **Notes pipeline.** Sheet IDs (10–14 on MFRS / 11–15 on MPERS)
  unchanged.
- **History page / API surface.** Template column count for MPERS Group
  SOCIE goes from 1 to 4. Verify the front-end's template viewer doesn't
  hard-code `max_col`; spot-check via browser before final cleanup.

## Risk Register

| Risk | Mitigation |
|---|---|
| `_inject_sum_formulas` refactor breaks Company / non-SOCIE Group templates | Default `base_row=_FIRST_BODY_ROW` keeps existing call sites byte-identical; full pytest run before merge |
| MPERS calc role 610000 references children outside the 22-row body | `_inject_sum_formulas` already skips unknown concepts ("face → sub" comment at line 758-760); same skip path applies |
| Snapshot diff includes unintended cell-style changes | Always inspect `git diff --stat XBRL-template-MPERS/backup-originals/Group/` and spot-check with `openpyxl` row dumps before committing |
| Front-end template viewer pins `max_col=1` for MPERS Group SOCIE | Search `web/src/` for hard-coded references; expected to be none (the viewer reads the workbook's `max_column` dynamically) |

## Verification Checklist

Before merging:

- [ ] 🟥 `pytest tests/ -v` — full suite green
- [ ] 🟥 `python3 -m pytest tests/test_mpers_generator.py tests/test_mpers_group_socie_layout.py tests/test_notes_prompt_phase1.py -v` — all targeted tests green, no xfails
- [ ] 🟥 Open the regenerated workbook in Excel — formulas evaluate; no `#REF!` or `#NAME?` errors
- [ ] 🟥 Run a real MPERS Group filing end-to-end through the CLI (`python3 run.py <pdf> --standard mpers --level group --statements SOFP SOPL SOCIE`); verify SOCIE balances against SOFP without a correction-agent pass
- [ ] 🟥 Snapshot diff reviewed; only additive changes in
      `XBRL-template-MPERS/backup-originals/Group/09-SOCIE.xlsx`
- [ ] 🟥 ADR-002 "Known gap" section moved to "Resolved (YYYY-MM-DD)"
- [ ] 🟥 CLAUDE.md gotcha #15 breadcrumb deleted
