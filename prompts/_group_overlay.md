=== GROUP FILING — DUAL-ENTITY EXTRACTION ===

This is a **Group-level** XBRL filing. The template has 6 data columns instead of 4:

| Column | Purpose |
|--------|---------|
| A | Field labels (same as Company template) |
| B | **Group** current year |
| C | **Group** prior year |
| D | **Company** current year |
| E | **Company** prior year |
| F | Source / evidence reference |

You must extract **two sets of numbers** from the PDF:

1. **Group (consolidated)** figures — typically labelled "Group", "Consolidated", or appear in the left-hand columns of the face statement. Fill these into columns B and C (col=2 and col=3).

2. **Company** figures — typically labelled "Company", "The Company", or appear in the right-hand columns of the face statement. Fill these into columns D and E (col=4 and col=5).

Source/evidence references go into column F (col=6) instead of column D.

### Where to find the numbers

- Most Malaysian financial statements show Group and Company side-by-side on the same page (4 columns: Group CY, Group PY, Company CY, Company PY).
- Some statements present Company figures on a separate page after the Group figures.
- Notes typically show consolidated numbers only. If a note shows both Group and Company, extract both.
- If the PDF only shows one set of numbers (consolidated only), fill Group columns (B/C) and leave Company columns (D/E) empty.

### Fill order

1. Fill Group current year (col=2) and Group prior year (col=3) first.
2. Then fill Company current year (col=4) and Company prior year (col=5).
3. Fill source/evidence in column F (col=6).

Apply the same label-matching and section-disambiguation rules as for a Company filing, just targeting the correct columns.