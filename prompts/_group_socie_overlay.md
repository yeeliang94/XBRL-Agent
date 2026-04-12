=== GROUP FILING — SOCIE 4-BLOCK LAYOUT ===

This is a **Group-level** SOCIE template. Instead of extra columns, it uses **4 vertical blocks** (same 24 equity-component columns B through X in each block):

| Block | Rows | Entity | Period |
|-------|------|--------|--------|
| 1 | 3–25 | **Group** | Current year |
| 2 | 27–49 | **Group** | Prior year |
| 3 | 51–73 | **Company** | Current year |
| 4 | 75–97 | **Company** | Prior year |

Each block has the same row labels (Equity at beginning, Profit/loss, Dividends, Equity at end, etc.). The column layout within each block is identical:
- Columns B–X: equity components (Issued capital, Retained earnings, Reserves, NCI, Total, etc.)

You must fill **all 4 blocks** independently:

1. **Block 1 (rows 3–25):** Group current-year SOCIE movements
2. **Block 2 (rows 27–49):** Group prior-year SOCIE movements
3. **Block 3 (rows 51–73):** Company current-year SOCIE movements
4. **Block 4 (rows 75–97):** Company prior-year SOCIE movements

Use explicit `row` and `col` coordinates when calling fill_workbook for SOCIE, targeting the correct row range for each block.

### Where to find the numbers

- The PDF typically shows the consolidated SOCIE first, then the company SOCIE.
- Each SOCIE section in the PDF covers one entity for one period.
- Map: PDF "Group Current Year" → Block 1, "Group Prior Year" → Block 2, "Company Current Year" → Block 3, "Company Prior Year" → Block 4.

Source/evidence references still go into the same column as Company-level SOCIE (the last data column or via the evidence field).