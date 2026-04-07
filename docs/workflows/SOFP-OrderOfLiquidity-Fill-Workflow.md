# SOFP-OrderOfLiquidity Fill Workflow

**Template:** `02-SOFP-OrderOfLiquidity.xlsx`
**Sheets:** `SOFP-OrdOfLiq` (main, 53 rows), `SOFP-Sub-OrdOfLiq` (sub, 310 rows)
**Based on:** FINCO FY2021 Audited Financial Statements

---

## Overview Strategy

The Order-of-Liquidity variant presents assets and liabilities ranked by liquidity (most
liquid first) WITHOUT current/non-current splits. This variant is used by financial
institutions (banks, insurance companies) where the current/non-current distinction is
less meaningful.

**Key difference from CuNonCu:** No current/non-current section headers. The main sheet
has NO cross-sheet formula references — it is standalone. The sub-sheet provides optional
breakdowns but the main sheet does not auto-pull from it.

**Fill order:** Both sheets can be filled independently. Fill main sheet for face values,
sub-sheet for note breakdowns where available.

---

## Template Structure Summary

### Main Sheet (`SOFP-OrdOfLiq`)
- Columns: A (labels), B (current year), C (prior year)
- 6 section headers: Assets, Equity, Liabilities
- 35 data-entry rows, 10 SUM/CALC formula rows
- **No cross-sheet references** — main sheet totals are self-contained
- Labels use `[abstract]` suffix for section groupings

### Sub-Sheet (`SOFP-Sub-OrdOfLiq`)
- 310 rows: 51 section headers, 239 data-entry rows, 18 formula rows
- Same breakdowns as CuNonCu but consolidated (no current/non-current split)

---

## Field-by-Field Mapping Table

### Main Sheet Mappings

| PDF Line Item | Note | Template Field | Template Section | Rule |
|---|---|---|---|---|
| Cash and bank balances | 6 | Cash and cash equivalents | Assets | Most liquid first |
| Receivables (net) | 5 | Trade and other receivables | Assets | Combined trade + non-trade |
| Office equipment | 4 | Property, plant and equipment | Assets | Direct value |
| Right-of-use asset | 3 | Right-of-use assets | Assets | Direct value |
| Accumulated fund | — | Retained earnings | Equity | "Accumulated fund" = "Retained earnings" |
| Other payables and accruals | 8 | Trade and other payables | Liabilities | Combined |
| Deferred income | 7 | Contract liabilities | Liabilities | Or "Deferred income" if available |
| Lease liability (total) | 3 | Lease liabilities | Liabilities | Combined NC + C (no split in this variant) |

### Sub-Sheet Mappings (where note breakdowns available)

Same as CuNonCu sub-sheet mappings but without current/non-current section disambiguation.
The sub-sheet field labels are consolidated — e.g., single "Trade receivables" field instead
of separate current/non-current versions.

---

## Common Mistakes / Sign Conventions

1. **No current/non-current split** — lease liabilities are a single combined amount (160,404 + 36,148 = 196,552).
2. **Main sheet is standalone** — unlike CuNonCu, writing to the main sheet does NOT get overwritten by sub-sheet formulas.
3. **Section hints are simpler** — just "assets", "equity", "liabilities" (no "non-current assets" etc.).

---

## Worked Example: FINCO FY2021

```json
{"fields": [
  {"sheet": "SOFP-OrdOfLiq", "field_label": "Cash and cash equivalents", "section": "assets", "col": 2, "value": 2551004, "evidence": "Note 6"},
  {"sheet": "SOFP-OrdOfLiq", "field_label": "Trade and other receivables", "section": "assets", "col": 2, "value": 391675, "evidence": "Note 5, total receivables"},
  {"sheet": "SOFP-OrdOfLiq", "field_label": "Property, plant and equipment", "section": "assets", "col": 2, "value": 7541, "evidence": "Note 4"},
  {"sheet": "SOFP-OrdOfLiq", "field_label": "Right-of-use assets", "section": "assets", "col": 2, "value": 191518, "evidence": "Note 3"},
  {"sheet": "SOFP-OrdOfLiq", "field_label": "Retained earnings", "section": "equity", "col": 2, "value": 2543264, "evidence": "SOFP face, accumulated fund"},
  {"sheet": "SOFP-OrdOfLiq", "field_label": "Lease liabilities", "section": "liabilities", "col": 2, "value": 196552, "evidence": "Note 3, NC 160404 + C 36148"},
  {"sheet": "SOFP-OrdOfLiq", "field_label": "Trade and other payables", "section": "liabilities", "col": 2, "value": 401922, "evidence": "Note 8"},
  {"sheet": "SOFP-OrdOfLiq", "field_label": "Contract liabilities", "section": "liabilities", "col": 3, "value": 3993750, "evidence": "Note 7, deferred income PY"}
]}
```
