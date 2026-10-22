=== TASK: Notes 13 — Issued Capital ===

Sheet: `Notes-Issuedcapital`. This is a structured numeric movement
table. Each row is a specific line in the share-capital reconciliation
(opening balance → movements → closing balance), split into NUMBER of
shares and AMOUNT columns. The ISSUED-CAPITAL NUMERIC RULES below govern
labels, share classes, units and movements.

=== STRATEGY ===

1. Call `read_template` to see the 27 data-entry rows. Key rows to fill:
   - *Number of shares issued and fully paid (opening + changes + closing)
   - *Other changes in number of shares issued and fully paid
   - Amount of shares issued and fully paid (opening + movements + closing)
   - *Number of shares outstanding at beginning / end of period
   - *Amount of shares outstanding at beginning / end of period
2. Find every note that discloses issued capital (usually labelled "Share
   capital" or "Issued and paid-up share capital", typically Note 14-18
   range).
3. Emit one `NotesPayload` per matched row with `numeric_values` set.
   For company filings provide `company_cy` and `company_py` (or
   generic `cy` / `py`). For group filings provide only the disclosed keys
   among `group_cy`, `group_py`, `company_cy`, `company_py`. Never copy Group
   amounts into Company fields or vice versa; omit an entity/period scope the
   note does not disclose.
4. **ALSO reproduce the disclosed table.** In addition to the numeric
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
5. Call `write_notes` with the batch, then `submit_share_classes` listing
   every disclosed class as written or not_written, then `save_result`.
