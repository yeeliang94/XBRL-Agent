=== TASK: Notes 14 — Related-Party Transactions ===

Sheet: `Notes-RelatedPartytran`. This is a structured numeric table of
related-party transactions by type (dividend income, management fees,
rental expense, etc.) plus outstanding balances at period end.

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
   with `numeric_values` set. For company filings use `company_cy` /
   `company_py`. For group filings provide all four of `group_cy`,
   `group_py`, `company_cy`, `company_py`.
4. Transactions that don't map to any listed row (rare) are skipped.
   Do NOT invent values for rows that aren't in the PDF.
5. Call `write_notes` with the batch, then `save_result`.

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
