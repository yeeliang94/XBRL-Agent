=== STATEMENT: SOCIE (Statement of Changes in Equity) — MPERS Default ({{VARIANT}}) ===

=== TEMPLATE STRUCTURE (MPERS) ===

The MPERS SOCIE template uses a flat per-row value layout — NOT the MFRS
24-col equity-component matrix. There are no per-equity-component columns
(no "Issued capital", "Retained earnings", "Reserves" columns).

### MPERS Company — single block
One vertical block of data rows (approx. rows 3–24). Columns:

- **A**: Field label (text, read-only)
- **B** (col=2): Current-period value
- **C** (col=3): Prior-period value
- **D** (col=4): Source / evidence

CY and PY are **column-separated**, not row-separated — fill the same
row's col B for CY and col C for PY.

### MPERS Group — four vertical blocks
The template stacks four copies of the data rows with the same labels in
each block — there are NO additional value columns (no E/F). Each block
has the same 4-col layout: A=label, B=value, D=source. Period and entity
are encoded by which block you write into, not by which column. Block
dividers are plain-text headers in column A:

| Block | Section header (row) | Typical row range |
|-------|---------------------|-------------------|
| 1 | `Group - Current period` (row 3) | rows 3–25 |
| 2 | `Group - Prior period` (row 27) | rows 27–49 |
| 3 | `Company - Current period` (row 51) | rows 51–73 |
| 4 | `Company - Prior period` (row 75) | rows 75–97 |

Because the same label (e.g. `Profit (loss)`) appears four times — once
per block — every group-filing write **must** include a `section` hint
naming the block header exactly. The writer uses it to pick the right row.

=== STRATEGY ===

1. Call `read_template()` to confirm the live row labels (they are the
   source of truth — this prompt lists the MBRS-standard labels but your
   local template is what the writer validates against).
2. View the Statement of Changes in Equity page in the PDF.
3. Fill each data row using **`field_label`** matching, not row coordinates:

   **Company filing example:**
   ```
   {"sheet": "SOCIE", "field_label": "Profit (loss)", "col": 2,
    "value": 322066, "evidence": "Page 14 statement of changes in equity"}
   ```

   **Group filing example (same label, four blocks):**
   ```
   {"sheet": "SOCIE", "field_label": "Profit (loss)",
    "section": "Group - Current period", "col": 2, "value": 322066, "evidence": "..."}
   {"sheet": "SOCIE", "field_label": "Profit (loss)",
    "section": "Group - Prior period", "col": 2, "value": 310000, "evidence": "..."}
   {"sheet": "SOCIE", "field_label": "Profit (loss)",
    "section": "Company - Current period", "col": 2, "value": ..., "evidence": "..."}
   {"sheet": "SOCIE", "field_label": "Profit (loss)",
    "section": "Company - Prior period", "col": 2, "value": ..., "evidence": "..."}
   ```

4. Data rows the agent typically fills (labels are case-insensitive; check
   `read_template()` for the exact text):
   - `Equity at beginning of period`
   - `Impact of changes in accounting policies` (blank if no restatement)
   - `Profit (loss)` — from SOPL
   - `Total other comprehensive income` — from SOCI (often blank/zero on MPERS)
   - `Acquisition (dilution) of equity interest in subsidiaries`
   - `Arising from conversion of Irredeemable Convertible Unsecured Loan Stock (ICULS)`
   - `Dividends paid` — enter as a **positive** magnitude because the
     Total increase/decrease formula subtracts this row
   - `Issuance of shares`
   - `Issue of convertible notes, net of tax`
   - `Increase (decrease) through share-based payment transactions, equity`
   - `Treasury shares transactions` — negative reduces equity
   - `Other transactions with owners`
   - `Increase (decrease) through other changes, equity`
   - `Equity at end of period` — closing balance

5. Call `fill_workbook()`, `verify_totals()` (status-only), then `save_result()`.

=== CRITICAL RULES (MPERS) ===

- **Use `field_label`, not `row` coordinates.** The only exception is the
  row 1 date cells — those have no label by design. Writing `{row: N, ...}`
  to any other row without a `field_label` will be rejected by the writer
  (guard added to prevent MFRS-shaped writes landing on blank MPERS rows).
- **Do NOT fill formula rows.** On MPERS the formula rows are the ones
  labelled with a leading `*` (e.g. `*Total comprehensive income`,
  `*Total increase (decrease) in equity`). These auto-calculate; writes
  to them are refused.
- **Leave cells blank where there is no activity.** Do not enter zeros —
  especially for `Total other comprehensive income` when the entity has
  no OCI items.
- **Dividends paid are entered as POSITIVE magnitudes.** The MPERS SOCIE
  template subtracts the dividends row in the Total increase/decrease formula
  (`... - dividends paid ...`), so a positive dividend input reduces equity.
  Do NOT enter dividends as negative unless the live formula no longer
  subtracts the row.
- Do not apply the SOPL "expenses/losses are positive" convention here.
  MPERS SOCIE is an equity movement statement: follow the formula sign for
  each row so closing equity reconciles to SOFP.
- **PY closing balance = CY opening balance** — the template does not
  auto-link these across rows; you must enter each period's opening
  balance explicitly.
- **On Group filings, every duplicate-label write needs a `section` hint.**
  The four blocks share labels; without the hint the writer defaults to
  block 1 and silently mis-files the other three blocks' values.
- **Closing `Equity at end of period` must match SOFP `Total equity`** —
  this is the `socie_to_sofp_equity` cross-check and it runs after
  extraction.
