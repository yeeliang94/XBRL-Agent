# Formula Fix Report: SOFP-OrderOfLiquidity Template

**File:** `/sessions/happy-zealous-dirac/mnt/xbrl-agent/XBRL-template-MFRS/02-SOFP-OrderOfLiquidity.xlsx`
**Sheet:** `SOFP-Sub-OrdOfLiq`
**Date:** 2026-04-07

## Summary

Fixed **4 critical mixed formula bugs** where subtotals were incorrectly summing rows from wrong accounting sections or double-counting leaf items and their subtotals. All fixes applied to both columns B (current period) and C (prior period).

| Row | Label | Issue | Status |
|-----|-------|-------|--------|
| 148 | Total cash | Included 13 inventory + derivative items | ✓ FIXED |
| 168 | Total issued capital | Included 4 prepaid asset items | ✓ FIXED |
| 241 | Total borrowings | Included 41 equity items + leaf items (was double-counting) | ✓ FIXED |
| 295 | Total trade and other payables | Double-counted 27 leaf items alongside subtotals | ✓ FIXED |

---

## Detailed Changes

### Row 148: Total Cash

**Bug:** Formula was summing inventory items (rows 129-134) and derivative assets (rows 137-143) alongside the actual cash items.

**Original Formula:**
```
=1*B129+1*B130+1*B131+1*B132+1*B133+1*B134+1*B137+1*B138+1*B139+1*B140+1*B141+1*B142+1*B143+1*B146+1*B147
```

**Corrected Formula:**
```
=1*B146+1*B147
```

**Impact:**
- Removed 13 incorrect references (inventory and derivative asset items)
- Kept only the 2 correct cash items:
  - Row 146: Cash on hand
  - Row 147: Balances with banks

**Root Cause:** Likely copy-paste error when constructing the formula, mixing content from different accounting sections.

---

### Row 168: Total Issued Capital

**Bug:** Formula included 4 prepaid/other asset rows (160-163) alongside the correct capital items.

**Original Formula:**
```
=1*B160+1*B161+1*B162+1*B163+1*B165+1*B166+1*B167
```

**Corrected Formula:**
```
=1*B165+1*B166+1*B167
```

**Impact:**
- Removed 4 incorrect asset references:
  - Row 160: Prepaid rental of buildings and facilities
  - Row 161: Prepaid land lease
  - Row 162: Other assets
  - Row 163: Other assets
- Kept the 3 correct capital items:
  - Row 165: Capital from ordinary shares
  - Row 166: Capital from redeemable preference shares
  - Row 167: Capital from non-redeemable preference shares

**Root Cause:** Row range mismatch during formula construction (160-167 instead of 165-167).

---

### Row 241: Total Borrowings

**Bug:** Formula was summing ALL rows from equity section (189-194) plus ALL individual loan/bond items (197-240), creating double-counting. Should only sum the 5 borrowing subtotals.

**Original Formula:**
```
=1*B189+1*B190+1*B191+1*B192+1*B193+1*B194+1*B197+1*B198+...+1*B240
```
(46 total row references)

**Corrected Formula:**
```
=1*B207+1*B218+1*B225+1*B232+1*B240
```

**Impact:**
- Removed 41 incorrect references (all equity items and individual loan items)
- Kept only the 5 correct borrowing category subtotals:
  - Row 207: Secured bank loans received (subtotal of rows 197-206)
  - Row 218: Unsecured bank loans received (subtotal of rows 209-217)
  - Row 225: Secured bonds/sukuk/loan stock (subtotal of rows 220-224)
  - Row 232: Unsecured bonds/sukuk/loan stock (subtotal of rows 227-231)
  - Row 240: Other borrowings (subtotal of rows 234-239)

**Equity items incorrectly included:**
- Perpetual sukuk, ICULS components, preference shares, head office accounts (rows 189-193)
- Other components of equity (row 194)

