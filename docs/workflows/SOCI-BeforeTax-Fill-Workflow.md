# SOCI-BeforeTax Fill Workflow

**Template:** `05-SOCI-BeforeTax.xlsx`
**Sheets:** `SOCI-BeforeOfTax` (single sheet, 48 rows)
**Based on:** FINCO FY2021 Audited Financial Statements

---

## Overview Strategy

The Before-Tax variant presents Other Comprehensive Income (OCI) items at their gross
amounts, with income tax on each OCI component shown separately at the bottom. This is
the more detailed of the two SOCI variants.

**FINCO specificity:** FINCO has NO OCI items. The company is a simple CLG entity with
no revaluations, hedging, foreign operations, or FVOCI instruments. The SOCI for FINCO
would contain only the profit/loss line from SOPL, with all OCI rows left blank. Total
comprehensive income = Net profit/loss.

**Single sheet** — no sub-sheet breakdown needed.

---

## Template Structure Summary

### Single Sheet (`SOCI-BeforeOfTax`)
- Columns: A (labels), B (current year), C (prior year)
- 13 section headers: OCI not reclassifiable, OCI reclassifiable (exchange differences,
  cash flow hedges, hedges of net investment, FVOCI), income tax on OCI, attributable splits
- 22 data-entry rows: Profit/loss, revaluation gains, remeasurement of defined benefit,
  FVOCI gains, exchange differences, hedging gains/reclassifications, tax on OCI, attributable
- 11 formula rows (all SUM/CALC): OCI subtotals, total OCI, total comprehensive income
- **No cross-sheet references**

---

## Field-by-Field Mapping Table

| PDF Line Item | Template Field | Template Section | Rule |
|---|---|---|---|
| Net profit/(loss) from SOPL | Profit/(loss) for the financial year | Top of statement | First data-entry row — links to SOPL bottom line |
| Revaluation of PPE/intangibles | Gains/(losses) on revaluation | Not reclassifiable | Only if entity revalues assets |
| Defined benefit remeasurement | Remeasurements of defined benefit liability | Not reclassifiable | Only if entity has DB pension |
| FVOCI equity instrument gains | Fair value changes of financial assets at FVOCI | Not reclassifiable | Only if entity holds FVOCI instruments |
| FX translation differences | Exchange differences on translation | Reclassifiable | Only if entity has foreign operations |
| Cash flow hedge gains/losses | Gains/(losses) on cash flow hedges | Reclassifiable | Only if entity uses hedge accounting |
| Income tax on OCI items | Tax on each OCI component | Income tax section | Gross-up: show tax separately from OCI amounts |
| TCI attributable to owners | Attributable to owners of the parent | Attributable splits | For group accounts only |
| TCI attributable to NCI | Attributable to non-controlling interests | Attributable splits | For group accounts only |

---

## Common Mistakes / Sign Conventions

1. **Profit/loss is the STARTING point** — enter the SOPL bottom line (net profit/loss
   for the year) as the first data row. This is NOT a formula — it's a data-entry cell
   that must match the SOPL figure exactly.

2. **OCI items before tax** — enter gross amounts. The tax effect is entered separately
   in the "Income tax relating to components of OCI" section. Do not net them.

3. **Losses in brackets** — enter negative values for losses (revaluation decreases,
   hedging losses, etc.).

4. **Empty OCI = just profit/loss row filled** — for entities like FINCO with no OCI,
   only the profit/loss row and (optionally) the attributable rows need values. All OCI
   rows stay blank. The formula rows will compute zero OCI and TCI = profit/loss.

5. **Cross-check with SOCIE** — total comprehensive income from SOCI must equal the TCI
   row in SOCIE.

---

## Worked Example: FINCO FY2021

```json
{"fields": [
  {"sheet": "SOCI-BeforeOfTax", "field_label": "Profit/(loss) for the financial year", "section": "", "col": 2, "value": -1963112, "evidence": "SOPL, deficit for the year CY"},
  {"sheet": "SOCI-BeforeOfTax", "field_label": "Profit/(loss) for the financial year", "section": "", "col": 3, "value": 1499074, "evidence": "SOPL, surplus for the year PY"}
]}
```

All OCI rows left blank. Formula-computed total comprehensive income = (1,963,112) CY, 1,499,074 PY.

### Verification

| Check | CY | PY |
|---|---|---|
| Profit/loss from SOPL | (1,963,112) | 1,499,074 |
| Total OCI | 0 | 0 |
| Total comprehensive income | (1,963,112) | 1,499,074 |
| Matches SOCIE movement? | Yes | Yes |
