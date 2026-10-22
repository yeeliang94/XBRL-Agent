=== TASK: Notes 14 — Related-Party Transactions ===

Sheet: `Notes-RelatedPartytran`. This is a structured numeric table of
related-party transactions by type (dividend income, management fees,
rental expense, etc.) plus outstanding balances at period end. Each numeric
figure also needs the related-party category that selects its native column.
The RELATED-PARTY NUMERIC RULES below govern categories, balances, signs and
scale.

=== STRATEGY ===

1. Call `read_template` to see the ~33 data-entry rows. Key row groups:
   - Transaction types (rows 6-33): Dividend income, Management fees,
     Rental expense / income, Purchases / sales of goods, etc.
   - Outstanding balances (rows 34-37): Amounts payable / receivable.
2. Find the related-party-transactions note in the PDF. It's typically
   one of the last notes, labelled "Related party transactions" or
   "Related party disclosures" and presented as a table listing
   transactions with subsidiaries, associates, directors, and
   significant shareholders. Then check the balance notes it references.
3. For each transaction type and balance that the PDF discloses, emit a payload
   with both `numeric_values` and its source-supported `dimensions` set.
   For company filings use `company_cy` /
   `company_py`. For group filings provide only the disclosed keys among
   `group_cy`, `group_py`, `company_cy`, `company_py`. Never copy Group
   amounts into Company fields or vice versa; omit undisclosed scopes.
4. **ALSO reproduce the disclosed table, when present, and narrative.** In addition to the numeric
   rows above, emit ONE prose `NotesPayload` containing the related-party
   disclosure. If the source includes a table, reproduce it verbatim as an HTML
   `<table>` (follow the SCHEDULES VS PROSE and CELL FORMAT rules in
   the base prompt — `<th>` header row, `<td>` body cells, accountant
   formatting preserved), together with its disclosed narrative. If the note
   has no table, reproduce its narrative in this same payload.
   Target it at the `Disclosure of transactions
   between related parties` text-block row — copy that label verbatim
   from the TEMPLATE ROW LABELS list as `chosen_row_label`. This prose
   payload needs its own `evidence`
   + `parent_note` like any other. The numeric grid above still fills exactly as before —
   this is an addition, not a replacement.
5. Before calling `write_notes`, check that every numeric payload has its
   category and source evidence. Before `save_result`, complete the
   reconciliation in the numeric rules below.
