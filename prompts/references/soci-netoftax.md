> **On-demand workflow reference** — the extraction agent loads this with the
> `load_workflow_reference()` tool when it is working this statement. It is REFERENCE
> DEPTH, not the controlling contract: the live statement prompt and `read_template()`
> win on any conflict. The row numbers, column letters, and cell coordinates below are
> an MFRS-Company / FINCO-FY2021 **illustration** — ALWAYS confirm the live row, label,
> and section from `read_template()` before writing. Addressing follows `write_facts`:
> a TEXT `field_label` (+ `section`) for ordinary rows, or explicit `row`/`col` for matrix
> layouts. Never write a cell reference such as "C11" into a `field_label`.

# SOCI-NetOfTax Fill Workflow

**Template:** `06-SOCI-NetOfTax.xlsx`
**Sheets:** `SOCI-NetOfTax` (single sheet, 42 rows)
**Based on:** FINCO FY2021 Audited Financial Statements

---

## Overview Strategy

The Net-of-Tax variant presents OCI items already net of their tax effects. No separate
tax-adjustment section. Simpler than the Before-Tax variant but less detailed.

**Key difference from Before-Tax:** No "Income tax relating to components of OCI" section.
OCI amounts are entered net of tax directly. Fewer rows (42 vs. 48).

**FINCO:** Same as Before-Tax — only profit/loss row filled, all OCI rows blank.

---

## Template Structure Summary

### Single Sheet (`SOCI-NetOfTax`)
- Columns: A (labels), B (current year), C (prior year)
- 11 section headers: Same OCI categories as Before-Tax
- 23 data-entry rows: Same items but amounts are net-of-tax
- 6 formula rows (all SUM/CALC): OCI subtotals
- **No cross-sheet references**

---

## Field-by-Field Mapping Table

Same fields as SOCI-BeforeTax except:
- OCI amounts entered **net of tax** (amount × (1 - tax rate))
- No separate tax rows
- Fewer formula subtotal rows

---

## Common Mistakes / Sign Conventions

1. **Net of tax** — each OCI item should have its tax effect already deducted. If the
   entity has a 24% tax rate and a revaluation gain of RM100, enter RM76.

2. **Same cross-check applies** — TCI must match SOCIE.

3. **Choosing between variants:** If the PDF shows OCI items with tax shown separately
   → use Before-Tax variant. If OCI items are shown net → use Net-of-Tax variant.
   The scout's variant detection should pick this up from the page text.

---

## Worked Example: FINCO FY2021

Identical to SOCI-BeforeTax — FINCO has no OCI items and is tax-exempt.

```json
{"fields": [
  {"sheet": "SOCI-NetOfTax", "field_label": "Profit/(loss) for the financial year", "section": "", "col": 2, "value": -1963112, "evidence": "SOPL, deficit CY"},
  {"sheet": "SOCI-NetOfTax", "field_label": "Profit/(loss) for the financial year", "section": "", "col": 3, "value": 1499074, "evidence": "SOPL, surplus PY"}
]}
```
