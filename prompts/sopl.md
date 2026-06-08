=== STATEMENT: SOPL (Statement of Profit or Loss) — {{VARIANT}} ===

=== TEMPLATE STRUCTURE ===

The SOPL template has two sheets:

1. **Main sheet** — the face of the income statement: Revenue, Cost of sales,
   Gross profit, operating expenses (by function or by nature), Finance
   income/costs, Tax expense, and the Profit/Loss attribution split.

2. **Analysis sub-sheet** — a long catalogue of fine-grained revenue and
   expense lines. For SOPL most of this sheet is left EMPTY on purpose (see
   the strategy below).

A handful of face lines are NOT directly writable — they are Excel formulas
that pull their value up from the Analysis sub-sheet, so the only way to make
the face line show a value is to write into the sub-sheet. On the **Function**
variant these are Revenue, Cost of sales, Other income, Other expenses, and
Finance income. On the **Nature** variant they are Total revenue, Other
income, Employee benefits expense, Other expenses, and Finance income. (Note:
the Nature face is NOT self-contained — these rollups apply to both variants.)
`read_template()` marks every formula cell — trust it. Never try to type a
value onto a formula cell: `write_facts()` refuses it and the formula would
overwrite you anyway.

=== STRATEGY: THE FACE STATEMENT IS THE TRUTH; STAY COARSE ===

SOPL is deliberately handled differently from the other statements. **This
coarse policy is SOPL's explicit exception to the ACCOUNTANT EXTRACTION
PROCEDURE in the system prompt:** for SOPL revenue/expense lines, do NOT follow
note references to fill component rows — record the face figure coarsely as
described below. Do NOT go hunting through the notes for revenue/expense
breakdowns. In real Malaysian filings the income-statement notes are usually
incomplete and lump the remainder into "Others", so trying to decompose
comprehensively makes you loop and over-bucket. What we file is the face
statement's own figures.

1. Call `read_template()` to see which cells are data-entry vs. formula, and
   `view_pdf_pages()` on the income-statement / profit-or-loss face page. You
   do NOT need to open the detailed revenue/expense note pages.

2. For every face line that is a NORMAL data-entry cell — Selling &
   distribution, Administrative expenses, Research & development, Finance
   costs, Share of profit of associates/JVs, Tax expense, Zakat, discontinued
   operations, the attribution split (owners / non-controlling interests),
   EPS, and on the Nature variant the raw-materials / depreciation /
   inventory-movement lines — write the face figure exactly as printed. One
   number, taken as-is. If the entity discloses operating expenses by
   function, write each to its matching face row; if it discloses a single
   aggregate, write that to the one face row the entity used. Do not split
   further and do not sweep anything.

3. For each face line that is a FORMULA pulling from the Analysis sub-sheet
   (the handful listed above): the value still has to enter through the
   sub-sheet for the face formula to resolve. Write the SINGLE face figure
   into that section's broadest catch-all leaf — the "Other …" /
   "Miscellaneous …" row that `read_template()` shows is summed into the
   section's total. Use the face page as the evidence/source.
   - Do this **even if the financials show a breakdown.** We intentionally do
     NOT split SOPL revenue/expenses into the granular sub-sheet fields.
   - The exact label of the catch-all leaf differs between MFRS/MPERS and
     Function/Nature (e.g. "Other revenue", "Other cost of sales", the
     section's "Other/Miscellaneous … income" row, the section's "Other …
     employee …" expense row, "Other miscellaneous expenses", "Other finance
     income"). Read it off the
     template — pick the section's most generic "Other/Miscellaneous" leaf —
     rather than assuming a fixed name or row number.

4. Call `write_facts()` with all mappings, then `verify_totals()` to report
   status, then `save_result()` when complete.

This keeps you to a single pass: read the face, write the face, finish. No
note-diving, no reconciliation loop.

=== THIS IS COARSE RECORDING, NOT PLUGGING ===

Writing the real, page-cited face figure into an "Other …" catch-all leaf is
legitimate coarse recording — the entity's own income statement is the source
and the number is genuine. That is NOT the banned behaviour. The INTEGRITY
RULE in the system prompt still holds in full: you must NEVER invent a
"balancing", "residual", or "unanalysed" figure and write it to a catch-all
row to force `verify_totals()` to pass. The line is simple — a coarse face
figure is a real disclosed number you are recording at a coarse grain; a plug
is a number you made up to close a gap. Never plug.

=== WORKED EXAMPLES ===

**Coarse revenue (the normal case):** The face shows Revenue RM9,000,000 and
the revenue note breaks it into goods RM5m / services RM3m / fees RM1m. Do
NOT split. Write RM9,000,000 to the Analysis sub-sheet's "Other revenue" leaf,
evidence = the income-statement page. The face Revenue formula then reads
9,000,000.

**Coarse expenses:** The face shows Other expenses RM2,400,000. Write
RM2,400,000 to the section's "Other miscellaneous expenses" leaf, cited to the
face page. Do not go looking for the expense-breakdown note.

**Directly-writable line:** The face shows Administrative expenses
RM1,200,000 — write 1,200,000 straight to that face cell as a positive value.

=== CRITICAL RULES ===

- Expenses are entered as POSITIVE values in the template. The formulas handle
  sign conventions. Do not enter negative values for expenses.
- Loss-labelled expense rows are also POSITIVE magnitudes when they are P&L
  charges. Examples: "Foreign exchange loss", "Impairment loss on trade
  receivables", "Expected credit loss allowance", "Loss on disposal", and
  "Write-off of inventories" should be entered as positive values unless the
  live template formula explicitly requires the opposite.
- The main-sheet Revenue / Cost of sales / Other income / Other expenses /
  Finance income lines are formulas — enter their values through the Analysis
  sub-sheet's catch-all leaf as described above, never onto the face cell.
- "Income" or "Income and Expenditure" in non-profit entities maps to
  Revenue/Expenses.
- Tax expense of zero should still be entered as 0 (not left blank) if disclosed.
- EPS (earnings per share) fields: only fill if the entity is a listed company
  with shares. CLG companies and entities without share capital skip EPS.
- Never use a catch-all row as a balancing figure / plug / residual to make
  `verify_totals()` pass. A coarse face figure is fine (see above); an invented
  number is not. If something genuinely cannot reconcile, finish honestly with
  the gap flagged — never plug a residual.
