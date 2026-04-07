# SOPL-Function Fill Workflow

**Template:** `03-SOPL-Function.xlsx`
**Sheets:** `SOPL-Function` (main, 40 rows), `SOPL-Analysis-Function` (sub, 138 rows)
**Based on:** FINCO FY2021 Audited Financial Statements

---

## Overview Strategy

The Function-of-Expense variant classifies expenses by their function: cost of sales,
distribution costs, administrative expenses, etc. This is the more common format for
Malaysian companies.

**FINCO specificity:** FINCO's income statement is titled "Statement of Income and
Expenditure" (non-profit entity). It does NOT use cost-of-sales structure — it has
"Income" and "Expenditure" as single lines. The Function variant template expects a
more granular breakdown. Map FINCO's simple format as follows:
- Income → Revenue
- Expenditure → Administrative expenses (FINCO is a service entity with no COGS)

**Fill order:** Sub-sheet (SOPL-Analysis-Function) FIRST for revenue/expense breakdowns,
then main sheet for items not broken down in the analysis.

**IMPORTANT — Broken cross-sheet formulas:** Template 03 has cross-sheet references from
the main sheet to `SOPL-Analysis-Function` that point to wrong columns (E/F instead of
B/C) and wrong rows (section headers instead of totals). The main sheet formula cells
will NOT auto-populate correctly. The agent should fill both sheets independently and
flag that formula verification may not work for SOPL.

---

## Template Structure Summary

### Main Sheet (`SOPL-Function`)
- Columns: A (labels), B (current year), C (prior year)
- 8 section headers: Continuing operations, Discontinued operations, Profit attributable to, Basic EPS, Diluted EPS
- 17 data-entry rows: selling/distribution expenses, admin expenses, research expenses, finance costs, share of P/L associates/JVs, tax expense, zakat, discontinued P/L, attributable splits, EPS
- 13 formula rows: 5 cross-sheet (BROKEN) + 8 SUM/CALC
- Key formula-derived lines: Gross profit (Revenue - CoS), Operating profit, Profit before tax, Profit from continuing ops, Total P/L

### Sub-Sheet (`SOPL-Analysis-Function`)
- 138 rows: 18 section headers, 102 data-entry rows, 16 SUM formulas
- Revenue: goods (broadband, property, construction, F&B, agriculture, oil&gas), services (entertainment, telecom, transport, IT, education, healthcare, shipping), interest income, fee/commission, dividend/rental/royalty
- Cost of sales: inventories, construction, energy, property development
- Other income: deferred income, bad debts recovered, FX gains, disposal gains, impairment reversals, interest, management fees
- Other expenses: auditor remuneration, depreciation/amortisation, disposal losses, rental, royalty
- Employee benefits: wages, bonus, share-based, social security
- Director remuneration: salaries, bonus, benefits-in-kind, fees
- Finance income: related party, other

---

## Field-by-Field Mapping Table

### Main Sheet Mappings

| PDF Line Item | Note | Template Field | Template Section | Rule |
|---|---|---|---|---|
| Income | 9 | *Revenue (FORMULA — do not write) | Continuing operations | Use sub-sheet instead |
| Expenditure | 10 | Administrative expenses | Continuing operations | FINCO has no COGS; all expenditure is admin |
| Taxation | 11 | Tax expense | Continuing operations | Zero for FINCO (tax exempt) |
| (Deficit)/surplus | — | Derived by formula | — | Profit before tax - tax = net profit |

### Sub-Sheet Mappings

| PDF Line Item | Note | Template Field | Template Section | Rule |
|---|---|---|---|---|
| Subscription fees from members | 9 | Fee and commission income | Revenue | FINCO's income is member subscription fees |
| Auditors' remuneration | 10 | Auditors' remuneration - statutory audit | Other expenses | Direct from Note 10 |
| CEO fee | 10 | Directors' fees | Director remuneration | FINCO's CEO fee = director remuneration |
| ECL allowance | 10 | Impairment loss - trade receivables | Other expenses | From Note 10 |
| Depreciation - ROU | 3 | Depreciation of right-of-use assets | Other expenses | From Note 3 |
| Depreciation - equipment | 4 | Depreciation of property, plant and equipment | Other expenses | From Note 4 |
| Accretion of interest on lease | 3 | Interest expense on lease liabilities | Finance costs (main sheet) | From Note 3 lease movement |

