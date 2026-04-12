# XBRL Template Formula Fix Guide

## Root Cause

The XBRL Excel templates in `XBRL-template-MFRS/` were derived from the original
SSM MBRS v2.0 XBRL templates (SSMxT_2022v1.0). The original SSM templates have a
different physical layout:

- **Columns E/F** for current/prior year data (columns A–D hold XBRL metadata)
- **20 extra header rows** at the top of each data sheet (XBRL element IDs, tags)

Our working templates were simplified:

- **Columns B/C** for current/prior year data (column A = labels, D/E = evidence)
- **No extra header rows** — data starts at the same row as labels

A prior AI agent translated the SSM formulas to our layout. It correctly:

1. Changed column references (E→B, F→C)
2. Re-derived within-section formulas from actual row positions
3. Applied the -20 row offset for **cross-sheet** references (e.g., SOPL-Function → SOPL-Analysis-Function)

But it **failed to apply the -20 row offset for cross-section references within
the same sheet**. These formulas still carry row numbers from the original SSM
layout, pointing exactly +20 rows from where they should be — landing in
completely unrelated accounting sections.

## Evidence

The +20 offset was confirmed by comparing backup originals against fixed templates:

```
SOPL cross-sheet references (original → fixed):
  'SOPL-Analysis-Function'!E60  → !B40   (row offset: -20)
  'SOPL-Analysis-Function'!E67  → !B47   (row offset: -20)
  'SOPL-Analysis-Function'!E110 → !B90   (row offset: -20)
  'SOPL-Analysis-Function'!E154 → !B134  (row offset: -20)
  'SOPL-Analysis-Function'!E158 → !B138  (row offset: -20)
```

Cross-sheet refs got the -20 fix. Same-sheet cross-section refs did not.

## Impact

The extraction agent reverse-engineers the broken formulas at runtime, placing
values in semantically wrong rows to make totals balance. This produces correct
main-sheet totals but misleading sub-sheet detail (e.g., deposits stored in
"Cash" rows, bank balances in "Other cash and cash equivalents").

## Affected Templates

The bug affects **cross-section total formulas** in these sheets:

| Template | Sheet | Bug Type | Severity |
|----------|-------|----------|----------|
| `01-SOFP-CuNonCu.xlsx` | `SOFP-Sub-CuNonCu` | +20 offset | **HIGH** — 450-row sub-sheet, many cross-section totals |
| `02-SOFP-OrderOfLiquidity.xlsx` | `SOFP-Sub-OrdOfLiq` | **Mixed bugs** (see below) | **HIGH** — different formula generation errors |

### ⚠️ OrderOfLiquidity has DIFFERENT bugs

`02-SOFP-OrderOfLiquidity.xlsx` / `SOFP-Sub-OrdOfLiq` does **not** follow the clean +20
offset pattern. It has a different class of formula errors:

- **Row 148 "Total cash"**: sums inventory items + derivative items + cash items (should be just CashOnHand + BalancesWithBanks)
- **Row 168 "Total issued capital"**: includes prepaid assets in addition to capital items
- **Row 241 "Total borrowings"**: includes equity items (perpetual sukuk, ICULS equity component) mixed with borrowings
- **Row 295 "Total trade and other payables"**: double-counts by summing both leaf items AND their subtotals

These errors appear to come from the formula generator walking the XBRL hierarchy
but including ALL preceding rows instead of only the correct children. Fix this
template by regenerating formulas from the XBRL calculation linkbase
(`cal_ssmt-fs-mfrs_2022-12-31_role-200200.xml`), NOT by applying the -20 rule.

**Templates confirmed clean (manually verified — all formulas reference correct rows):**
- `01-SOFP-CuNonCu.xlsx` / `SOFP-CuNonCu` (main sheet — formulas are within-section)
- `03-SOPL-Function.xlsx` (both sheets — cross-sheet refs already fixed correctly)
- `04-SOPL-Nature.xlsx` (both sheets — formulas within-section)
- `05-SOCI-BeforeTax.xlsx` / `06-SOCI-NetOfTax.xlsx`
- `07-SOCF-Indirect.xlsx` (formulas reference correct operating/investing/financing rows)
- `08-SOCF-Direct.xlsx` (formulas reference correct rows within each activity section)
- `09-SOCIE.xlsx` (equity beginning + changes = end — all refs verified correct)
- `10-14 Notes templates`

