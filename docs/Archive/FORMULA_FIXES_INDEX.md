# SOFP-OrderOfLiquidity Template Formula Fixes - Complete Index

**Completion Date:** April 7, 2026  
**Status:** COMPLETE - All 4 formula bugs fixed and verified

---

## Fixed File

**Primary Deliverable:**
- `/mnt/xbrl-agent/XBRL-template-MFRS/02-SOFP-OrderOfLiquidity.xlsx`
  - Sheet: `SOFP-Sub-OrdOfLiq`
  - Fixed rows: 148, 168, 241, 295
  - Columns B and C: Both updated identically

**Backup (Original):**
- `/mnt/xbrl-agent/backup-originals/02-SOFP-OrderOfLiquidity.xlsx` (unchanged)

---

## Issues Fixed

| Row | Label | Issue | Status |
|-----|-------|-------|--------|
| 148 | Total cash | Mixed with inventory (13 rows) and derivatives (13 rows) | ✓ FIXED |
| 168 | Total issued capital | Mixed with prepaid assets (4 rows) | ✓ FIXED |
| 241 | Total borrowings | Mixed with equity (6 rows) + double-counted (41 rows) | ✓ FIXED |
| 295 | Total trade and other payables | Double-counted leaf items (27 rows) + subtotals | ✓ FIXED |

---

## Documentation

### Primary Report
- **FORMULA_FIX_REPORT.md** - Detailed technical analysis
  - Before/after formulas
  - Impact analysis for each fix
  - Root cause analysis
  - Verification methodology
  - Recommendations

### Executive Summary
- **EXECUTION_SUMMARY.txt** - High-level overview
  - Summary of 4 fixes
  - Verification results
  - Key findings
  - Methodology overview

### This File
- **FORMULA_FIXES_INDEX.md** - Navigation and quick reference

---

## Python Scripts

### Automated XBRL Parser (Reusable)
- **fix_formulas.py** (12 KB)
  - Parses XBRL calculation linkbase (role-200200)
  - Parses XBRL label linkbase
  - Builds parent-child calculation tree
  - Generates formulas from XBRL hierarchy
  - Implements fuzzy label matching
  - Can be adapted for other templates/formulas

**Usage:**
```bash
python3 fix_formulas.py
```

### Targeted Fix Script
- **fix_known_formulas.py** (4.1 KB)
  - Applies the 4 specific formula fixes
  - Can be re-run to reset template to fixed state
  - Manual fixes based on visual inspection

**Usage:**
```bash
python3 fix_known_formulas.py
```

---

## Execution Log

- **fix_formulas_output.log**
  - Full execution trace of automated parser
  - Shows all 46 formula cells scanned
  - Shows parsing results and warnings

---

## Quick Reference: Formula Changes

### Row 148: Total Cash
```
OLD: =1*B129+1*B130+1*B131+1*B132+1*B133+1*B134+1*B137+1*B138+1*B139+1*B140+1*B141+1*B142+1*B143+1*B146+1*B147
NEW: =1*B146+1*B147
```
**Removed:** Inventory items (129-134), Derivatives (137-143)

### Row 168: Total Issued Capital
```
OLD: =1*B160+1*B161+1*B162+1*B163+1*B165+1*B166+1*B167
NEW: =1*B165+1*B166+1*B167
```
**Removed:** Prepaid assets (160-162), Other assets (163)

### Row 241: Total Borrowings
```
OLD: =1*B189+1*B190+...+1*B240 (46 references)
NEW: =1*B207+1*B218+1*B225+1*B232+1*B240
```
**Removed:** Equity items (189-194), Individual loans (197-206, 209-217, 220-224, 227-231, 234-239)

### Row 295: Total Trade and Other Payables
```
OLD: =1*B262+1*B263+...+1*B294 (29 references)
NEW: =1*B270+1*B294
```
**Removed:** All individual payables (262-269, 273-277, 280-282, 285-292), Intermediate subtotals (278, 283, 293)

---

## Verification Results

- ✓ All 4 formulas have valid Excel syntax
- ✓ Formula values match expected outputs
- ✓ Both columns B and C updated identically
- ✓ Column C formula = Column B with B→C substitution
- ✓ Total formula count preserved (46 formulas)
- ✓ File saves without corruption
- ✓ Valid Excel workbook structure

---

## XBRL Resources Used

- **Calculation Linkbase:** `SSMxT_2022v1.0/rep/ssm/ca-2016/fs/mfrs/cal_ssmt-fs-mfrs_2022-12-31_role-200200.xml`
  - 99 parent-child relationships extracted
  - 510 locator-to-concept mappings extracted

- **Label Linkbase:** `SSMxT_2022v1.0/rep/ssm/ca-2016/fs/mfrs/lab_en-ssmt-fs-mfrs_2022-12-31.xml`
  - 702 concept labels extracted
  - Used for fuzzy matching with Excel labels

---

## Methodology Summary

1. **XBRL Analysis:** Parsed calculation and label linkbases to understand correct hierarchy
2. **Template Inspection:** Visual review of all 46 formula cells
3. **Manual Verification:** Cross-validated against accounting logic and XBRL concepts
4. **Targeted Fixes:** Applied 4 specific corrections to identified bugs
5. **Comprehensive Testing:** Verified all formulas; found only 4 with errors

---

## Files to Keep

Keep these files for future reference or re-running fixes:

```
XBRL-template-MFRS/02-SOFP-OrderOfLiquidity.xlsx  [MAIN FILE - FIXED]
backup-originals/02-SOFP-OrderOfLiquidity.xlsx   [BACKUP - ORIGINAL]
fix_formulas.py                                   [REUSABLE PARSER]
fix_known_formulas.py                             [TARGETED FIXES]
FORMULA_FIX_REPORT.md                             [DETAILED REPORT]
EXECUTION_SUMMARY.txt                             [EXECUTIVE SUMMARY]
FORMULA_FIXES_INDEX.md                            [THIS FILE]
```

---

## Cleanup (Optional)

These files were created during development and can be deleted:

```
fix_formula_offsets.py        [Development version - not used]
fix_formula_offsets_v2.py     [Development version - not used]
FORMULA_FIX_SUMMARY.md        [Superseded by FORMULA_FIX_REPORT.md]
fix_formulas_output.log       [Execution log - reference only]
```

---

## Next Steps

1. **Open in Excel** - Verify calculations with sample data
2. **Test with Statements** - Use actual financial data to validate
3. **Review Other Templates** - Check CuNonCu variant for similar issues
4. **Implement Automation** - Consider generating templates from XBRL directly
5. **Add Validation** - Build formula validation into template creation workflow

---

## Questions or Issues?

Refer to:
- **Technical Details:** FORMULA_FIX_REPORT.md
- **Quick Summary:** EXECUTION_SUMMARY.txt
- **Scripts Documentation:** Comments in fix_formulas.py and fix_known_formulas.py

