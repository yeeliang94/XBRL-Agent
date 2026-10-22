=== RELATED-PARTY NUMERIC RULES ===

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
checking that context. Classify from the reporting entity's perspective. Use
the catalog category supported by the relationship: parent, entities with
joint control or significant influence, subsidiaries, associates, joint
ventures, key management personnel, or other related parties.
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

=== TRANSACTIONS AND OUTSTANDING BALANCES ===

The outstanding-balances rows capture the year-end position, not the in-year
flow. Keep them distinct from the transaction rows. Balances are often
disclosed outside the related-party transactions note, in the receivables,
payables or other balance notes, or referenced from the transactions note.
Inspect those notes for amounts due from or owing to related parties before
deciding no balance is disclosed.

When several distinct source items map to the same row, category, period and
entity scope, add them with calculator and submit ONE combined figure. Name
each component and its amount in `evidence`. Never send them as separate
payloads: a later payload for the same row and category replaces the earlier
value, and two different values in one call are rejected.

If the PDF aggregates multiple line items (e.g. "Management fees and
administrative charges"), pick the best-matching row and copy the aggregate
value. Note the aggregation in `evidence`.

A value obtained only by subtracting one printed subtotal from another is a
**subtraction-only residual**, not a disclosed transaction. Leave the row
blank unless the source prints that component as its own line. Do not infer
"Other key management personnel" (or any other catch-all row) merely so the
template adds back to the printed total. Transactions that don't map to any
listed row (rare) are skipped. Do NOT invent values for rows that aren't in
the PDF.

=== SIGNS AND SCALE ===

- Use the destination row's convention, not the source table's. Income,
  expenses, purchases, sales, amounts receivable and amounts payable are all
  entered as positive magnitudes. The row label already says which direction
  the amount runs. Brackets that a source table uses to show expenses,
  payments or payables do not make the filed value negative. Enter a negative
  value only when the source states a reversal or a negative amount for that
  same concept.
- If the PDF shows amounts in RM '000, preserve that scale when writing.
  The evidence column is a good place to note the scale.

=== BEFORE save_result ===

Check that every numeric payload has its category and source evidence.
Reconcile against the source: every disclosed related-party transaction and
outstanding balance, for every disclosed period and entity scope, was either
written with a category or explicitly reported as unresolved with its source
pages and amounts. Resolve missing categories now; do not leave them for the
exporter or a later reviewer.
