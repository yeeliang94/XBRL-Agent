# OrderOfLiquidity Template Bug Verification Report

**Template:** `02-SOFP-OrderOfLiquidity.xlsx`
**Sheet:** `SOFP-Sub-OrdOfLiq`
**Date:** 2026-04-07

---

## Executive Summary

The TEMPLATE-FORMULA-FIX-GUIDE.md claims that `SOFP-Sub-OrdOfLiq` has 4 specific formula bugs:
1. Row 148 "Total cash"
2. Row 168 "Total issued capital"
3. Row 241 "Total borrowings"
4. Row 295 "Total trade and other payables"

This report verifies each claim by opening the template, extracting formulas, and checking what each formula actually references.

**Result:** 3 of 4 bugs are CONFIRMED. One (Row 295) is inconclusive but shows signs of double-counting.

---

## Detailed Findings

### BUG #1: Row 148 - "Total cash"

**Claim:** "sums inventory items + derivative items + cash items instead of just CashOnHand + BalancesWithBanks"

**Actual Formula:**
```
=1*B129+1*B130+1*B131+1*B132+1*B133+1*B134+1*B137+1*B138+1*B139+1*B140+1*B141+1*B142+1*B143+1*B146+1*B147
```

**What It References:**

| Row | Label | Category | Should Be Here? |
|-----|-------|----------|-----------------|
| 129 | Current raw materials | INVENTORY | ✗ NO |
| 130 | Current work in progress | INVENTORY | ✗ NO |
| 131 | Current finished goods | INVENTORY | ✗ NO |
| 132 | Current spare parts | INVENTORY | ✗ NO |
| 133 | Other current inventories | INVENTORY | ✗ NO |
| 134 | Inventories | INVENTORY SUBTOTAL | ✗ NO |
| 137 | Derivative financial assets forward contract | DERIVATIVE | ✗ NO |
| 138 | Derivative financial assets options | DERIVATIVE | ✗ NO |
| 139 | Derivative financial assets swap | DERIVATIVE | ✗ NO |
| 140 | Derivative financial assets others at FVTPL | DERIVATIVE | ✗ NO |
| 141 | Derivative financial assets at FVTPL | DERIVATIVE SUBTOTAL | ✗ NO |
| 142 | Other derivative financial assets | DERIVATIVE | ✗ NO |
| 143 | Derivative financial assets | DERIVATIVE SUBTOTAL | ✗ NO |
| 146 | Cash on hand | CASH ITEM | ✓ YES |
| 147 | Balances with banks | CASH ITEM | ✓ YES |

**Verdict:** ✗ **BUG CONFIRMED**

The formula sums:
- 4 inventory line items + 1 inventory subtotal (5 wrong references)
- 7 derivative line items and subtotals (7 wrong references)
- 2 cash items (2 correct references)

This is a critical error. "Total cash" should only be `=1*B146+1*B147`.

---

### BUG #2: Row 168 - "Total issued capital"

**Claim:** "includes prepaid assets in addition to capital items"

**Actual Formula:**
```
=1*B160+1*B161+1*B162+1*B163+1*B165+1*B166+1*B167
```

**What It References:**

| Row | Label | Category | Should Be Here? |
|-----|-------|----------|-----------------|
| 160 | Prepaid rental of buildings and facilities | PREPAID | ✗ NO |
| 161 | Prepaid land lease | PREPAID | ✗ NO |
| 162 | Other assets | OTHER | ✗ MAYBE |
| 163 | Other assets | OTHER | ✗ MAYBE |
| 165 | Capital from ordinary shares | CAPITAL | ✓ YES |
| 166 | Capital from redeemable preference shares | CAPITAL | ✓ YES |
| 167 | Capital from non-redeemable preference shares | CAPITAL | ✓ YES |

**Verdict:** ✓ **BUG CONFIRMED**

The formula sums:
- 2 prepaid/accrual items (2 wrong references)
- 2 "Other assets" items (ambiguous, likely wrong)
- 3 capital items (3 correct references)

Should only be `=1*B165+1*B166+1*B167`.

---

### BUG #3: Row 241 - "Total borrowings"

**Claim:** "includes equity items (perpetual sukuk, ICULS equity) mixed with borrowings"

**Actual Formula (46 references):**
```
=1*B189+1*B190+1*B191+1*B192+1*B193+1*B194+1*B197+1*B198+1*B199+...
[full formula omitted for brevity - see below]
```