---

## Common Mistakes / Sign Conventions

1. **Expenses are POSITIVE in the template** even though they appear in brackets (negative) in the PDF. The template formulas handle the sign. Enter absolute values.

2. **Revenue is a formula on the main sheet** — always use the sub-sheet for revenue breakdowns. The cross-sheet formula is broken but the sub-sheet total is correct.

3. **"Income" in FINCO ≠ "Revenue" generically** — FINCO's income is subscription fees, which maps to "Fee and commission income" on the analysis sub-sheet, not "Revenue from sale of goods".

4. **No cost of sales for FINCO** — leave CoS fields empty. All operating expenditure goes to administrative expenses.

5. **Finance costs on the main sheet** — interest on lease liability (6,373) goes directly to "Finance costs" on the main sheet (data-entry cell), not the sub-sheet.

---

## Worked Example: FINCO FY2021

### Sub-Sheet Entries

```json
{"fields": [
  {"sheet": "SOPL-Analysis-Function", "field_label": "Fee and commission income", "section": "revenue", "col": 2, "value": 3871250, "evidence": "Note 9, subscription fees CY"},
  {"sheet": "SOPL-Analysis-Function", "field_label": "Fee and commission income", "section": "revenue", "col": 3, "value": 3956875, "evidence": "Note 9, subscription fees PY"},
  {"sheet": "SOPL-Analysis-Function", "field_label": "Auditors' remuneration - statutory audit", "section": "other expenses", "col": 2, "value": 5500, "evidence": "Note 10"},
  {"sheet": "SOPL-Analysis-Function", "field_label": "Auditors' remuneration - statutory audit", "section": "other expenses", "col": 3, "value": 5500, "evidence": "Note 10, PY"},
  {"sheet": "SOPL-Analysis-Function", "field_label": "Directors' fees", "section": "director remuneration", "col": 2, "value": 420000, "evidence": "Note 10, CEO fee"},
  {"sheet": "SOPL-Analysis-Function", "field_label": "Directors' fees", "section": "director remuneration", "col": 3, "value": 420000, "evidence": "Note 10, PY CEO fee"}
]}
```

### Main Sheet Entries

```json
{"fields": [
  {"sheet": "SOPL-Function", "field_label": "Administrative expenses", "section": "continuing operations", "col": 2, "value": 5834362, "evidence": "SOPL face, total expenditure CY"},
  {"sheet": "SOPL-Function", "field_label": "Administrative expenses", "section": "continuing operations", "col": 3, "value": 2457801, "evidence": "SOPL face, total expenditure PY (restated)"},
  {"sheet": "SOPL-Function", "field_label": "Finance costs", "section": "continuing operations", "col": 2, "value": 6373, "evidence": "Note 3, accretion of interest on lease"},
  {"sheet": "SOPL-Function", "field_label": "Tax expense", "section": "continuing operations", "col": 2, "value": 0, "evidence": "Note 11, tax exempt"},
  {"sheet": "SOPL-Function", "field_label": "Tax expense", "section": "continuing operations", "col": 3, "value": 0, "evidence": "Note 11, PY tax exempt"}
]}
```

### Verification

| Check | CY | PY |
|---|---|---|
| Revenue | 3,871,250 | 3,956,875 |
| Expenditure (admin + finance) | 5,840,735 | 2,457,801 |
| Profit before tax | (1,969,485) | 1,499,074 |
| Tax | 0 | 0 |
| Net profit | (1,969,485) | 1,499,074 |

**Note:** CY deficit differs slightly from SOFP (1,963,112) because finance costs (6,373) need proper classification. In practice, the agent should reconcile this against the SOCIE movement.

---

## Open Questions

1. The cross-sheet formulas in this template are broken (wrong column/row references). Should the agent attempt to fill the main sheet formula cells directly as data-entry overrides, or leave them and accept that Excel will show errors?
2. FINCO's expenditure is a single lump sum — there's no note breaking it down into admin vs. other categories. A more complex company would have separate lines.
