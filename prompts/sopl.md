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

=== STRATEGY: FACE TOTALS WITH RECONCILED DISCLOSED COMPONENTS ===

SOPL uses a dedicated face-first workflow. The face figure is the control total.
Read the face page and its referenced revenue/expense notes once. Use complete,
source-disclosed breakdowns when they reconcile to the face figure; do not keep
searching for missing components or derive a remainder.

1. Call `read_template()` and view the profit-or-loss face page. Identify the
   writable leaves and the formula rollups for the active standard and variant.
2. For directly writable face lines, write the face figure as-is. Do not add
   the same amount to Analysis leaves unless the live formula requires them.
3. For formula-driven Revenue, Cost of sales, Other income, Employee benefits
   expense, Other expenses and Finance income, inspect the referenced note.
   Evaluate each period and entity scope independently: CY and PY, and Group
   and Company when applicable. Use `calculator` to check that the complete
   disclosed components reconcile exactly in the source presentation scale.
   Split only when every component has source evidence and a suitable writable
   destination contributing to that same face rollup. Map interest, royalties,
   salaries and defined-contribution expenses to their matching leaves.
   A source-disclosed Other component is valid; a subtraction-only residual is not.
4. If a breakdown is incomplete, inconsistent, absent or cannot be mapped
   without invention, do not split. Record the whole face figure once in the
   best supported broad leaf, normally the section's catch-all leaf. Cite the
   face page and report the unresolved breakdown/classification in `save_result`.
   Never retain the full face amount alongside its components. If revising a
   coarse write into components, clear the earlier coarse leaf with value=None and source evidence so the rollup
   does not double-count. Read live labels rather than assuming row numbers.
5. Revenue classification follows disclosed revenue substance. For a coarse
   revenue amount, inspect principal activities and choose the matching goods
   or services leaf in the live template. Mixed revenue requires a reconciled
   disclosed breakdown; do not allocate it by guessing from principal activity.
6. Call `write_facts()` with grounded mappings, `verify_totals()`, then
   `save_result()`. A mismatch stays visible; never invent a balancing figure.

=== WORKED EXAMPLES ===

**Reconciled other income:** CY interest income 45,431 equals face Other income
45,431: use the interest-income leaf. PY interest 36,646 plus disclosed Others
8,353 equals 44,999: use interest and the matching Other income leaf. Cite the
face and note pages for each period. Do not also write 44,999 as a coarse total.

**Incomplete breakdown:** Face Other expenses is 2,400,000 but the note discloses
only 1,900,000 of components. Record 2,400,000 once in the section's broad leaf,
report the incomplete breakdown, and never invent the missing 500,000.

**Directly writable line:** Administrative expenses 1,200,000 is written
straight to that face cell as a positive value.

=== NO-RESIDUAL-PLUG RULE ===

A cited face figure, or a disclosed class with no suitable dedicated leaf, may
use an Other row. NEVER invent a balancing, residual or unanalysed amount to
make `verify_totals()` pass. No arithmetic-only catch-all allocations.

=== CRITICAL RULES ===

- Expenses and losses charged to profit or loss are entered as POSITIVE
  magnitudes; the template formulas apply the sign. Follow the live per-row
  sign guidance where it says otherwise.
- EPS (earnings per share) fields: only fill if the entity is a listed company
  with shares. CLG companies and entities without share capital skip EPS.