**Root Cause:** Formula was constructed to include BOTH leaf items AND their category subtotals, causing double-counting of individual items. This is the most severe mixing error.

---

### Row 295: Total Trade and Other Payables

**Bug:** Formula double-counted by including both individual leaf items (rows 262-269, 273-277, 280-282, 285-292) AND their category subtotals (rows 270, 278, 283, 293, 294).

**Original Formula:**
```
=1*B262+1*B263+1*B264+1*B265+1*B266+1*B267+1*B268+1*B269+1*B270+1*B273+1*B274+1*B275+1*B276+1*B277+1*B278+1*B280+1*B281+1*B282+1*B283+1*B285+1*B286+1*B287+1*B288+1*B289+1*B290+1*B291+1*B292+1*B293+1*B294
```
(29 total row references)

**Corrected Formula:**
```
=1*B270+1*B294
```

**Impact:**
- Removed 27 incorrect references (all leaf items and intermediate subtotals)
- Kept only the 2 correct top-level subtotals:
  - Row 270: Trade payables (already sums rows 262-269)
  - Row 294: Other payables (already sums rows 278, 283, 293)

**Hierarchy being summed:**
- Row 270 (Trade payables) contains:
  - Rows 262-269: Trade payables by type (customers, suppliers, related parties, etc.)
- Row 294 (Other payables) contains:
  - Row 278: Other payables due to related parties (rows 273-277)
  - Row 283: Other payables due to non-controlling interests (rows 280-282)
  - Row 293: Other non-trade payables (rows 285-292)

**Root Cause:** Formula was built by concatenating all rows without recognizing the parent-child structure, resulting in summing both parent subtotals and their children.

---

## Verification Approach

Fixes were verified using:

1. **XBRL Calculation Linkbase Analysis:** Parsed the official `cal_ssmt-fs-mfrs_2022-12-31_role-200200.xml` (Order of Liquidity variant) to understand the correct parent-child relationships.

2. **Template Structure Inspection:** Visually examined the template rows to identify which rows are subtotals vs. leaf items based on:
   - Row labels containing "[abstract]" keyword (indicates grouping/category)
   - Nested indentation in row structure
   - Presence of formulas (all fixed rows had formulas)

3. **Logical Accounting Verification:** Ensured fixes align with:
   - Cash in current assets = only cash line items, not inventory/derivatives
   - Equity capital = only share capital items, not prepaid assets
   - Total borrowings = sum of borrowing category subtotals, not leaf items or equity
   - Total payables = sum of major payables categories, not individual line items

---

## Files Modified

| File | Status |
|------|--------|
| `/mnt/xbrl-agent/XBRL-template-MFRS/02-SOFP-OrderOfLiquidity.xlsx` | **FIXED** |
| `/mnt/xbrl-agent/backup-originals/02-SOFP-OrderOfLiquidity.xlsx` | Backup (unchanged) |

Both columns B (current period) and C (prior period) formulas were corrected identically.

---

## Additional Findings

A comprehensive scan of ALL 46 formula cells in the sheet was performed. Only these 4 rows had detectable errors based on:
- Mismatched row ranges that mix accounting sections
- Double-counting parent subtotals and their child items

Other formulas appear to be correctly constructed within their respective sections.

---

## Recommendations

1. **Validate in Excel:** Open the fixed template in Excel and verify that calculated totals are correct with sample data.

2. **Cross-check with Audited Statements:** For templates filled with actual financial data, verify that "Total cash" + "Total inventories" = full current assets, etc.

3. **Update Test Fixtures:** If there are reference files or test cases using the old template, update them with the corrected formulas.

4. **Template Generation:** Consider regenerating templates directly from the XBRL calculation linkbase using a script to eliminate hand-constructed formula errors.

---

## Script Artifacts

- `fix_formulas.py` - Automated parser for XBRL linkbase (generic, reusable for future fixes)
- `fix_known_formulas.py` - Targeted fix for the 4 known bugs
- `fix_formulas_output.log` - Full execution log with detailed warnings

