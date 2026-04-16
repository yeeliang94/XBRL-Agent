# OrderOfLiquidity Template Bug Verification - Complete Report Index

## Overview

This directory contains a complete verification of the four bug claims in `TEMPLATE-FORMULA-FIX-GUIDE.md` for the OrderOfLiquidity template (`02-SOFP-OrderOfLiquidity.xlsx`, sheet `SOFP-Sub-OrdOfLiq`).

**Result: All 4 bugs CONFIRMED**

---

## Key Findings

| Row | Bug Claim | Status | Severity |
|-----|-----------|--------|----------|
| **148** | Total cash sums inventory + derivative + cash | ✗ CONFIRMED | HIGH |
| **168** | Total issued capital includes prepaid assets | ✓ CONFIRMED | MEDIUM |
| **241** | Total borrowings includes equity items | ✗ CONFIRMED | HIGH |
| **295** | Total payables double-counts leaf + subtotals | ✗ CONFIRMED | **CRITICAL** |

---

## Documents

### 1. **BUG_VERIFICATION_SUMMARY.txt** (Start here)
Executive summary of all findings in plain text format.
- Quick overview of the 4 bugs
- Methodology
- Detailed findings breakdown
- Severity assessment
- Recommendations for fixing

**Read this first for quick understanding.**

### 2. **ORDEROF_LIQUIDITY_BUG_VERIFICATION_REPORT.md**
Comprehensive markdown report with detailed analysis.
- Row-by-row analysis with actual formulas
- What each reference resolves to
- Complete verification table
- Fix recommendations
- Explanation of why these are "different bugs" from CuNonCu

**Read this for detailed technical analysis.**

### 3. **OFFSET_BUG_VERIFICATION_REPORT.md**
Companion report showing the CuNonCu bugs for comparison.
- Documents the +20 offset bugs in SOFP-Sub-CuNonCu
- Proves OrderOfLiquidity has fundamentally different errors
- Historical evidence of the formula generation failure

**Read this to understand why OrderOfLiquidity bugs are different.**

---

## Verification Scripts

### 1. **verify_ordofliq_bugs.py**
Basic formula extraction and labeling.
```bash
python3 verify_ordofliq_bugs.py
```
Output: Shows all formulas and what each cell reference points to.

### 2. **verify_ordofliq_bugs_v2.py**
Section-aware analysis with better categorization.
```bash
python3 verify_ordofliq_bugs_v2.py
```
Output: Groups references by accounting section, flags cross-section anomalies.

### 3. **verify_ordofliq_bugs_final.py**
Final comprehensive analysis with detailed categorization.
```bash
python3 verify_ordofliq_bugs_final.py
```
Output: Detailed breakdown of what each formula sums, category analysis.

**Use this script to regenerate the findings.**

### 4. **validate_ordofliq_fixes.py**
Validation script for verifying fixes have been applied.
```bash
python3 validate_ordofliq_fixes.py [path_to_fixed_template.xlsx]
```
Output: PASS/FAIL for each of the 4 rows, with specific details on what's wrong.

**Use this after applying fixes to verify they're correct.**

---

## Bug Details at a Glance

### Row 148: Total cash
- **Should contain:** B146 (Cash on hand) + B147 (Balances with banks) = 2 cells
- **Actually contains:** 15 cells including inventory items, derivative items, and cash
- **Wrong references:** 13 out of 15 (inventory + derivatives)
- **Impact:** Cash total will include non-cash assets

### Row 168: Total issued capital
- **Should contain:** B165, B166, B167 (Capital items only) = 3 cells
- **Actually contains:** 7 cells including prepaid assets
- **Wrong references:** 2-4 out of 7 (prepaid + other assets)
- **Impact:** Capital total will be mixed with asset section items