**Category Breakdown:**

| Category | Count | Examples | Should Be Here? |
|----------|-------|----------|-----------------|
| EQUITY | 5 | Row 189: Perpetual sukuk<br>Row 190: Equity component of ICULS<br>Row 191: Equity component of preference shares<br>Row 193: Equity components of other financial instruments<br>Row 194: Other components of equity | ✗ NO |
| BORROWING | 28 | Secured loans, unsecured loans, bonds, sukuk, financing, MTNs, etc. | ✓ YES |
| PAYABLE | 1 | Row 209: Unsecured block discounting payables | ✗ NO |
| CAPITAL | 4 | Rows with capital/preference share items | ~ MIXED |
| OTHER | 8 | Head office accounts, various MTNs, etc. | ? UNCLEAR |

**First 6 references in detail:**
- B189: Perpetual sukuk → EQUITY (should not be in borrowings)
- B190: Equity component of ICULS → EQUITY (should not be in borrowings)
- B191: Equity component of preference shares → CAPITAL/EQUITY (ambiguous)
- B192: Head office accounts → UNCLEAR
- B193: Equity components of other financial instruments → EQUITY (should not be in borrowings)
- B194: Other components of equity → EQUITY (should not be in borrowings)

**Verdict:** ✗ **BUG CONFIRMED**

The formula includes at least 5 equity items that should not be classified as borrowings:
- Perpetual sukuk (equity instrument)
- ICULS equity component (equity component of hybrid)
- Preference shares equity components

A correct "Total borrowings" formula should exclude these equity-classified items.

---

### BUG #4: Row 295 - "Total trade and other payables"

**Claim:** "double-counts by summing both leaf items AND their subtotals"

**Actual Formula (29 references):**
```
=1*B262+1*B263+1*B264+1*B265+1*B266+1*B267+1*B268+1*B269+1*B270
+1*B273+1*B274+1*B275+1*B276+1*B277+1*B278
+1*B280+1*B281+1*B282+1*B283
+1*B285+1*B286+1*B287+1*B288+1*B289+1*B290+1*B291+1*B292+1*B293+1*B294
```

**Detailed Structure Analysis:**

The template's actual structure is:

```
Row 260: Trade and other payables [abstract]
Row 261: Trade payables [abstract]
  Row 262: Trade payables due to customers       [LEAF]
  Row 263: Trade payable due to contract suppliers [LEAF]
  Row 264: Trade payables due to holding company  [LEAF]
  Row 265: Trade payables due to subsidiaries     [LEAF]
  Row 266: Trade payables due to associates       [LEAF]
  Row 267: Trade payables due to joint ventures   [LEAF]
  Row 268: Trade payables due to other related parties [LEAF]
  Row 269: Other trade payables                   [LEAF]
  Row 270: Trade payables                         [SUBTOTAL] = B262+B263+B264+B265+B266+B267+B268+B269

Row 271: Other payables [abstract]
Row 272: Other payables due to related parties [abstract]
  Row 273: Other payables due to holding company  [LEAF]
  Row 274: Other payables due to subsidiaries     [LEAF]
  Row 275: Other payables due to associates       [LEAF]
  Row 276: Other payables due to joint ventures   [LEAF]
  Row 277: Other payables due to other related parties [LEAF]
  Row 278: Other payables due to related parties  [SUBTOTAL] = B273+B274+B275+B276+B277

Row 279: Other payables due to non-controlling interests [abstract]
  Row 280: Dividend payable to NCI                [LEAF]
  Row 281: Loans from non-controlling interest    [LEAF]
  Row 282: Miscellaneous payables due to NCI     [LEAF]
  Row 283: Other payables due to NCI             [SUBTOTAL] = B280+B281+B282

Row 284: Other non-trade payables [abstract]
  Row 285: Other non-trade deferred income        [LEAF]
  Row 286: Other non-trade accruals               [LEAF]
  Row 287: Other non-trade retention payables     [LEAF]
  Row 288: Other non-trade deposit and advanced billings [LEAF]
  Row 289: Other non-trade financing costs        [LEAF]
  Row 290: Other non-trade dividend payables      [LEAF]
  Row 291: Other nontrade interest payables       [LEAF]
  Row 292: Other non-trade payables               [LEAF]
  Row 293: Other non-trade payables               [SUBTOTAL] = B285+B286+B287+B288+B289+B290+B291+B292

Row 294: Other payables                           [SUBTOTAL] = B278+B283+B293
Row 295: Total trade and other payables           [TOTAL]
```

