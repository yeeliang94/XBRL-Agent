# SOFP-CuNonCu Fill Workflow

**Template:** `01-SOFP-CuNonCu.xlsx`
**Sheets:** `SOFP-CuNonCu` (main, 75 rows), `SOFP-Sub-CuNonCu` (sub, 452 rows)
**Based on:** FINCO FY2021 Audited Financial Statements

---

## Overview Strategy

The Current/Non-Current variant splits assets and liabilities into non-current and current
categories. The template has a **main sheet** with high-level line items (many are formulas
pulling from the sub-sheet) and a **sub-sheet** with granular breakdowns.

**Fill order:** Sub-sheet FIRST, then main-sheet data-entry-only cells. The main sheet has
26 cross-sheet formula references (`='SOFP-Sub-CuNonCu'!B{row}`) that auto-sum sub-sheet
totals. Writing to those formula cells would be overwritten when Excel recalculates.

---

## Template Structure Summary

### Main Sheet (`SOFP-CuNonCu`)
- Columns: A (labels), B (current year), C (prior year)
- 10 section headers (blue fill): Assets, Non-current assets, Current assets, Equity and liabilities, Equity, Liabilities, Non-current liabilities, Current liabilities
- 26 data-entry rows (no formula — safe to write)
- 37 formula rows (26 cross-sheet refs + 11 SUM/CALC — do NOT write)
- Labels prefixed with `*` are formula totals — never write directly

### Sub-Sheet (`SOFP-Sub-CuNonCu`)
- 452 rows: 76 section headers, 301 data-entry rows, 73 SUM formulas
- Breakdowns: PPE, investment property, intangibles, investments, receivables (trade/non-trade, current/non-current), derivatives, cash, inventories, issued capital, reserves, borrowings, employee benefits, provisions, payables

---

## Field-by-Field Mapping Table

### Sub-Sheet Mappings (fill these FIRST)

| PDF Line Item | Note | Template Field | Template Section | Rule |
|---|---|---|---|---|
| Office equipment (carrying amount) | 4 | Office equipment, fixture and fittings | Property, plant and equipment | Direct value from note |
| Trade receivables (net of ECL) | 5 | Trade receivables | Current trade receivables | Gross receivables minus ECL allowance |
| Deposits | 5 | Deposits | Current non-trade receivables | From non-financial assets in Note 5 |
| Other receivables | 5 | Other current non-trade receivables | Current non-trade receivables | From financial assets in Note 5 |
| Cash and bank balances | 6 | Balances with Licensed Banks | Cash and cash equivalents | Not "Cash in hand" — note says bank balances |
| Deferred income | 7 | Deferred income | Current non-trade payables | Sub-sheet row, NOT "Contract liabilities" on main |
| Other payables | 8 | Other current non-trade payables | Current non-trade payables | Only the "other payables" line from note |
| Accrued bonus + Accruals | 8 | Accruals | Current non-trade payables | SUM both lines — no separate "accrued bonus" field |

### Main Sheet Mappings (fill AFTER sub-sheet)

| PDF Line Item | Note | Template Field | Template Section | Rule |
|---|---|---|---|---|
| Right-of-use asset | 3 | Right-of-use assets | Non-current assets | Direct — no sub-sheet breakdown for ROU |
| Accumulated fund | — | Retained earnings | Equity | "Accumulated fund" = "Retained earnings" for CLG companies |
| Lease liability (non-current) | 3 | Lease liabilities | Non-current liabilities | Section hint: "non-current liabilities" |
| Lease liability (current) | 3 | Lease liabilities | Current liabilities | Section hint: "current liabilities" — SAME label, different section |

---

## Common Mistakes / Sign Conventions / Sums-and-Splits

1. **"Deferred income" goes to SUB-SHEET, not main sheet "Contract liabilities".** Unless the PDF explicitly labels it as "Contract liabilities" per MFRS 15, use the sub-sheet "Deferred income" field under Current non-trade payables.

2. **"Accrued bonus" has no separate template field.** Sum it with "Accruals" into the single "Accruals" field. Do NOT put it in "Other current non-trade payables".

3. **"Deposits" under receivables → "Deposits" field.** Not "Other current non-trade receivables". The template has a specific Deposits row.

4. **Lease liabilities appear twice** — once under non-current, once under current. Use the `section` hint to disambiguate the identical label.