## How to Identify Broken Formulas

A formula is broken if it references a row in a **different accounting section** of the sheet.

### Pattern: Same-sheet formula references rows +20 away from the correct target

```
Row 193: *Total cash and cash equivalents
  Formula:  =1*B203+1*B211+1*B192
  B203 = *Total issued capital      ← WRONG (equity section!)
  B211 = Statutory reserve           ← WRONG (equity section!)
  B192 = Other cash and cash equiv   ← correct (same section)

  Fix: subtract 20 from the broken refs:
  Corrected: =1*B183+1*B191+1*B192
  B183 = *Total cash                 ← correct
  B191 = *Total cash equivalents     ← correct
  B192 = Other cash and cash equiv   ← unchanged
```

### Verified Broken Formulas in `SOFP-Sub-CuNonCu`

These were manually verified against the XBRL calculation linkbase:

| Row | Label | Broken Ref | Points To (wrong) | Correct (-20) | Points To (right) |
|-----|-------|-----------|-------------------|---------------|-------------------|
| 20 | *Total land and buildings | B33 | Mining assets | B13 | *Land |
| 39 | *Total PPE | B40 | Investment property | B20 | *Total land and buildings |
| 39 | *Total PPE | B46 | Building under construction | B26 | *Total vehicles |
| 69 | *Total intangible + goodwill | B87 | Investments in JV | B67 | *Total intangible assets other than goodwill |
| 119 | Total other NC receivables | B133 | Finished goods | B113 | *Total other NC receivables from related parties |
| 119 | Total other NC receivables | B138 | Current trade receivables | B118 | *Total NC non-trade receivables |
| 129 | *Total NC derivative assets | B147 | Other current trade receivables | B127 | Total NC derivatives at FVTPL |
| 160 | Total prepayments/accrued income | B178 | *Total current derivative assets | B158 | Prepayments |
| 168 | Total other current receivables | B176 | Total current derivatives FVTPL | B156 | Total other current receivables from related parties |
| 168 | Total other current receivables | B180 | Cash | B160 | Total current prepayments and accrued income |
| 168 | Total other current receivables | B187 | Cash equiv w/ other FI | B167 | Total current non-trade receivables |
| 178 | *Total current derivative assets | B196 | Prepaid land lease | B176 | Total current derivatives at FVTPL |
| 193 | *Total cash and cash equivalents | B203 | *Total issued capital | B183 | *Total cash |
| 193 | *Total cash and cash equivalents | B211 | Statutory reserve | B191 | *Total cash equivalents |
| 222 | *Total reserves | B234 | Hire purchase liabilities | B214 | Total non-distributable reserves |
| 222 | *Total reserves | B241 | Term loans | B221 | Total distributable reserves |
| 256 | Total NC secured bonds/sukuk | B271 | *Total NC borrowings | B251 | Bonds |
| 271 | *Total NC borrowings | B276 | Provision for unconsumed leave | B256 | Total NC secured bonds/sukuk |
| 271 | *Total NC borrowings | B283 | Restructuring provision | B263 | Total NC unsecured bonds/sukuk |
| 320 | Other NC payables | B327 | Other derivatives | B307 | Total other payables due to NCI |
| 320 | Other NC payables | B339 | Bankers acceptance | B319 | Total NC non-trade payables |
| 336 | *Total NC derivative liabilities | B348 | Other secured bank loans | B328 | Total NC derivatives at FVTPL |
| 336 | *Total NC derivative liabilities | B354 | Islamic financing facilities | B334 | Total NC derivatives used for hedging |
| 436 | Total other current payables | B440 | Forward contract | B420 | Total other current payables from related parties |
| 436 | Total other current payables | B445 | Derivatives hedging | B425 | Total other payables due to NCI |
| 436 | Total other current payables | B455 | (current derivatives section) | B435 | Total current non-trade payables |
| 452 | *Total current derivative liabilities | B464 | **OUT OF BOUNDS** | B444 | Total current derivatives at FVTPL |
| 452 | *Total current derivative liabilities | B470 | **OUT OF BOUNDS** | B450 | Total current derivatives used for hedging |

