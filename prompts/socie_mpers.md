=== STATEMENT: SOCIE (Statement of Changes in Equity) — MPERS Default ({{VARIANT}}) ===

=== TEMPLATE STRUCTURE (MPERS) ===

The MPERS SOCIE template is a **flat two-column layout** — NOT a matrix.
There are no per-equity-component columns (no "Issued capital", "Retained
earnings", "Reserves" columns). Values for every equity movement go into
one of these columns:

- **A**: Field label (text, read-only)
- **B** (col=2): Current-period value
- **C** (col=3): Prior-period value
- **D** (col=4): Source / evidence (company filings only)
- Group filings additionally use: E (col=5) = Company CY, F (col=6) = evidence

### MPERS Company — single block
The template has one vertical block of data rows (approx. rows 3–24). CY
and PY are **column-separated**, not row-separated — fill the same row's
col B for CY and col C for PY.

### MPERS Group — four vertical blocks
The template stacks four copies of the data rows with the same labels in
each block. Block dividers are plain-text headers in column A:

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
   - `Dividends paid` — enter as a **negative** number
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
- **Dividends are NEGATIVE.** They reduce equity. Enter as a negative number.
- **PY closing balance = CY opening balance** — the template does not
  auto-link these across rows; you must enter each period's opening
  balance explicitly.
- **On Group filings, every duplicate-label write needs a `section` hint.**
  The four blocks share labels; without the hint the writer defaults to
  block 1 and silently mis-files the other three blocks' values.
- **Closing `Equity at end of period` must match SOFP `Total equity`** —
  this is the `socie_to_sofp_equity` cross-check and it runs after
  extraction.
