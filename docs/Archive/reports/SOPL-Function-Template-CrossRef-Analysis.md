# SOPL-Function Template Cross-Reference Analysis

## Context

This analysis documents a broken cross-sheet formula issue found in `XBRL-template-MFRS/03-SOPL-Function.xlsx`. The issue was discovered during a live extraction run where the SOPL agent correctly extracted values from a PDF but the verifier consistently reported an imbalance (`Profit/loss (-11.0) != attribution total (-20,678.0)`) that the agent could not resolve.

This document is intended for the AI agent that refactored the template files according to the SSM MBRS taxonomy, to determine the correct fix.

---

## The Problem

The `SOPL-Function` main sheet has 5 cross-sheet formula cells that reference the `SOPL-Analysis-Function` sub-sheet. **All 5 references point to the wrong column (E/F) and wrong row numbers.** The Analysis sheet only has 3 columns (A, B, C) — columns E and F do not exist. Two of the referenced rows (154, 158) exceed the Analysis sheet's max row (138).

### Broken References in `SOPL-Function`

| SOPL-Function Row | Label | Current Formula (col B) | Current Formula (col C) | Target Cell Content |
|---|---|---|---|---|
| 7 | `*Revenue` | `='SOPL-Analysis-Function'!E60` | `='SOPL-Analysis-Function'!F60` | Row 60 = "Foreign exchange gain" (empty label, col E doesn't exist) |
| 8 | `*Cost of sales` | `='SOPL-Analysis-Function'!E67` | `='SOPL-Analysis-Function'!F67` | Row 67 = "Gain on disposal of joint ventures" (empty label, col E doesn't exist) |
| 10 | `*Other income` | `='SOPL-Analysis-Function'!E110` | `='SOPL-Analysis-Function'!F110` | Row 110 = "Employee benefits expense" (empty label, col E doesn't exist) |
| 14 | `*Other expenses` | `='SOPL-Analysis-Function'!E154` | `='SOPL-Analysis-Function'!F154` | Row 154 doesn't exist (max row = 138) |
| 16 | `*Finance income` | `='SOPL-Analysis-Function'!E158` | `='SOPL-Analysis-Function'!F158` | Row 158 doesn't exist (max row = 138) |

### Result

All 5 cross-sheet references evaluate to `0` (or `None`), meaning:
- Revenue = 0, Cost of sales = 0, Other income = 0, Other expenses = 0, Finance income = 0
- The only non-formula data cell in the upper section is Tax expense (row 21, manually filled by agent)
- `Profit (loss) before tax` = 0, so `Profit (loss)` = 0 - Tax = -11
- Meanwhile `Total profit (loss)` (row 31) = sum of attribution rows = -20,678 (correctly filled)
- Verifier reports permanent imbalance: -11 != -20,678

---

## Analysis Sheet Layout

The `SOPL-Analysis-Function` sheet has the following structure:

- **Columns**: A (labels), B (current year values/formulas), C (prior year values/formulas)
- **No columns D, E, F exist** (`max_column = 3`)
- **Max row**: 138

### Formula/Total Rows in the Analysis Sheet (column B)

| Row | Label | Formula |
|---|---|---|
| 16 | Total revenue from sale of goods | `=1*B8+1*B9+1*B10+1*B11+1*B12+1*B13+1*B14+1*B15` |
| 26 | Total revenue from rendering of services | `=1*B18+1*B19+...+1*B25` |
| 30 | Total interest income | `=1*B28+1*B29` |
| 35 | Total other fee and commission income | `=1*B32+1*B33+1*B34` |
| **40** | **\*Total revenue** | `=1*B16+1*B26+1*B30+1*B55+1*B36+1*B37+1*B38+1*B39` |
| **47** | **\*Total cost of sales** | `=1*B42+1*B43+1*B44+1*B45+1*B46` |
| 63 | Total foreign exchange gain | `=1*B61+1*B62` |
| 68 | Total gain on disposal of subs/assoc/JVs | `=1*B65+1*B66+1*B67` |
| 79 | Total reversal of impairment loss | `=1*B75+1*B76+1*B77+1*B78` |
| **90** | **\*Total other income** | `=1*B49+1*B50+...+1*B89` (26 terms) |
| 95 | \*Total auditor's remuneration | `=1*B93+1*B94` |
| 103 | Total loss on disposal of subs/assoc/JVs | `=1*B120+1*B101+1*B102` |
| 121 | \*Total employee benefits expense | `=1*B111+...+1*B120` |
| 132 | Total director's remuneration | `=1*B123+...+1*B131` |
| **134** | **\*Total other expenses** | `=1*B115+1*B96+1*B97+1*B98+1*B123+1*B104+...+1*B133` |
| **138** | **\*Total finance income** | `=1*B136+1*B137` |

The bolded rows are the section totals that the `SOPL-Function` main sheet should be referencing.

---

## Comparison With the SOPL-Nature Template (Correct)

`04-SOPL-Nature.xlsx` has the same two-sheet design (main sheet + analysis sub-sheet) and its cross-references are **correct**:

| SOPL-Nature Row | Label | Formula | Target |
|---|---|---|---|
| 7 | Revenue | `='SOPL-Analysis-Nature'!B40` | Row 40 = "Total revenue" (formula row) |
| 13 | Employee benefits expense | `='SOPL-Analysis-Nature'!B95` | Row 95 = "Total employee benefits expense, by nature" (formula row) |

Key differences:
- **Nature uses column B** (where the formulas actually are)
- **Nature uses correct row numbers** that land on `*Total` formula rows
- **Function uses column E** (which doesn't exist) at wrong row numbers

---

## Comparison With SOFP Templates (Correct)

Both SOFP variants (`01-SOFP-CuNonCu.xlsx`, `02-SOFP-OrderOfLiquidity.xlsx`) have correct cross-sheet references:

| Example | Formula | Target |
|---|---|---|
| SOFP-CuNonCu B8 | `='SOFP-Sub-CuNonCu'!B39` | Row 39 = "\*Total property, plant and equipment" (formula) |
| SOFP-CuNonCu B9 | `='SOFP-Sub-CuNonCu'!B49` | Row 49 = "\*Total investment properties" (formula) |

Same pattern: column B, correct row numbers pointing to `*Total` rows.

---

## Likely Intended Fix

Based on the pattern from all other working templates, the `SOPL-Function` cross-references should use **column B/C** (where the Analysis formulas are) at the **correct total row numbers**:

| SOPL-Function Row | Label | Broken Formula (B) | Proposed Fix (B) | Proposed Fix (C) |
|---|---|---|---|---|
| 7 | `*Revenue` | `='SOPL-Analysis-Function'!E60` | `='SOPL-Analysis-Function'!B40` | `='SOPL-Analysis-Function'!C40` |
| 8 | `*Cost of sales` | `='SOPL-Analysis-Function'!E67` | `='SOPL-Analysis-Function'!B47` | `='SOPL-Analysis-Function'!C47` |
| 10 | `*Other income` | `='SOPL-Analysis-Function'!E110` | `='SOPL-Analysis-Function'!B90` | `='SOPL-Analysis-Function'!C90` |
| 14 | `*Other expenses` | `='SOPL-Analysis-Function'!E154` | `='SOPL-Analysis-Function'!B134` | `='SOPL-Analysis-Function'!C134` |
| 16 | `*Finance income` | `='SOPL-Analysis-Function'!E158` | `='SOPL-Analysis-Function'!B138` | `='SOPL-Analysis-Function'!C138` |

---

## Additional Note: Possible Formula Issue in `*Total other expenses`

While investigating, I noticed the `*Total other expenses` formula in the Analysis sheet may have structural issues (separate from the cross-ref bug):

```
=1*B115+1*B96+1*B97+1*B98+1*B123+1*B104+1*B105+1*B106+1*B107+1*B108+1*B109+1*B121+1*B152+1*B133
```

Potential concerns:
1. **B115** (Social security contributions) is already included in **B121** (\*Total employee benefits expense) — possible double-counting
2. **B123** (Salaries and other emoluments, under Director's remuneration) is a sub-item, not B132 (Total director's remuneration) — inconsistent with how other sub-totals are aggregated
3. **B152** doesn't exist (max row = 138) — phantom reference
4. **B95** (\*Total auditor's remuneration) is NOT included in this formula, so audit fees filled at B93 never flow into Total other expenses

This may need a separate review against the SSM taxonomy to determine the correct aggregation formula.

---

## Scope of Issue

| Template File | Cross-Sheet Refs | Status |
|---|---|---|
| 01-SOFP-CuNonCu.xlsx | SOFP-CuNonCu → SOFP-Sub-CuNonCu | Correct (col B, valid rows) |
| 02-SOFP-OrderOfLiquidity.xlsx | SOFP-OrdOfLiq → SOFP-Sub-OrdOfLiq | Correct (col B, valid rows) |
| **03-SOPL-Function.xlsx** | **SOPL-Function → SOPL-Analysis-Function** | **BROKEN (col E/F, wrong rows)** |
| 04-SOPL-Nature.xlsx | SOPL-Nature → SOPL-Analysis-Nature | Correct (col B, valid rows) |
| 05 through 14 | No cross-sheet references | N/A |

The issue is isolated to a single template file: `03-SOPL-Function.xlsx`.

---

## Questions for the Template Refactoring Agent

1. Were columns E/F in `SOPL-Analysis-Function` part of an earlier template layout that was removed during refactoring? The row numbers (60, 67, 110, 154, 158) don't match any current label or formula rows.

2. Should the fix simply align with the pattern used by `04-SOPL-Nature.xlsx` (reference column B at the `*Total` formula rows)? Or was there an intentional design difference between the Function and Nature variants?

3. Can you verify the `*Total other expenses` formula aggregation against the SSM MBRS taxonomy? The current formula appears to have double-counting (B115 inside B121) and a phantom reference (B152).