**Pattern:** Almost all bugs were cross-section subtotal references shifted by +20.
One additional bug at row 256 also had the same +20 shape, but it broke a
single-level subtotal by pointing to the grand borrowing total instead of the
first secured-borrowings leaf row. In practice, both subtotal references and
same-section leaf references should be checked for +20 offsets.

## Fix Instructions

### Step 1: Scan each affected sheet for cross-section references

For each formula cell in the affected sheets:

1. Parse all `B{row}` and `C{row}` references in the formula
2. Look up the label of each referenced row
3. Check if the referenced row's label belongs to the same accounting section as the formula row's label
4. If not, check whether `row - 20` has a label that DOES belong to the correct section
5. If yes, this is a broken reference — replace `B{row}` with `B{row-20}` (and same for C column)

### Step 2: Verify against the XBRL calculation linkbase

The authoritative source of truth is the SSM XBRL taxonomy:

```
SSMxT_2022v1.0/rep/ssm/ca-2016/fs/mfrs/cal_ssmt-fs-mfrs_2022-12-31_role-200200.xml
```

This XML file defines calculation arcs like:
```xml
<calculationArc from="parent_concept" to="child_concept" weight="1" order="1.0"/>
```

For each total formula:
1. Identify the XBRL concept for the total row (match by label)
2. Find its children in the calculation linkbase
3. Map each child concept to an Excel row by label matching
4. Verify the formula references exactly those rows

### Step 3: Apply fixes

For both Column B and Column C formulas (they always have the same structure):
- Only change references that point to a different accounting section
- Subtract 20 from those specific references
- Leave all other references unchanged
- Save the file

### Step 4: Verify

After applying all fixes:

- [ ] Open each `.xlsx` in Excel (formulas must evaluate, not just openpyxl)
- [ ] Enter test values in detail rows across multiple sections
- [ ] Verify total rows compute correctly
- [ ] **SOFP Sub**: Total PPE = sum of land + buildings + vehicles + equipment items
- [ ] **SOFP Sub**: Total cash = Cash + Cash equivalents + Other cash (NOT equity rows)
- [ ] **SOFP Sub**: Total receivables = Trade + Other receivables
- [ ] **SOFP Sub**: Total reserves = Non-distributable + Distributable
- [ ] **SOFP Sub**: Total borrowings = Secured + Unsecured + Bonds + Other
- [ ] **SOCF**: Net cash = Operating + Investing + Financing
- [ ] **SOCIE**: Opening + Changes = Closing equity
- [ ] Run extraction agent on FINCO PDF — verify no more formula workarounds needed

## Important Notes

- **Within-section formulas are correct.** Do NOT change references that point to rows
  in the same accounting section. For example, `*Total cash = B181 + B182` (Cash in hand
  + Balances with Licensed Banks) is correct — both rows are in the cash section.

- **Cross-sheet formulas are correct.** References like `='SOPL-Analysis-Function'!B40`
  were already fixed. Do not change these.

- **Both B and C columns have identical formula structure.** If B has a broken ref at
  a given row, C will have the same broken ref (just with C instead of B).

- **The fix is always exactly -20.** No broken reference has a different offset.
  If you find a formula that seems wrong but -20 doesn't produce a sensible target,
  it's either not broken or needs manual investigation against the XBRL linkbase.

- **The `SOFP-Sub-OrdOfLiq` sheet** has DIFFERENT bugs from CuNonCu (see "Mixed bugs"
  note above). Do NOT apply the -20 rule blindly — regenerate formulas from the
  XBRL calculation linkbase instead.

## XBRL Multi-Parent Warning

The XBRL calculation linkbase (role-200100) defines concepts appearing under
**multiple parents** for different presentation variants. For example:

- `CashAndBankBalances` is a child of both `Assets` (flat view) and
  `CurrentAssets` (current/non-current view)
- `TradeAndOtherCurrentPayables` appears under both `Liabilities` and
  `CurrentLiabilities`

This means the calculation linkbase contains **alternative calculation trees**
for CuNonCu, OrderOfLiquidity, and flat presentations in a single file.

When regenerating formulas, use the **presentation linkbase** to determine which
tree applies to the template variant:
- CuNonCu: `pre_ssmt-fs-mfrs_2022-12-31_role-210000.xml` (main) and `role-210100.xml` (sub)
- OrderOfLiquidity: corresponding role files

Match calculation arcs to the presentation hierarchy for the correct variant.
