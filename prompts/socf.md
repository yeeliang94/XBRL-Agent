=== STATEMENT: SOCF (Statement of Cash Flows) — {{VARIANT}} ===

=== TEMPLATE STRUCTURE ===

Single sheet with four sections: Operating Activities, Investing Activities, Financing
Activities, and Details of Cash (opening/closing balances).

For **Indirect** method (137 rows, 109 data-entry):
- Operating starts with Profit before tax, then adds back non-cash items (depreciation,
  amortisation, impairment, disposals), adjusts for working capital changes, and arrives
  at net cash from operations.
- Sub-sections: profit adjustments, impairment adjustments, disposal adjustments,
  write-off adjustments, working capital changes.
- Most input-heavy template (109 data-entry rows).

For **Direct** method (82 rows, 71 data-entry):
- Operating shows actual cash receipts and payments (receipts from customers, payments
  to suppliers, payments to employees, etc.).
- Simpler structure but less common in Malaysia.

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

- **Sign conventions are critical for SOCF:**
  - First decide the line's intended cash contribution `C`: inflow/add-back =
    positive; outflow/deduction = negative.
  - Then use the live per-row coefficient `k` and enter `V = C / k`. A row
    ADDED with +1 keeps the cash-direction sign. A row SUBTRACTED with -1
    takes the opposite input sign.
  - Indirect working-capital labels are standard-specific. A decrease in
    receivables or increase in payables has a positive cash contribution, but
    its TEMPLATE INPUT may be positive or negative depending on the live
    coefficient. Do not use the label direction as the stored sign.
  - Direct-method operating payments are normally ADDED and therefore
    negative. Many investing/financing payment rows are SUBTRACTED and
    therefore take positive magnitudes. Obey the live per-row block.
  - Do not import SOPL sign rules into SOCF. A "loss" adjustment may be a
    positive add-back, while a "payment" or "purchase" is normally negative
    as a cash contribution but may require a positive TEMPLATE INPUT when its
    subtotal subtracts it.

- **Closing cash ("Cash and cash equivalents at end of period") — check the
  template before writing; behaviour differs by template:**
  1. The **statement closing-balance line** sits immediately after
     "Cash and cash equivalents at beginning of period". In most SOCF
     templates this is a **blank data-entry row you MUST type directly**
     (it equals beginning + net increase (decrease) after FX) — do not leave
     it blank, it is the headline closing figure. In some templates it is
     instead a **live formula** (read_template shows a `=...` in that cell);
     when so, **leave it untouched** — never overwrite a template formula
     (it computes itself once opening cash and the prior lines are filled).
  2. Some templates **repeat the identical label under "Details of cash
     flows"** as a reconciliation total computed from the *Cash and bank
     balances* / *Bank overdraft* breakdown beneath it. **Never write to that
     formula row** — fill the breakdown rows, and Excel computes the total.
  So: fill the closing line ONLY when it is a blank cell, fill the Details
  breakdown rows, and never type over a `=...` formula. If a "mandatory row
  unfilled" or "cash at end" imbalance warning fires while the Details total
  already shows the right number, it is almost always a blank statement
  closing-balance line (item 1) that still needs typing.

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

- **When several source lines fold into one template row, sum them with the
  calculator before writing, and re-check the section subtotal after.** The
  template is often coarser than the source (e.g. one "trade and other
  receivables" row absorbing receivables, deposits, prepayments and
  pledged-deposit movements). List the source lines you are combining, sum
  them with the calculator tool (signs included), write the exact total, and
  then verify your section's lines sum to the section subtotal the source
  prints. A hand-combined aggregate that is close-but-wrong is the main
  cause of a cash-flow statement that will not articulate.

- **Prefer the most specific template row; use the notes to disambiguate.**
  The SOCF face often prints a coarse line ("Interest paid", "Impairment
  loss", "Loss on disposal") while a footnote beneath the statement, or the
  note it cross-references, reveals what the line actually is. When that
  detail matches a MORE SPECIFIC template row than the generic one, write to
  the specific row. For example, interest the notes identify as the interest
  portion of lease liabilities belongs in the lease-interest row, not the
  generic "Interest paid"; an impairment the notes attribute to a specific
  asset class belongs in that class's impairment row. If the template has NO
  specific row for the item, keep it on the generic line — never invent a
  row or force a wrong one (this is the common case on the leaner templates,
  where the granular rows simply do not exist).

- Lease payments under MFRS 16: principal → Financing activities, interest → Operating
  or Financing (entity's choice). Check the entity's classification policy.

- For "Purchase of property, plant and equipment", first set `C` negative as
  a cash outflow, then divide by that live row's coefficient. For an ordinary
  "Proceeds from disposal" receipt, set `C` positive and apply the same rule.
  The specialized discontinued-operation proceeds row (labelled either
  "Proceeds from disposal of net cash and cash equivalents disposed off" or
  "Disposal of discontinued operation, proceeds from disposal, net of cash
  and cash equivalents disposed of") is one SSM taxonomy exception: its
  linkbase subtracts the amount, so follow its authoritative per-row instruction.
  Do not hardcode template-input signs across standards or variants.

- Some entities combine operating + investing, or have no financing activities — this
  is normal. Leave unused sections blank.
