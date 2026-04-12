# Handoff: XBRL Template Formula Fixes

**Date:** 2026-04-05
**Author:** Previous agent session
**For:** Next AI agent picking up this task
**Status:** Analysis complete, fixes not yet implemented

---

## Context

### Project goal

Build an AI agent that extracts financial tables from Malaysian company audited financial statement PDFs and fills them into Excel templates. The filled Excel is a human-in-the-loop deliverable: the user manually keys values from it into SSM's MTool (M2) software, which then generates the XBRL instance for SSM filing.

Because MTool locks formula cells and only permits manual entry of non-calculated cells, byte-level identity between our templates and MTool's templates is not required. What IS required: **our templates' formulas must produce the same numerical answers MTool produces**, so the extraction agent can self-validate and self-correct.

### Scope of this task

- The 14 Excel templates in `XBRL-template-MFRS/` (SOFP, SOPL, SOCI, SOCF, SOCIE, Notes).
- User priority: **SOCIE (09-SOCIE.xlsx)** — deep dive requested.
- Other 13 templates: flag material discrepancies only.
- Reference for "correct" behaviour: `data/MBRS_test.xlsx` — an actual MTool workbook with 32 sheets including all the statement templates plus MTool's system sheets (`StartUp`, `Data`, `+Lineitems`, `+FootnoteTexts`, `+Elements`, `MainSheet`).
- Reference for taxonomy: `SSMxT_2022v1.0/` — SSM 2022 taxonomy (IFRS core + SSM MFRS extension).

### User's latest clarifications (in order)