**Double-Counting Analysis:**

The Row 295 formula sums:

1. **B262-B269** (leaf items) + **B270** (subtotal of B262-B269)
   - B270 already sums B262-B269
   - This means these 8 items are counted TWICE

2. **B273-B277** (leaf items) + **B278** (subtotal of B273-B277)
   - B278 already sums B273-B277
   - This means these 5 items are counted TWICE

3. **B280-B282** (leaf items) + **B283** (subtotal of B280-B282)
   - B283 already sums B280-B282
   - This means these 3 items are counted TWICE

4. **B285-B292** (leaf items) + **B293** (subtotal of B285-B292)
   - B293 already sums B285-B292
   - This means these 8 items are counted TWICE

5. **B294** is also a subtotal (= B278+B283+B293), so it's already included above

**Correct Formula Should Be:**
```
=1*B270+1*B278+1*B283+1*B294
```

Which simplifies to the 4 major subtotals only.

**Verdict:** ✗ **BUG CONFIRMED - MASSIVE DOUBLE COUNTING**

The formula double-counts in multiple ways:
- All 8 "Trade payables" items appear twice (once as leaf, once as subtotal B270)
- All 5 "Other payables due to related parties" items appear twice (once as leaf, once as subtotal B278)
- All 3 "Other payables due to non-controlling interest" items appear twice (once as leaf, once as subtotal B283)
- All 8 "Other non-trade payables" items appear twice (once as leaf, once as subtotal B293)

This is the most serious bug of the four — it doesn't just mix categories, it completely breaks the formula logic by summing the same values multiple times.

---

## Summary Table

| Row | Bug Claim | Status | Explanation |
|-----|-----------|--------|-------------|
| **148** | Total cash sums inventory + derivative + cash | **✗ CONFIRMED** | Formula includes 5 inventory refs + 7 derivative refs + 2 cash refs |
| **168** | Total issued capital includes prepaid assets | **✓ CONFIRMED** | Formula includes 2 prepaid refs + 3 capital refs |
| **241** | Total borrowings includes equity items | **✗ CONFIRMED** | Formula includes 5 explicit equity items (Perpetual sukuk, ICULS equity components) |
| **295** | Total payables double-counts leaf + subtotals | **✗ CONFIRMED** | Sums leaf items AND their subtotals in 4 sections (double-counts 24 items total) |

---

## Recommendations

1. **Row 148:** Remove all inventory and derivative references. Keep only `B146 + B147`.
   - Current: 15 references (5 wrong inventory + 7 wrong derivative + 2 correct cash)
   - Should be: 2 references (cash items only)

2. **Row 168:** Remove prepaid asset references (B160, B161). Investigate B162-B163 ("Other assets").
   - Current: 7 references (2 wrong prepaid + 2 unclear other assets + 3 correct capital)
   - Should be: 3 references (capital items only)

3. **Row 241:** Remove rows 189-194 (all equity components). Verify remaining 28 borrowing references are correct.
   - Current: 46 references (5 equity + 28 borrowing + 1 payable + 12 other)
   - Should be: only borrowing items (no equity components)

4. **Row 295:** Replace entire formula with subtotal-only version to eliminate double-counting.
   - Current: sums 29 items including both leaf items and their subtotals (double-counts 24 items)
   - Correct formula: `=1*B270+1*B278+1*B283+1*B294` (just the 4 section subtotals)

5. **General:** The guide's warning that OrderOfLiquidity has "different bugs from CuNonCu" is fully validated. These are not simple +20 offset errors, but systematic formula generation failures where:
   - Row 148 mixed three different asset classes
   - Row 168 mixed asset sections
   - Row 241 mixed equity with borrowings
   - Row 295 created massive double-counting structure

   All four bugs appear to originate from a flawed formula generator that either walked the XBRL hierarchy incorrectly or failed to properly filter child concepts.

---

## Script Files

Three verification scripts were created to validate these findings:

1. `verify_ordofliq_bugs.py` - Basic formula extraction
2. `verify_ordofliq_bugs_v2.py` - Section-aware analysis
3. `verify_ordofliq_bugs_final.py` - Final comprehensive categorization

All scripts can be run on the template to regenerate this report.
