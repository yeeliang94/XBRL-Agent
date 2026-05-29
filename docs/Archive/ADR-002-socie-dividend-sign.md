# ADR-002: SOCIE / SoRE dividends entered as positive magnitudes

**Status:** Accepted (2026-04-25)
**Supersedes:** Prior `Dividends are NEGATIVE` guidance in
`prompts/socie*.md` and `prompts/_base.md` (pre-2026-04-25).
**Context:** Implemented as part of the post-FINCO-2021 audit
pass; pinned by `tests/test_notes_prompt_phase1.py
::test_live_templates_subtract_dividends_paid` and the
`test_mpers_group_socie_subtracts_dividends_paid` xfail seat.

## Context

The SOCIE and SoRE prompts previously instructed agents to enter
`Dividends paid` as a **negative** magnitude on the assumption
that "dividends reduce equity". For most templates this was
double-counting:

```text
B17 (Dividends paid)  →  agent writes -500
B24 (Total increase / decrease formula)
                       →  =1*B13+1*B15+1*B16+-1*B17+...
                       →  resolves to ... + 500 (sign-flipped twice)
```

The downstream `socie_to_sofp_equity` cross-check then failed
because closing equity was overstated by 2 × dividend.

## Decision

Prompts now instruct agents to enter `Dividends paid` as a
**positive** magnitude when the live workbook formula subtracts
the row, and to verify via `inspect_workbook` (correction agent)
or `read_template()` (face agents) before relying on the rule.

Touched prompts:

- `prompts/_base.md` — sign-convention troubleshooting block
- `prompts/socie.md` — MFRS Company + Group
- `prompts/socie_mpers.md` — MPERS Company + Group (with the
  Group-template manual-sign caveat — see "Known gap" below)
- `prompts/socie_sore.md` — MPERS SoRE variant
- `prompts/correction.md` — correction-agent sign-repair rules

## Formula evidence

`tests/test_notes_prompt_phase1.py
::test_live_templates_subtract_dividends_paid` is parametrised
across the five templates that DO carry the formula:

| Template | Dividend rows | Subtotal column-B formula |
|---|---|---|
| `XBRL-template-MFRS/Company/09-SOCIE.xlsx` | 17, 41 | `B24/B48: ... +-1*B17/...` |
| `XBRL-template-MFRS/Group/09-SOCIE.xlsx` | 17, 41, 65, 89 | `B24/B48/B72/B96: ... +-1*B<dr>/...` |
| `XBRL-template-MPERS/Company/09-SOCIE.xlsx` | 16 | `B23: ... +-1*B16+...` |
| `XBRL-template-MPERS/Company/10-SoRE.xlsx` | 14 | `B15: =1*B12+-1*B14` |
| `XBRL-template-MPERS/Group/10-SoRE.xlsx` | 14 | `B15: =1*B12+-1*B14` |

The test reads each workbook with `openpyxl(data_only=False)`
and asserts that the column-B formula within 10 rows below the
dividends row contains either `-1*B<dr>` or `-B<dr>`. A future
template regeneration that flips the formula direction breaks
the test before it breaks production.

## Resolved (2026-04-25) — MPERS Group SOCIE template

`XBRL-template-MPERS/Group/09-SOCIE.xlsx` was originally
generated with column-A labels only (`max_col=1`), so the
"positive magnitude" rule had nothing to subtract against on
Group MPERS. Closed via
[`docs/PLAN-mpers-group-socie-formulas.md`](PLAN-mpers-group-socie-formulas.md):

- `scripts/generate_mpers_templates.py::_apply_group_socie_layout`
  now wires `_inject_sum_formulas` per block (each block uses a
  `base_row` offset against SOCIE calc role 610000), producing
  formulas of the same shape as MPERS Company SOCIE in all four
  vertical blocks.
- `_inject_sum_formulas` gained a `base_row: int = _FIRST_BODY_ROW`
  parameter (back-compat default keeps every other call site
  byte-identical).
- The strict-xfail seat in `tests/test_notes_prompt_phase1.py`
  was removed; MPERS Group SOCIE joined the parametrised
  `test_live_templates_subtract_dividends_paid` case set.
- The Group-filing manual-sign bullet was removed from
  `prompts/socie_mpers.md` — agents follow the formula-first
  rule on every MPERS layout.

## Consequences

### Positive

- **Cross-checks reconcile.** `socie_to_sofp_equity` no longer
  fails on the double-negation cases that motivated this flip.
- **Sign rule pivots on formula evidence.** Prompts cite the
  workbook formula rather than wording. A template change is
  detectable by the test and by `inspect_workbook` rather than
  by silently mis-extracting a future PDF.
- **Correction agent inherits the same rule.** The new
  `inspect_workbook` tool (added in the same patch) lets the
  correction agent confirm the formula direction before flipping
  a sign — symmetric with how face agents are now expected to
  work.

### Negative

- **Cached SOCIE results from runs prior to 2026-04-25 will not
  reproduce.** Re-running the same PDF will produce a
  sign-corrected workbook; the cached output reflects the
  pre-flip rule.
- **MPERS Group SOCIE relies on a fallback paragraph.** Until
  the template is regenerated, a careful agent reading the
  formula evidence is the only safety net.