5. **"Accumulated fund" = "Retained earnings"** — standard mapping for companies limited by guarantee (no share capital).

6. **Cash → "Balances with Licensed Banks"** — when the note says "cash and bank balances" without separating cash-in-hand, use the bank balances field.

7. **All values are positive** (no sign convention issue for SOFP). Negative values in the PDF (shown in brackets) should be entered as negative numbers.

8. **PY values may be restated** — always use the restated column if present.

---

## Worked Example: FINCO FY2021

### Sub-Sheet Entries (col 2 = CY, col 3 = PY)

```json
{"fields": [
  {"sheet": "SOFP-Sub-CuNonCu", "field_label": "Office equipment, fixture and fittings", "section": "property, plant and equipment", "col": 2, "value": 7541, "evidence": "Note 4, carrying amount 31.12.2021"},
  {"sheet": "SOFP-Sub-CuNonCu", "field_label": "Trade receivables", "section": "current trade receivables", "col": 2, "value": 384375, "evidence": "Note 5, 563125 gross - 178750 ECL = 384375"},
  {"sheet": "SOFP-Sub-CuNonCu", "field_label": "Trade receivables", "section": "current trade receivables", "col": 3, "value": 3774428, "evidence": "Note 5, PY net of ECL"},
  {"sheet": "SOFP-Sub-CuNonCu", "field_label": "Deposits", "section": "current non-trade receivables", "col": 2, "value": 7300, "evidence": "Note 5, non-financial assets"},
  {"sheet": "SOFP-Sub-CuNonCu", "field_label": "Balances with Licensed Banks", "section": "cash and cash equivalents", "col": 2, "value": 2551004, "evidence": "Note 6"},
  {"sheet": "SOFP-Sub-CuNonCu", "field_label": "Balances with Licensed Banks", "section": "cash and cash equivalents", "col": 3, "value": 5003869, "evidence": "Note 6, PY"},
  {"sheet": "SOFP-Sub-CuNonCu", "field_label": "Deferred income", "section": "current non-trade payables", "col": 3, "value": 3993750, "evidence": "Note 7, PY only (CY is zero)"},
  {"sheet": "SOFP-Sub-CuNonCu", "field_label": "Other current non-trade payables", "section": "current non-trade payables", "col": 2, "value": 2809, "evidence": "Note 8, other payables line only"},
  {"sheet": "SOFP-Sub-CuNonCu", "field_label": "Other current non-trade payables", "section": "current non-trade payables", "col": 3, "value": 1537, "evidence": "Note 8, PY other payables"},
  {"sheet": "SOFP-Sub-CuNonCu", "field_label": "Accruals", "section": "current non-trade payables", "col": 2, "value": 399113, "evidence": "Note 8, accrued bonus 128400 + accruals 270713"},
  {"sheet": "SOFP-Sub-CuNonCu", "field_label": "Accruals", "section": "current non-trade payables", "col": 3, "value": 276634, "evidence": "Note 8, PY accrued bonus 112569 + accruals 164065"}
]}
```

### Main Sheet Entries

```json
{"fields": [
  {"sheet": "SOFP-CuNonCu", "field_label": "Right-of-use assets", "section": "non-current assets", "col": 2, "value": 191518, "evidence": "Note 3, carrying amount"},
  {"sheet": "SOFP-CuNonCu", "field_label": "Retained earnings", "section": "equity", "col": 2, "value": 2543264, "evidence": "SOFP face, accumulated fund"},
  {"sheet": "SOFP-CuNonCu", "field_label": "Retained earnings", "section": "equity", "col": 3, "value": 4506376, "evidence": "SOFP face, PY accumulated fund (restated)"},
  {"sheet": "SOFP-CuNonCu", "field_label": "Lease liabilities", "section": "non-current liabilities", "col": 2, "value": 160404, "evidence": "Note 3, non-current lease liability"},
  {"sheet": "SOFP-CuNonCu", "field_label": "Lease liabilities", "section": "current liabilities", "col": 2, "value": 36148, "evidence": "Note 3, current lease liability"}
]}
```

### Verification

| Check | CY | PY |
|---|---|---|
| Total assets | 3,141,738 | 8,778,297 |
| Total equity + liabilities | 3,141,738 | 8,778,297 |
| Balanced? | Yes | Yes |
