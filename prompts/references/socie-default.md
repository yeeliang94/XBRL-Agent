> **On-demand workflow reference** — the extraction agent loads this with the
> `load_workflow_reference()` tool when it is working this statement. It is REFERENCE
> DEPTH, not the controlling contract: the live statement prompt and `read_template()`
> win on any conflict. The row numbers, column letters, and cell coordinates below are
> an MFRS-Company / FINCO-FY2021 **illustration** — ALWAYS confirm the live row, label,
> and section from `read_template()` before writing. Addressing follows `write_facts`:
> a TEXT `field_label` (+ `section`) for ordinary rows, or explicit `row`/`col` for matrix
> layouts. Never write a cell reference such as "C11" into a `field_label`.

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

6. **Dividends paid are entered as POSITIVE magnitudes** (column C, Retained
   earnings). The `Total increase/(decrease) in equity` formula SUBTRACTS the
   dividends row, so a positive input reduces retained earnings — do NOT enter
   them as negative unless the live `read_template()` formula no longer subtracts
   the row. Share buybacks / treasury-share purchases → column D as NEGATIVE
   (that row is ADDED by the formula). This matches `prompts/socie.md` and
   ADR-002; follow the live formula sign for every movement row.

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

SOCIE is filled with **explicit `row`/`col` coordinates** (it is a matrix — label
matching cannot disambiguate the two period blocks). Retained earnings is column C
= `col` 3. Read the live movement-row numbers from `read_template()`; the rows below
are the MFRS-Company illustration (CY profit row 11, PY opening row 30, PY profit
row 35):

```json
{"fields": [
  {"sheet": "SOCIE", "row": 11, "col": 3, "value": -1963112, "evidence": "SOPL, deficit CY → retained earnings (col C)"},
  {"sheet": "SOCIE", "row": 30, "col": 3, "value": 3007302, "evidence": "SOCIE, opening balance 1 Jan 2020 → retained earnings (col C)"},
  {"sheet": "SOCIE", "row": 35, "col": 3, "value": 1499074, "evidence": "SOPL, surplus PY → retained earnings (col C)"}
]}
```

A profit/(loss) follows its **natural sign** — a loss is entered NEGATIVE (above)
because the Total-change formula ADDS the profit/TCI rows, so a loss must decrease
equity. This is the opposite of the dividends row (#6), which is subtracted by the
formula and so goes in positive.

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

## Settled coordinate mechanics

1. **Coordinate-based filling is supported.** `write_facts` takes explicit
   `row`/`col` coordinates (use them for SOCIE — see the worked example above);
   label matching cannot tell the two period blocks apart, so do not use
   `field_label` here.

2. **Writing intersection cells inside a "formula row" is allowed and expected.**
   The formula lives at the row-TOTAL level (it sums across the component columns);
   the individual component-column cells in that row are data-entry. Writing, e.g.,
   row 11 / col 3 (profit × retained earnings) is correct — `read_template()`
   labels the genuinely-protected formula cells, and the writer refuses any write
   to one of those.