### Row 241: Total borrowings
- **Should contain:** Only borrowing items (loans, bonds, sukuk, etc.)
- **Actually contains:** 46 cells including 5 equity items
- **Equity items included:**
  - Row 189: Perpetual sukuk
  - Row 190: Equity component of ICULS
  - Row 191: Equity component of preference shares
  - Row 193: Equity components of other financial instruments
  - Row 194: Other components of equity
- **Impact:** Borrowings total will include equity-classified instruments

### Row 295: Total trade and other payables
- **Should contain:** Only the 4 section subtotals (B270, B278, B283, B294)
- **Actually contains:** 29 cells including both leaf items AND their subtotals
- **Double-counted sections:**
  - Trade payables: 8 leaf items (B262-B269) + subtotal B270
  - Other payables due to related: 5 leaf items (B273-B277) + subtotal B278
  - Other payables due to NCI: 3 leaf items (B280-B282) + subtotal B283
  - Other non-trade payables: 8 leaf items (B285-B292) + subtotal B293
- **Total double-counted:** 24 items
- **Impact:** Payables total will be exactly 2x the correct value

---

## How to Use This Report

### For Code Review:
1. Read **BUG_VERIFICATION_SUMMARY.txt** for quick overview
2. Run `python3 verify_ordofliq_bugs_final.py` to see live findings
3. Read **ORDEROF_LIQUIDITY_BUG_VERIFICATION_REPORT.md** for details

### To Apply Fixes:
1. Read "Recommendations" section in **ORDEROF_LIQUIDITY_BUG_VERIFICATION_REPORT.md**
2. Apply fixes to the template:
   - Row 148: Change to `=1*B146+1*B147`
   - Row 168: Change to `=1*B165+1*B166+1*B167`
   - Row 241: Remove rows 189-194 from formula
   - Row 295: Change to `=1*B270+1*B278+1*B283+1*B294`
3. Run `python3 validate_ordofliq_fixes.py path_to_fixed_template.xlsx`
4. All validations should PASS

### To Verify Template Status:
```bash
# Check current (broken) template
python3 validate_ordofliq_fixes.py /path/to/02-SOFP-OrderOfLiquidity.xlsx
# Should show: RESULTS: 0 passed, 4 failed

# After fixes
python3 validate_ordofliq_fixes.py /path/to/fixed/02-SOFP-OrderOfLiquidity.xlsx
# Should show: RESULTS: 4 passed, 0 failed
```

---

## Technical Notes

1. **Verification Method:** All findings based on openpyxl formula extraction and label lookup
2. **No Changes Made:** These scripts are read-only; they only analyze the template
3. **Reproducibility:** All findings can be regenerated by running the scripts
4. **Scope:** Analysis covers only the SOFP-Sub-OrdOfLiq sheet; main SOFP sheet was not analyzed

---

## Conclusion

The TEMPLATE-FORMULA-FIX-GUIDE.md claims about OrderOfLiquidity bugs are **100% accurate and verified**. These are not simple +20 row offset errors (like CuNonCu), but rather systematic formula generation failures where:

1. **Row 148** mixed three different asset classes in one formula
2. **Row 168** mixed asset and capital sections
3. **Row 241** mixed equity items with borrowings
4. **Row 295** created massive double-counting structure

All four bugs require specific corrections, not a blanket row offset fix.

---

## Files Generated

```
├── VERIFICATION_INDEX.md (this file)
├── BUG_VERIFICATION_SUMMARY.txt (executive summary)
├── ORDEROF_LIQUIDITY_BUG_VERIFICATION_REPORT.md (detailed technical report)
├── OFFSET_BUG_VERIFICATION_REPORT.md (comparison with CuNonCu)
├── verify_ordofliq_bugs.py (basic extraction script)
├── verify_ordofliq_bugs_v2.py (section-aware script)
├── verify_ordofliq_bugs_final.py (comprehensive analysis script)
└── validate_ordofliq_fixes.py (post-fix validation script)
```

All scripts are in: `/sessions/happy-zealous-dirac/mnt/xbrl-agent/`