1. First read the SSM taxonomy and check our templates' labels/formulas/ordering against it — done, no material taxonomy-level defects found.
2. Pivot: not generating XBRL directly; user keys values into MTool which generates XBRL.
3. Compare our templates against `MBRS_test.xlsx` (MTool's actual Excel) and identify what to amend so numbers match.
4. **Row/column offsets don't matter** — the user keys values cell-by-cell into MTool, so coordinate alignment is irrelevant.
5. What DOES matter: **formulas must be correct so the extraction agent can validate its own output and self-correct** when it gets totals wrong.

---

## Key findings from the analysis

### Findings from taxonomy audit (completed earlier in session)

- All 14 templates are taxonomy-conformant. SOCIE has all 23 `ComponentsOfEquityAxis` members the SSM MFRS role-610000 defines. No material label/ordering discrepancies found against `SSMxT_2022v1.0/rep/ssm/ca-2016/fs/mfrs/`.
- The full IFRS core taxonomy has ~30+ equity reserve members SSM deliberately does not surface. Users shove these into SSM's 3 catch-all buckets: **L (Other non-distributable)**, **R (Other distributable)**, **V (Equity, other components)**.
- Taxonomy audit was written to `XBRL-Template-Taxonomy-Audit.md` in the workspace.

### Findings from MTool comparison (completed)

Structural differences (IGNORE per user):
- MTool SOCIE starts data at row 27, ours at row 6 — 21-row offset.
- MTool SOCIE equity columns live in E–AA (24 cols, incl. spacer at C and label at D), ours in B–X (23 cols).
- All 23 equity-component labels match 1:1 in the same order.
- Row labels match 1:1 in the same order.
- MTool has system sheets we don't need.

Formula differences (ACT ON):

**MTool's SOCIE formula patterns:**
- Restated row (R29): `P29 = SOCIE!P27 + SOCIE!P28` — per-column Opening + Impact.
- Total comprehensive income (R34): `P34 = 1*P32 + 1*P33` — per-column Profit + OCI, coefficient-prefixed.
- Total increase/(decrease) in equity (R45): per-column with explicit `1*` and `-1*` coefficients on change rows. Example:
  ```
  P45 = 1*P34 + 1*P36 + 1*P37 + -1*P38 + 1*P39 + 1*P40 + 1*P41 + 1*P42 + 1*P43 + 1*P44
  ```
  The **`-1*P38` (Dividends paid)** is the critical coefficient — dividends reduce equity.
- Equity at end of period (R46): `P46 = SOCIE!P29 + SOCIE!P45` — per-column Restated + Total increase.
- MTool does NOT have horizontal SUM formulas on data-entry rows. Subtotal columns (P, V, W, X, AA) are free-entry; their roll-up comes from the `1*` / `-1*` vertical arithmetic above.

**Our 09-SOCIE.xlsx current formula pattern:**
- `M6 = SUM(E6:L6)` subtotal non-distributable — horizontal sum on EVERY data row (rows 6–25).
- `S6 = SUM(N6:R6)` subtotal distributable.
- `T6 = M6 + S6` total reserves.
- `U6 = B6+C6+D6+T6` equity to owners.
- `X6 = U6+V6+W6` total equity.
- Missing: "Total increase (decrease) in equity" row formula. Missing: end-of-period = restated + total-increase formula. Missing: any `-1*` handling for Dividends paid. Missing: Total comprehensive income aggregation formula.
- R8 (Restated) has per-column `=R6+R7` — this matches MTool ✓.

### Bugs identified in our SOCIE (self-validation impact)

| # | Severity | Bug | Impact on self-validation |
|---|---|---|---|
| 1 | HIGH | Dividends paid sign not handled (no `-1*` coefficient anywhere) | Equity at end of period overstated by 2× dividend amount |
| 2 | HIGH | No formula for "Total increase (decrease) in equity" row | Agent must compute this manually; no cross-check possible |
| 3 | HIGH | No formula for "Equity at end of period" row | Closing balance can't be validated against opening + changes |
| 4 | MEDIUM | Horizontal SUM formulas overwrite subtotal columns on every row | If PDF reports a subtotal directly without the breakdown, agent can't enter it |
| 5 | MEDIUM | No "Total comprehensive income" aggregation formula (Profit + OCI) | Agent must compute this manually |
| 6 | MEDIUM | No cross-sheet validation against SOFP prior-year equity | Can't catch extraction errors at the opening-balance stage |
| 7 | LOW | No hidden validation cells per column | No programmatic way for agent to detect sign/addition errors |

### Other sheets (NOT yet diffed against MTool)

Only SOCIE was line-by-line compared. Row labels on SOFP-CuNonCu were spot-checked and match MTool exactly (just offset by 21 rows). Formulas on the other 13 sheets have NOT been compared — same methodology must be applied.

Taxonomy-level audit (completed, see `XBRL-Template-Taxonomy-Audit.md`) found these sheets clean at the label/ordering level:
- 01-SOFP-CuNonCu (2 sheets, 74 formulas) — aggregates from sub-sheet
- 02-SOFP-OrderOfLiquidity (2 sheets, 20 formulas)
- 03-SOPL-Function (2 sheets, 26 formulas)
- 04-SOPL-Nature (2 sheets, 12 formulas)
- 05-SOCI-BeforeTax (1 sheet, 22 formulas)
- 06-SOCI-NetOfTax (1 sheet, 12 formulas)
- 07-SOCF-Indirect (1 sheet, 28 formulas)
- 08-SOCF-Direct (1 sheet, 4 formulas) — caution, very few formulas
- 10–14 Notes (0 formulas, narrative disclosure sheets)

---

## Action plan for the next agent

### Step 1 — Fix 09-SOCIE.xlsx (priority file)

Read `XBRL-template-MFRS/09-SOCIE.xlsx` and `data/MBRS_test.xlsx`. Build a row-label → concept mapping between the two sheets (they share identical labels, just at different row numbers). Then for each period block in our SOCIE (rows 6–25 first period, then 27–49 second period, etc.):

1. **Drop horizontal SUM formulas on subtotal columns M, S, T, U, X for change-in-equity rows.** Keep them ONLY on the "Equity at beginning of period" row and "Equity at end of period" row so opening and closing totals are cross-checked horizontally, but leave change rows as free-entry.

2. **Add the "Total comprehensive income" formula** on the row with label `*Total comprehensive income` (our R13, R34, etc.):
   ```
   For each column X in [B..X]: X_thisrow = X_profitLossRow + X_OCIRow
   ```

3. **Add the "Total increase (decrease) in equity" formula** on the row with label `*Total increase (decrease) in equity` (our R24, R48, etc.):
   ```
   For each column X: X_thisrow = 1*X_TotalComprehensive + 1*X_Acquisition + 1*X_ICULS
                                 + -1*X_DividendsPaid
                                 + 1*X_IssuanceOfShares + 1*X_ConvertibleNotes
                                 + 1*X_SBP + 1*X_TreasuryTxns + 1*X_OtherOwnerTxns
                                 + 1*X_OtherChanges
   ```
   Read the specific row numbers from MBRS_test's formula (`P45` formula in MTool) and apply the same per-column pattern. The `-1*` on Dividends paid is mandatory.

4. **Add the "Equity at end of period" formula** on the row with label `*Equity at end of period` (our R25, R49, etc.):
   ```
   For each column X: X_thisrow = X_restatedRow + X_totalIncreaseRow
   ```

5. **Add hidden validation cells** in a side block (e.g. columns AA–AX, rows 6+) that compute:
   - `closing - (opening + total_increase)` per column — should equal zero.
   - Opening balance of column X at current year = closing balance of column X at prior year.
   Your agent can scan these cells at the end of extraction to auto-detect errors.

6. **Verify fixes** by loading the modified workbook in openpyxl with `data_only=False`, walking every formula cell, and confirming:
   - Every change row has the `-1*` coefficient on DividendsPaid column references.
   - Every closing-balance cell references restated + total-increase.
   - Every subtotal column still has horizontal SUM at opening and closing rows only.

### Step 2 — Apply the same formula diff to the other 13 templates

For each of templates 01–08 (the statements with formulas), do the same MBRS_test diff:

1. Open `data/MBRS_test.xlsx` and find the corresponding sheet (names: `SOFP-CuNonCu`, `SOFP-Sub-CuNonCu`, `SOPL-Function`, `SOPL-Analysis-Function`, `SOCI-BeforeOfTax`, `SOCF-Indirect` — note MTool may not have separate sheets for all our variants).
2. Extract MTool's formulas for every non-blank cell in the data-entry block.
3. Diff against our template's formulas at the matching row label.
4. Report discrepancies (missing formulas, wrong sign coefficients, wrong summation patterns).
5. Patch our template.

**Expected findings per sheet (hypotheses to verify):**

- **SOFP-CuNonCu / SOFP-OrderOfLiquidity**: MTool likely has `Total non-current assets = sum of items`, `Total current assets = sum of items`, `Total assets = Total non-current + Total current`, `Total equity and liabilities = sum of equity + sum of liabilities`, `Total assets = Total equity and liabilities` (balance check). Verify sign on Treasury shares — should be subtraction from equity.
- **SOPL-Function / SOPL-Nature**: `Gross profit = Revenue - Cost of sales`. `Profit before tax = operating items summed`. `Profit for period = Profit before tax - Tax expense`. Verify: are expenses entered as positive numbers and subtracted in formula, or entered as negatives and added?
- **SOCI-BeforeTax / SOCI-NetOfTax**: `Total comprehensive income = Profit/loss + OCI total`. OCI items summed per section (items that will not be reclassified, items that will be reclassified).
- **SOCF-Indirect**: `Net cash from operating = Profit adjusted for non-cash items ± working capital changes ± taxes paid`. `Net increase in cash = Operating + Investing + Financing`. `Cash at end = Cash at beginning + Net increase`. Check signs on cash outflows (e.g. purchase of PPE, dividends paid, loan repayment should be `-1*`).
- **SOCF-Direct**: Only 4 formulas today — might be missing all the section subtotals. Compare to MTool carefully.

### Step 3 — Cross-sheet validation formulas

Add a validation sheet or hidden cells that enforce:

- `SOFP!Total equity == SOCIE!Equity at end of period (Total column X/AA)`
- `SOFP!Profit/loss for the year (current retained earnings - prior retained earnings, adj) == SOPL!Profit for period`
- `SOPL!Profit for period == SOCI!Profit (loss)` (top of SOCI)
- `SOCI!Total comprehensive income == SOCIE!Total comprehensive income row, Total column`
- `SOCF!Cash at end of period == SOFP!Cash and cash equivalents`

When any of these is non-zero, the agent knows to re-examine the source PDF.

### Step 4 — Update agent tools

Once the templates are fixed:

1. **`tools/fill_workbook.py`**: Update `SECTION_HEADERS` or label-matching logic if any labels changed (they shouldn't have — only formulas should change).
2. **`tools/verifier.py`**: Extend from SOFP balance check to:
   - All balance-sheet-like identities per statement (SOFP total assets = total equity+liab, SOCIE closing = opening + changes, SOCF cash-at-end reconciliation).
   - Cross-sheet identities (see Step 3).
   - Per-column SOCIE consistency checks.
3. **System prompt in `agent.py`**: Add guidance on:
   - Sign conventions: "Enter all values as they appear on the statement. The template's formulas handle sign conversion (dividends are subtracted, expenses are subtracted)."
   - SOCIE routing rules for "Other" columns: Share premium → L, Capital redemption reserve → L, General reserve → R, Remeasurements of defined benefit plans → V (see taxonomy audit doc for full list).

### Step 5 — Test on existing extraction output

1. Run the agent against the FINCO PDF (`data/FINCO-Audited-Financial-Statement-2021.pdf`).
2. Open the filled workbook in Excel (not openpyxl) so formulas evaluate.
3. Verify every validation cell is zero or within rounding tolerance.
4. Compare key totals against the FINCO reference file.
5. Fix any remaining logic bugs.

---

## Files to reference

| Path | Why |
|---|---|
| `data/MBRS_test.xlsx` | MTool's authoritative template. Source of truth for formula patterns. |
| `XBRL-template-MFRS/09-SOCIE.xlsx` | SOCIE — PRIORITY fix target |
| `XBRL-template-MFRS/01-SOFP-CuNonCu.xlsx` through `14-Notes-RelatedParty.xlsx` | Other 13 templates to diff and fix |
| `XBRL-Template-Taxonomy-Audit.md` | Earlier audit — confirms labels/ordering are taxonomy-correct |
| `HANDOFF-Template-Formula-Fixes.md` | This file |
| `SSMxT_2022v1.0/rep/ssm/ca-2016/fs/mfrs/cal_ssmt-fs-mfrs_2022-12-31_role-*.xml` | SSM calculation linkbases — secondary source for which sums must hold |
| `data/FINCO-Audited-Financial-Statement-2021.pdf` | Test input for validation |
| `CLAUDE.md` | Project instructions — pydantic-ai version constraints, Windows UTF-8, proxy setup |

---

## Quick helper snippets

### Read MTool formulas for a given sheet/row range

```python
from openpyxl import load_workbook
wb = load_workbook("data/MBRS_test.xlsx", data_only=False)
ws = wb["SOCIE"]
for r in range(27, 47):
    for c in range(3, 30):
        cell = ws.cell(r, c)
        if cell.value and isinstance(cell.value, str) and cell.value.startswith("="):
            print(f"{cell.coordinate}: {cell.value}")
```

### Build row-label map between MTool and our template

```python
from openpyxl import load_workbook
m = load_workbook("data/MBRS_test.xlsx", data_only=False)["SOCIE"]
t = load_workbook("XBRL-template-MFRS/09-SOCIE.xlsx", data_only=False)["SOCIE"]

# MTool labels in col D
mtool_map = {m.cell(r, 4).value: r for r in range(25, 85) if m.cell(r, 4).value}
# Our labels in col A
ours_map = {t.cell(r, 1).value: r for r in range(3, 60) if t.cell(r, 1).value}

# Align by label
for label, mrow in mtool_map.items():
    if label in ours_map:
        print(f"{label!r}: MTool R{mrow} -> Ours R{ours_map[label]}")
```

### Write new formulas into our template (pattern)

```python
from openpyxl import load_workbook
wb = load_workbook("XBRL-template-MFRS/09-SOCIE.xlsx")
ws = wb["SOCIE"]

# Example: Equity at end of period R25 = R8 (restated) + R24 (total increase) per column
for col in range(2, 25):  # B through X
    col_letter = ws.cell(1, col).column_letter
    ws.cell(25, col).value = f"={col_letter}8+{col_letter}24"

wb.save("XBRL-template-MFRS/09-SOCIE.xlsx")
```

---

## What "done" looks like

- All 14 templates have formulas that produce the same numerical outputs as MTool for any valid input.
- The extraction agent can complete a fill, evaluate the workbook, and flag any non-zero validation cells as errors to investigate.
- Sign conventions are documented and consistent (extracted values entered as shown on statements; formulas handle signs).
- `tools/verifier.py` runs the full cross-sheet validation suite end-to-end and returns a structured error report.
- A clean run of the FINCO PDF produces zero validation failures and totals that match the reference file within rounding tolerance.

---

## Implementation Status (2026-04-06)

**Steps 1–3 of the action plan are COMPLETE.** Step 4 (agent tool updates) and Step 5 (end-to-end test) remain.

### Step 1 — Fix 09-SOCIE.xlsx 🟩 Done

- **Dividends paid sign**: Changed `+B17` to `+-1*B17` on rows 24 and 48 (both periods). All 18 data columns (B–L, N–R, V, W) now use `1*` / `-1*` weighted-sum pattern matching MTool.
- **Dangling rows 50–52**: Cleared (were orphan label duplicates with no formulas or data).
- Horizontal subtotal formulas (M, S, T, U, X) kept as-is — they enable self-validation by the extraction agent.
- Restated (row 8/32), Total comprehensive income (row 13/37), and Equity at end (row 25/49) formulas were already correct. No changes needed.

### Step 2 — Fix templates 01–08 🟩 Done

| Template | Bugs Found & Fixed |
|---|---|
| **01-SOFP-CuNonCu** | B75 Total equity+liabilities referenced B67 (trade payables) + B94 (nonexistent) → fixed to B47+B74. B71/B73 referenced B85 (nonexistent) → fixed to B64 (employee benefit liabilities). |
| **02-SOFP-OrderOfLiquidity** | Missing formulas for Total assets (row 27), Total equity attrib to owners (row 34), Total equity (row 37), Total equity+liabilities (row 53). All added. Treasury shares subtracted with `-1*`. |
| **03-SOPL-Function** | ✓ All formulas matched MTool exactly. No changes. |
| **04-SOPL-Nature** | 7 broken formulas: row 12 was summing income rows instead of depreciation (converted to data-entry); row 15 was `=B14` instead of full operating profit formula; row 18 was summing finance items instead of being data-entry for associates; rows 19, 22, 25, 30, 35 either missing or wrong. All fixed with proper accounting logic and `-1*` sign conventions. **No MTool reference exists for this template** — fixed from accounting principles (IAS 1 by-nature classification). |
| **05-SOCI-BeforeTax** | ✓ All formulas matched MTool exactly. No changes. |
| **06-SOCI-NetOfTax** | B11 included P&L in an OCI subtotal (removed, made data-entry). Rows 37 (Total OCI) and 38 (Total comprehensive income) had no formulas — added. **No MTool reference exists.** |
| **07-SOCF-Indirect** | 5 wrong cross-references: B58 pointed to B77 (investing section) instead of B57 (total adjustments); B67 was `=B66` instead of `B58+B66`; B75 referenced B87 (investing) instead of B67; B128 referenced B95 (purchase of intangibles) and B147 (nonexistent) instead of B75+B107+B127; B130 was `=B129` instead of `B128+B129`. All fixed to match MTool's calculation chain. |
| **08-SOCF-Direct** | Only 2 formulas existed (B79 summing the entire sheet, B82). Added 6 section subtotals: Net operating (row 20), Net investing (row 52), Net financing (row 72), Net increase before FX (row 73), Net increase after FX (row 75), Cash at end (row 77). Fixed B79 to data-entry and B82 to `cash - overdraft + adjustments`. **No MTool reference exists.** |

### Step 3 — Cross-sheet validation & verifier extension 🟩 Done

**`tools/verifier.py` now has per-statement balance checks:**
- SOFP: Total assets == Total equity and liabilities (unchanged)
- SOCIE: Closing equity == Restated opening + Total increase (per column X)
- SOCF: Cash at end == Cash at beginning + Net increase after FX; Operating + Investing + Financing == Net increase before FX
- SOPL: P&L == attribution total (handles both Function and Nature label variants)
- SOCI: Total comprehensive income == P&L + Total OCI; attribution check

**New `verify_cross_sheet()` function** checks inter-statement identities:
- SOFP equity == SOCIE closing equity
- SOPL P&L == SOCI P&L
- SOCF cash at end == SOFP cash

**Formula evaluator fixes** (found during peer review):
- Added `SUM()` with range expansion — `=SUM(E6:L6)` now correctly resolves all 8 cells, not just E6+L6
- All verifiers now fail closed when required labels are missing (return `is_balanced=False` with explicit mismatch messages, not vacuous `True`)
- Cross-sheet label lookup uses exact-match-first to avoid "total equity" resolving to "total equity attributable to owners"

**Cross-check modules updated:**
- `cross_checks/sopl_to_socie_profit.py` and `cross_checks/soci_to_socie_tci.py` now use SOCIE column X (Total) when NCI data is present, falling back to column C (Retained earnings) for single-entity companies.

### Step 4 — Update agent tools 🟨 Not started

Still needed per original plan:
1. `tools/fill_workbook.py`: No label changes expected, but verify `SECTION_HEADERS` still align after formula-only edits.
2. `tools/verifier.py`: Done (see Step 3 above).
3. `agent.py` system prompt: Add sign-convention guidance ("enter values as they appear; formulas handle signs") and SOCIE routing rules for Other columns.

### Step 5 — End-to-end test on FINCO PDF 🟨 Not started

Run the agent against `data/FINCO-Audited-Financial-Statement-2021.pdf`, open filled workbook in Excel, verify all validation cells are zero.

---

## Learnings & Surprises

1. **4 of 8 statement templates had no MTool equivalent** (SOFP-OrderOfLiquidity, SOPL-Nature, SOCI-NetOfTax, SOCF-Direct). MTool only ships the more common variant of each statement. Fixes for these relied on accounting logic rather than byte-for-byte formula comparison. Risk: these could still diverge from what MTool would produce if SSM ever ships the alternate variants.

2. **SOFP-CuNonCu had 3 formula bugs, not 0.** The HANDOFF's earlier analysis said "Row labels on SOFP-CuNonCu were spot-checked and match MTool exactly (just offset by 21 rows). Formulas on the other 13 sheets have NOT been compared." The label match was correct, but the formulas had broken cell references (B75→B67+B94, B71/B73→B85). Lesson: label match ≠ formula correctness.

3. **SOCF-Indirect had cascading wrong references.** Five formulas each pointed at wrong rows, and the errors compounded: operating surplus referenced an investing row, cash-from-operations missed the operating surplus entirely, and the final net-increase formula referenced a purchase row and a nonexistent row. These would have produced completely wrong cash flow totals. Root cause appears to be a copy-paste error during initial template construction where MTool row numbers were used directly without adjusting for the 20-row offset.

4. **The homegrown formula evaluator didn't support `SUM()` ranges.** `=SUM(E6:L6)` was silently evaluated as `E6+L6`, returning 9 instead of 36 on test data. This affected every SOCIE horizontal subtotal. The evaluator also had no range expansion for `A1:B3`-style references. Fixed by adding `_expand_range()` and a SUM-specific parser path.

5. **Sign convention is now standardized.** All templates use MTool's pattern: values entered as positive numbers as they appear on the financial statement, with formulas applying `+-1*` coefficients for items that reduce totals (Dividends paid, Treasury shares, Cost of sales, Finance costs, Tax expense, etc.). This matches the HANDOFF's hypothesis and is now implemented consistently across all 9 template files.

6. **Fail-closed verifier behavior matters.** The initial verifier implementation returned `is_balanced=True` when no matching labels were found (vacuous pass). A peer review caught this: a blank or mislabelled workbook would be reported as "balanced." Now all verifiers require finding their key labels or they return `is_balanced=False` with explicit "required label not found" messages.

---

## Open questions for the user

These came up during analysis and remain unanswered:

1. **Sign convention preference** — now implemented as "enter values as positive, formulas handle signs with `-1*`" across all templates. This matches MTool. Confirm this is acceptable.
2. **Hidden validation columns vs verifier-only** — validation is implemented in the verifier tool only, templates stay clean for MTool paste. The HANDOFF recommended adding to templates; we chose verifier-only. Confirm preference.
3. **Cross-sheet check severity** — verifier reports mismatches but doesn't hard-fail; caller decides. The `verify_cross_sheet()` function returns structured results the coordinator can act on.
4. **SOCIE "Other" column sub-labelling** — not addressed (template structure unchanged).
5. **NEW: Wiring cross-checks into coordinator** — `verify_cross_sheet()` and the `cross_checks/` framework exist as tested library code but are not yet called from `coordinator.py` after `asyncio.gather`. Step 4 should address this.
