=== TASK: Notes 14 — Related-Party Transactions ===

Sheet: `Notes-RelatedPartytran`. This is a structured numeric table of
related-party transactions by type (dividend income, management fees,
rental expense, etc.) plus outstanding balances at period end. Each numeric
figure also needs the related-party category that selects its native column.

=== REQUIRED CATEGORY FOR EVERY NUMERIC FIGURE ===

Every payload containing `numeric_values` must also contain non-empty
`dimensions={axis: member}` using the exact identifiers in NUMERIC CATEGORY
IDENTITIES for this filing standard. The transaction type selects the row;
the relationship to the reporting entity selects the category column. A
correct amount with an omitted or empty category is an incomplete extraction.
This applies to transactions AND outstanding balances, including zero values.

Determine the relationship from the table heading, counterparty description,
corporate-information note and any referenced balance note. Read those pages
before submitting the figure when the transaction line alone is unclear.
Do not stop at the words "related company" or "related corporation" without
checking that context. Use the catalog category supported by the relationship:
parent, entities with joint control or significant influence, subsidiaries,
associates, joint ventures, key management personnel, or other related parties.
For example, a fee paid to a disclosed parent uses the Parent category; a fee
with a disclosed fellow subsidiary under common control uses Other related
parties, not Subsidiaries of the reporting entity. Disclosed directors'
compensation uses Key management personnel. These are relationship examples,
not keyword defaults: cite the source relationship in `evidence`.

Emit separate payloads when the same transaction row has amounts for different
categories. Keep each category's disclosed periods and entity scopes together;
do not merge different relationships merely because their row labels match.
Do not use Other related parties or Total simply to fill a missing category,
invent a relationship, or split an aggregate without supporting disclosure.
If the source genuinely cannot establish a category after those checks,
preserve the disclosed figures in the reproduced note and explicitly report
the unresolved relationship with its pages and amounts in the final response.
Do not submit an unclassified numeric payload or describe it as completed.

=== STRATEGY ===

1. Call `read_template` to see the ~33 data-entry rows. Key row groups:
   - Transaction types (rows 6-33): Dividend income, Management fees,
     Rental expense / income, Purchases / sales of goods, etc.
   - Outstanding balances (rows 34-37): Amounts payable / receivable.
2. Find the related-party-transactions note in the PDF. It's typically
   one of the last notes, labelled "Related party transactions" or
   "Related party disclosures" and presented as a table listing
   transactions with subsidiaries, associates, directors, and
   significant shareholders.
3. For each transaction type that the PDF discloses, emit a payload
   with both `numeric_values` and its source-supported `dimensions` set.
   For company filings use `company_cy` /
   `company_py`. For group filings provide only the disclosed keys among
   `group_cy`, `group_py`, `company_cy`, `company_py`. Never copy Group
   amounts into Company fields or vice versa; omit undisclosed scopes.
4. Transactions that don't map to any listed row (rare) are skipped.
   Do NOT invent values for rows that aren't in the PDF.
   A value obtained only by subtracting one printed subtotal from another is a
   **subtraction-only residual**, not a disclosed transaction. Leave the row
   blank unless the source prints that component as its own line. Do not infer
   "Other key management personnel" (or any other catch-all row) merely so the
   template adds back to the printed total.
5. **ALSO reproduce the disclosed table.** In addition to the numeric
   rows above, emit ONE prose `NotesPayload` whose `content` is the
   related-party transactions table reproduced verbatim as an HTML
   `<table>` (follow the SCHEDULES VS PROSE and CELL FORMAT rules in
   the base prompt — `<th>` header row, `<td>` body cells, accountant
   formatting preserved). Target it at the `Disclosure of transactions
   between related parties` text-block row — copy that label verbatim
   from the TEMPLATE ROW LABELS list as `chosen_row_label`. This prose
   payload needs its own `evidence`
   + `parent_note` like any other. Only do this when the PDF actually
   shows a table; if the note is plain prose with no schedule, skip the
   reproduction. The numeric grid above still fills exactly as before —
   this is an addition, not a replacement.
6. Before calling `write_notes`, check that every numeric payload has its
   category and source evidence. Resolve missing categories now; do not leave
   them for the exporter or a later reviewer. Before `save_result`, check that
   all disclosed numeric items were either submitted with a category or
   explicitly reported as unresolved with their source figures preserved.

=== NOTES ===

- Sign convention: income is positive; expenses are positive. Don't
  pre-negate expenses.
- If the PDF aggregates multiple line items (e.g. "Management fees and
  administrative charges"), pick the best-matching row and copy the
  aggregate value. Note the aggregation in `evidence`.
- The outstanding-balances rows capture the year-end position, not the
  in-year flow. Keep them distinct from the transaction rows.
- If the PDF shows amounts in RM '000, preserve that scale when writing.
  The evidence column is a good place to note the scale.
