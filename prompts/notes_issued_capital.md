=== TASK: Notes 13 — Issued Capital ===

Sheet: `Notes-Issuedcapital`. This is a structured numeric movement
table. Each row is a specific line in the share-capital reconciliation
(opening balance → movements → closing balance), split into NUMBER of
shares and AMOUNT columns.

=== STRATEGY ===

1. Call `read_template` to see the 27 data-entry rows. Key rows to fill:
   - *Number of shares issued and fully paid (opening + changes + closing)
   - *Other changes in number of shares issued and fully paid
   - Amount of shares issued and fully paid (opening + movements + closing)
   - *Number of shares outstanding at beginning / end of period
   - *Amount of shares outstanding at beginning / end of period
2. Find the share-capital note in the PDF (usually labelled "Share capital"
   or "Issued and paid-up share capital", typically Note 14-18 range).
3. The note contains a movement table. Extract the numeric values by line.
4. Emit one `NotesPayload` per matched row with `numeric_values` set.
   For company filings provide `company_cy` and `company_py` (or
   generic `cy` / `py`). For group filings provide all four of
   `group_cy`, `group_py`, `company_cy`, `company_py` — group filings
   typically disclose both consolidated and standalone figures.
5. **ALSO reproduce the disclosed table.** In addition to the numeric
   rows above, emit ONE prose `NotesPayload` whose `content` is the
   share-capital movement table reproduced verbatim as an HTML
   `<table>` (follow the SCHEDULES VS PROSE and CELL FORMAT rules in
   the base prompt — `<th>` header row, `<td>` body cells, accountant
   formatting preserved). Target it at the top-level disclosure
   text-block row — the "Disclosure of …" row near the top of this
   sheet (it sits just under the sheet-title row, above the numeric
   line items). Copy that row's label verbatim from the TEMPLATE ROW
   LABELS list as `chosen_row_label`. This prose payload needs its own
   `evidence` + `parent_note` like any other. Only do this when the PDF
   actually shows a table; if the note is a single line with no
   schedule, skip the reproduction. The numeric grid above still fills
   exactly as before — this is an addition, not a replacement.
6. Call `write_notes` with the batch, then `save_result`.

=== NOTES ===

- Number-of-shares rows take integer counts. Amount rows take RM values
  (check the unit at the top of the note — values may be in RM '000).
- "Issued for cash under ESOS" and "Private placement" rows are only
  filled when the PDF specifically mentions these as movement causes.
  If the PDF shows a generic "Issue of shares" line, put it in
  "Shares issued during financial year" instead.
- If the company didn't issue / repurchase shares during the year the
  opening and closing balances will be equal — fill both rows.
- Rows starting with `*` are calculation rows in some templates — they
  may be safe to write to, but the writer refuses to overwrite formula
  cells so any true formula row will be skipped with a warning.
