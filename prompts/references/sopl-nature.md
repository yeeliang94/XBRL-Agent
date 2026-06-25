> **On-demand workflow reference** — the extraction agent loads this with the
> `load_workflow_reference()` tool when it is working this statement. It is REFERENCE
> DEPTH, not the controlling contract: the live statement prompt and `read_template()`
> win on any conflict. The row numbers, column letters, and cell coordinates below are
> an MFRS-Company / FINCO-FY2021 **illustration** — ALWAYS confirm the live row, label,
> and section from `read_template()` before writing. Addressing follows `write_facts`:
> a TEXT `field_label` (+ `section`) for ordinary rows, or explicit `row`/`col` for matrix
> layouts. Never write a cell reference such as "C11" into a `field_label`.

# SOPL-Nature Fill Workflow

**Template:** `04-SOPL-Nature.xlsx`
**Sheets:** `SOPL-Nature` (main, 39 rows), `SOPL-Analysis-Nature` (sub, 132 rows)
**Based on:** FINCO FY2021 Audited Financial Statements

---

## Overview Strategy

The Nature-of-Expense variant classifies expenses by their nature: raw materials,
employee benefits, depreciation, etc. Less common in Malaysia but required by some
entities (especially those without clear cost-of-sales functions).

**Key difference from Function variant:** Most P&L lines are entered directly on the
main sheet (23 data-entry rows vs. 17 for Function). The main sheet has NO cross-sheet
references — it is self-contained. The sub-sheet provides optional detailed breakdowns.

**FINCO mapping:** FINCO's simple income/expenditure format maps more naturally to the
Nature variant since the expenditure is not broken by function.

---

## Template Structure Summary

### Main Sheet (`SOPL-Nature`)
- Columns: A (labels), B (current year), C (prior year)
- 8 section headers (same structure as Function)
- 23 data-entry rows: Revenue, Other income, Changes in inventories, Raw materials,
  Employee benefits expense, Depreciation/amortisation, Other expenses, Finance income,
  Finance costs, Tax, Zakat, Discontinued P/L, Attributable splits, EPS
- 6 formula rows (all SUM/CALC, NO cross-sheet refs)
- **No cross-sheet references** — main sheet is standalone

### Sub-Sheet (`SOPL-Analysis-Nature`)
- 132 rows: 17 section headers, 107 data-entry rows, 6 formula rows
- Same revenue/expense category breakdowns as Function sub-sheet

---

## Field-by-Field Mapping Table

### Main Sheet Mappings

| PDF Line Item | Note | Template Field | Template Section | Rule |
|---|---|---|---|---|
| Income (subscription fees) | 9 | Revenue | Continuing operations | Direct — main sheet data-entry |
| Employee costs (portion) | — | Employee benefits expense | Continuing operations | From Note 10 or expenditure breakdown |
| Depreciation - equipment | 4 | *Depreciation and amortisation (FORMULA) | Continuing operations | May need sub-sheet |
| Other operating expenses | 10 | Other expenses | Continuing operations | Residual after employee/depreciation |
| Interest on lease | 3 | Finance costs | Continuing operations | Direct entry |
| Taxation | 11 | Tax expense | Continuing operations | Zero for FINCO |

### Sub-Sheet Mappings

Same as SOPL-Function sub-sheet. Revenue breakdowns, expense categories, director
remuneration — all available in the analysis sub-sheet.

---

## Common Mistakes / Sign Conventions

1. **Expenses are POSITIVE** in both main and sub-sheet. Do not enter negative values
   even though the PDF shows expenditure in brackets.

2. **Employee benefits is a main-sheet data-entry cell** — enter the total employee cost
   directly. Use the sub-sheet for the breakdown (wages, bonus, social security, etc.).

3. **Changes in inventories** — typically zero for service entities like FINCO. Leave blank.

4. **Raw materials consumed** — not applicable for FINCO. Leave blank.

5. **Depreciation/amortisation on the main sheet is a FORMULA** — it sums from sub-entries.
   Enter depreciation values on the sub-sheet instead.

---

## Worked Example: FINCO FY2021

```json
{"fields": [
  {"sheet": "SOPL-Nature", "field_label": "Revenue", "section": "continuing operations", "col": 2, "value": 3871250, "evidence": "Note 9, subscription fees CY"},
  {"sheet": "SOPL-Nature", "field_label": "Revenue", "section": "continuing operations", "col": 3, "value": 3956875, "evidence": "Note 9, PY"},
  {"sheet": "SOPL-Nature", "field_label": "Other expenses", "section": "continuing operations", "col": 2, "value": 5834362, "evidence": "SOPL face, total expenditure (no COGS breakdown available)"},
  {"sheet": "SOPL-Nature", "field_label": "Other expenses", "section": "continuing operations", "col": 3, "value": 2457801, "evidence": "SOPL face, PY expenditure (restated)"},
  {"sheet": "SOPL-Nature", "field_label": "Finance costs", "section": "continuing operations", "col": 2, "value": 6373, "evidence": "Note 3, lease interest"},
  {"sheet": "SOPL-Nature", "field_label": "Tax expense", "section": "continuing operations", "col": 2, "value": 0, "evidence": "Note 11, tax exempt"}
]}
```

---

## Notes

- When the entity discloses expenditure as a single line (like FINCO), map every component
  you CAN identify from the notes (employee benefits, depreciation, audit/CEO fees, ECL) to
  its own row, and only the genuinely-undisclosed remainder to "Other expenses". Never invent
  a residual to force a tie — see the integrity rule in the base prompt.
