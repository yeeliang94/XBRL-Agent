# SOCIE Fill Workflow

**Template:** `09-SOCIE.xlsx`
**Sheets:** `SOCIE` (single sheet, 52 rows x 24 columns)
**Based on:** FINCO FY2021 Audited Financial Statements

---

## Overview Strategy

SOCIE is the **hardest template** — it's a matrix where rows represent equity movements
and columns represent equity components. Most cells are formulas (34 formula rows vs.
only 9 data-entry rows). The agent must understand both the row (movement type) and
column (equity component) to place each value correctly.

**FINCO specificity:** FINCO is a CLG company with only one equity component: Accumulated
fund (= Retained earnings). No share capital, no reserves, no NCI. The SOCIE is a single
column with opening balance, movement (surplus/deficit), and closing balance.

**Many cells should stay BLANK.** A typical company has only a few equity components
(share capital, retained earnings, maybe 1-2 reserves). Most of the 24 columns will be
empty. Do NOT fill zeros — leave cells blank where there is no activity.

---

## Template Structure Summary

### Column Layout (B through X)
| Col | Component | Type |
|---|---|---|
| B | Issued capital | Data entry |
| C | Retained earnings | Data entry |
| D | Treasury shares | Data entry |
| E | Capital reserve | Data entry |
| F | Hedging reserve | Data entry |
| G | Foreign currency translation reserve | Data entry |
| H | Reserve of share-based payments | Data entry |
| I | Revaluation surplus | Data entry |
| J | Statutory reserve | Data entry |
| K | Warrant reserve | Data entry |
| L | Other non-distributable reserves | Data entry |
| M | Sub-total non-distributable reserves | **FORMULA: SUM(E:L)** |
| N | Fair value reserve | Data entry |
| O | Reserve of non-current assets held for sale | Data entry |
| P | Consolidation reserve | Data entry |
| Q | Warranty reserve | Data entry |
| R | Other distributable reserves | Data entry |
| S | Sub-total distributable reserves | **FORMULA: SUM(N:R)** |
| T | Reserves total | **FORMULA: M+S** |
| U | Equity attributable to owners | **FORMULA: B+C+D+T** |
| V | Equity other components | Data entry |
| W | Non-controlling interests | Data entry |
| X | Total | **FORMULA: U+V+W** |

### Row Layout (duplicated for two periods)

**Current period (rows 6-25):**
| Row | Movement | Type |
|---|---|---|
| 6 | Equity at beginning of period | FORMULA |
| 7 | Impact of changes in accounting policies | FORMULA |
| 8 | Equity at beginning, restated | FORMULA |
| 11 | Profit/(loss) | FORMULA (SUM across cols) |
| 12 | Other comprehensive income | FORMULA |
| 13 | Total comprehensive income | FORMULA (11+12) |
| 15 | Acquisition/dilution of equity in subsidiaries | FORMULA |
| 16 | ICULS conversion | FORMULA |
| 17 | Dividends paid | FORMULA |
| 18 | Issuance of shares | FORMULA |
| 19 | Convertible notes - equity component | FORMULA |
| 20 | Share-based payment transactions | FORMULA |
| 21 | (Purchase)/disposal of treasury shares | FORMULA |
| 22 | Other transactions with owners | FORMULA |
| 23 | Other changes in equity | FORMULA |
| 24 | Total increase/(decrease) in equity | FORMULA |
| 25 | Equity at end of period | FORMULA |

**Prior period (rows 30-49):** Same structure, rows shifted by 24.

**Data-entry rows:** 3, 28, 29, 33, 34, 38, 50, 51, 52 — these are the prior period's
opening balances and supplementary rows.

### The Critical Insight

Almost ALL movement rows (profit, OCI, dividends, etc.) are FORMULAS that SUM across
their own columns. The agent writes values into the **intersection cells** — the specific
column for the equity component where the movement applies.

For example: Profit of RM 1,499,074 goes into **row 11, column C** (Retained earnings
column, Profit row) for the current period. The formula in row 11 then sums across all
columns to get total profit.

---

## Field-by-Field Mapping Table

The mapping is by **cell coordinate**, not by label, because SOCIE is a matrix:

### For each equity component used by the entity:

| Movement | Row (CY) | Row (PY) | Which Column? | Rule |
|---|---|---|---|---|
| Opening balance | 6 | 30 | Component column | Beginning equity for the period |
| Accounting policy change | 7 | 31 | Component column | Restatement adjustments |
| Restated opening | 8 | 32 | Component column | FORMULA: row 6 + row 7 |
| Profit/(loss) | 11 | 35 | **Retained earnings (C)** | Always goes to retained earnings |
| Other comprehensive income | 12 | 36 | **Relevant reserve column** | E.g., revaluation → column I |
| Total comprehensive income | 13 | 37 | FORMULA | row 11 + row 12 |
| Dividends paid | 17 | 41 | Retained earnings (C) | Reduces retained earnings |
| Share issuance | 18 | 42 | Issued capital (B) | Increases share capital |
| Share-based payments | 20 | 44 | Column H | Reserve of share-based payments |
| Treasury shares | 21 | 45 | Column D | Purchase/disposal |
| Total change | 24 | 48 | FORMULA | Sum of rows 13-23 |
| Closing balance | 25 | 49 | FORMULA | row 8 + row 24 |

---

## Common Mistakes / Sign Conventions

1. **Do NOT write to formula rows** — most rows (6-8, 11-13, 15-25, 30-32, 35-37, 39-49)
   are formulas. Only write to the individual cell intersections that the formulas will
   sum from.

2. **Actually, the data-entry happens at the CELL level** — you write a value to, e.g.,
   cell C11 (retained earnings × profit row). The row-level formula sums C11 across all
   columns to get total profit. The column-level formula sums down column C for total
   retained earnings movement.

3. **Leave blank, don't enter zero** — for equity components the entity doesn't have.
   Entering zero in every cell clutters the output.

4. **Profit always goes to retained earnings column (C)** — even if the entity calls it
   "accumulated fund" or "revenue reserve".

5. **OCI goes to the RELEVANT reserve column** — revaluation gains → column I,
   FX translation → column G, hedging → column F, FVOCI → column N.

6. **Dividends are NEGATIVE** — they reduce retained earnings.

7. **NCI column (W)** — only for group accounts. Single-entity statements leave blank.

8. **Two periods required** — the template has CY (rows 6-25) and PY (rows 30-49).
   Both must be filled. The PY closing balance should equal the CY opening balance.

---

## Worked Example: FINCO FY2021

FINCO has one equity component: Retained earnings (column C).

### Current Period (FY2021)

| Cell | Value | Evidence |
|---|---|---|
| C6 (Opening) | — | FORMULA: pulls from PY closing |
| C11 (Profit/loss) | (1,963,112) | SOPL deficit for the year |
| C25 (Closing) | — | FORMULA: opening + changes |

### Prior Period (FY2020)

| Cell | Value | Evidence |
|---|---|---|
| C30 (Opening) | 3,007,302 | Per SOCIE in PDF, as at 1 Jan 2020 |
| C35 (Profit/loss) | 1,499,074 | SOPL surplus PY (restated) |
| C49 (Closing) | — | FORMULA: 3,007,302 + 1,499,074 = 4,506,376 |

```json
{"fields": [
  {"sheet": "SOCIE", "field_label": "C11", "col": 3, "value": -1963112, "evidence": "SOPL, deficit CY → retained earnings column"},
  {"sheet": "SOCIE", "field_label": "C30", "col": 3, "value": 3007302, "evidence": "SOCIE, opening balance 1 Jan 2020"},
  {"sheet": "SOCIE", "field_label": "C35", "col": 3, "value": 1499074, "evidence": "SOPL, surplus PY → retained earnings column"}
]}
```

**Note:** SOCIE filling is coordinate-based, not label-based. The fill_workbook tool may
need a coordinate mode for SOCIE, or the prompt must instruct the agent to identify the
correct column for each equity component and use that column letter in the field mapping.

### Verification

| Check | Amount |
|---|---|
| PY opening (1 Jan 2020) | 3,007,302 |
| PY movement (surplus) | 1,499,074 |
| PY closing = CY opening | 4,506,376 |
| CY movement (deficit) | (1,963,112) |
| CY closing | 2,543,264 |
| Matches SOFP equity? | Yes (2,543,264) |

---

## Open Questions

1. **Coordinate-based filling:** The current `fill_workbook` tool matches by label in
   column A. SOCIE rows share the same labels across two period blocks. The agent may
   need to specify exact cell coordinates (e.g., C11, C35) rather than label+section.
   This likely requires a fill_workbook enhancement or a SOCIE-specific fill strategy.

2. **Which rows are truly data-entry?** The template analysis shows only 9 data-entry
   rows, but the agent needs to write to specific cells within formula rows. This means
   the agent writes to individual cells in the matrix, and the formula structure
   aggregates them. The tool must allow writing to non-formula cells within formula rows
   (the formula is at the row total level, individual column cells may be data-entry).
