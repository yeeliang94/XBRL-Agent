=== STATEMENT: SOCIE — SoRE (Statement of Retained Earnings, MPERS) ===

=== TEMPLATE STRUCTURE ===

Single sheet (`SoRE`) — a **simplified retained-earnings schedule** used by
MPERS filings that elect to present only the retained-earnings movement in
place of a full SOCIE matrix. The template is one tall column of values, not
a matrix; there are no per-component reserve columns.

**Column layout:**
- A: Label
- B: Current period value
- C: Prior period value
- (Group filings also fill D = Company CY, E = Company PY.)

**Writable labels:**
- `Retained earnings at beginning of period`
- `Impact of changes in accounting policies` (if any; otherwise blank)
- `Retained earnings at beginning of period, restated`
- the writable LEAF occurrence of `Profit (loss)`
- `Dividends paid` (positive magnitude — the formula subtracts it)
- `Retained earnings at end of period`

The live template also contains ABSTRACT headings and `*Total …` formula rows.
They are not writable. Do not infer writability from a remembered row number.

=== STRATEGY ===

1. Call `read_template()` to confirm the labels and DATA_ENTRY/formula status.
2. View the Statement of Retained Earnings page in the PDF.
3. Fill writable rows by exact `field_label` (B for CY, C for PY). The duplicate
   `Profit (loss)` label includes an ABSTRACT heading; label matching selects
   the writable leaf. Use explicit coordinates only for the unlabelled
   reporting-period cells shown by `read_template()`.
4. **Do NOT fill any `*Total …` formula row.** The closing retained-earnings
   row is a DATA_ENTRY row in the current template, so extract it from the
   statement instead of assuming it is calculated.
5. Call `write_facts()`, `verify_totals()` (status-only), then `save_result()`.

=== CRITICAL RULES ===

- **Dividends paid are entered as POSITIVE magnitudes.** The SoRE template
  subtracts the dividends row (`retained earnings + profit - dividends`), so
  a positive dividend input reduces retained earnings. Do NOT enter dividends
  as negative unless the live formula no longer subtracts the row.
- Do not apply the SOPL "expenses/losses are positive" convention here.
  SoRE is a retained-earnings movement statement: follow the formula sign so
  closing retained earnings reconciles to SOFP.
- **Closing retained earnings must match SOFP "Retained earnings".**
  This is the SoRE cross-check — the one reconciliation that still runs after
  the SOCIE-consuming checks are gated out for SoRE filings.
- **Leave cells blank where there is no activity.** Do not enter zeros —
  especially for the accounting-policy impact when no restatement applies.
- **No equity-component columns.** If the PDF shows a share-capital or
  reserves movement, that goes on a different statement (SOFP / SOCIE on
  standard MPERS) — not this sheet.
- **Two periods required:** fill both CY (col B) and PY (col C). Group
  filings additionally fill Company CY (col D) and Company PY (col E) from
  the standalone figures.
