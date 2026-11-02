=== STATEMENT: SOCF (Statement of Cash Flows) — {{VARIANT}} ===

=== TEMPLATE STRUCTURE ===

Single sheet with four sections: Operating Activities, Investing Activities, Financing
Activities, and Details of Cash (opening/closing balances).

For the **Indirect** method, Operating starts with Profit before tax, adds back
non-cash items, adjusts for working-capital changes and arrives at net cash
from operations. For the **Direct** method, Operating shows actual cash
receipts and payments. `read_template()` lists the live rows.

=== STRATEGY ===

1. Call read_template() to understand the template sections.
2. View the Statement of Cash Flows page(s) in the PDF.
3. For **Indirect method:**
   a. Start with Profit before tax from the SOPL.
   b. Map each reconciliation adjustment (depreciation, interest, etc.) from the SOCF face.
   c. Map working capital changes.
   d. Map investing and financing activities.
   e. Fill opening cash, the closing-balance line, and the Details section
      (see the closing-cash rule below — check the template for whether the
      closing line is blank or a formula).
4. For **Direct method:**
   a. Map each cash receipt/payment line directly from the SOCF face.
   b. Map investing and financing activities.
   c. Fill opening cash, the closing-balance line, and the Details section
      (see the closing-cash rule below — check the template for whether the
      closing line is blank or a formula).

=== CRITICAL RULES ===

- **A per-row sign block may be appended below** ("PER-ROW SIGN CONVENTIONS
  — AUTHORITATIVE"), built from this template's live `*Total …` formulas.
  When it lists a row, its ADD/SUBTRACT instruction is the single source of
  truth and OVERRIDES the generic rules in this section for that row. The
  generic rules below are the fallback for rows the block does not list.

- **Signs:** decide each line's cash contribution `C` (inflow or add-back
  positive, outflow or deduction negative), then enter `V = C / k` using the
  live row coefficient `k`. Do not take the stored sign from the label's
  direction or from SOPL conventions. The shared SIGN-CONVENTION
  TROUBLESHOOTING section applies.

- **Closing cash:** fill every blank data-entry line the statement prints,
  including the closing-balance line after "at beginning of period" when it is
  a data-entry cell. Never type over a formula. Where the same label repeats as
  a formula reconciliation total (for example under "Details of cash flows"),
  fill its component rows instead. A "cash at end" or mandatory-row warning
  while the details total is already right usually means the blank statement
  closing line still needs its value.

- Closing cash will be cross-checked against SOFP cash and bank balances LATER.
  You only see the SOCF here, so you cannot perform that check yourself — enter
  the cash-flow lines correctly so closing cash equals what the SOCF face reports.
- Opening cash must equal prior year's closing cash.
- Net change = Operating + Investing + Financing.
- Opening + Net change = Closing.

- **Pledged deposits: read the cash note's reconciliation for
  BOTH years before writing opening or closing cash.** Deposits pledged as
  security are often cash at bank but NOT cash equivalents
  for cash-flow purposes — the cash note usually reconciles the SOFP balance
  down to the SOCF figure by deducting them, and the deduction can differ
  between the current and prior year. Use the note's SOCF-side figure for
  opening and closing cash, never the raw SOFP balance.

- **Assign each line to the section the SOURCE statement prints it in, not
  the section its wording suggests.** "Adjustments for" items and "Changes in
  working capital" items can carry similar labels (e.g. a fair-value movement
  on financial assets); what decides the template section is WHERE the line
  physically sits in the source statement — before or after the "Operating
  profit/surplus before changes in working capital" subtotal. Locate that
  subtotal first, then classify each line by its position relative to it.
  When a template label occurs in more than one section, pass the exact
  template section in `section`; the writer rejects unresolved duplicates
  instead of choosing the first row.

- **When several source lines fold into one template row, sum them with the
  calculator before writing, and re-check the section subtotal after.** The
  template is often coarser than the source (e.g. one "trade and other
  receivables" row absorbing receivables, deposits, prepayments and
  pledged-deposit movements). List the source lines you are combining, sum
  them with the calculator tool (signs included), write the exact total, and
  then verify your section's lines sum to the section subtotal the source
  prints. A hand-combined aggregate that is close-but-wrong is the main
  cause of a cash-flow statement that will not articulate.

- **Preserve the source activity section before choosing a specific row.**
  Record the source line, page, disclosed amount, unit and whether it is cash,
  principal, interest, a combined payment, or a noncash adjustment in evidence.
  Choose the most specific compatible row within that source activity section;
  a more specific label in another section does not justify reclassification.
  The SOCF face often prints a coarse line ("Interest paid", "Impairment
  loss", "Loss on disposal") while a footnote beneath the statement, or the
  note it cross-references, reveals what the line actually is. When that
  detail matches a MORE SPECIFIC template row than the generic one, write to
  the specific row. For example, interest the notes identify as the interest
  portion of lease liabilities belongs in a lease-interest row only when its
  activity section matches the source; otherwise retain the compatible generic
  "Interest paid" row. An impairment the notes attribute to a specific
  asset class belongs in that class's impairment row. If the template has NO
  specific row for the item, keep it on the generic line — never invent a
  row or force a wrong one (this is the common case on the leaner templates,
  where the granular rows simply do not exist).

- **Combined payments:** split a payment (for example lease principal and
  interest) only when the source identifies the components. Classify each part
  where the entity's stated policy and the source statement put it. Never
  subtract separately disclosed interest from a payment already labelled
  principal. Account for each cash component once within a period/entity's
  cash activities; a repeat in a note or an indirect add-back is not a second
  cash flow. List the components and
  arithmetic in `evidence`.

- Some entities combine operating + investing, or have no financing activities — this
  is normal. Leave unused sections blank.
