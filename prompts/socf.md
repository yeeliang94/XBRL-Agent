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
5. Call write_facts(), verify_totals(), and save_result().

=== CRITICAL RULES ===

- **A per-row sign block may be appended below** ("PER-ROW SIGN CONVENTIONS
  — AUTHORITATIVE"), built from this template's live `*Total …` formulas.
  When it lists a row, its ADD/SUBTRACT instruction is the single source of
  truth and OVERRIDES the generic rules in this section for that row. The
  generic rules below are the fallback for rows the block does not list.

- **Sign conventions are critical for SOCF:**
  - Indirect method: non-cash add-backs are POSITIVE (depreciation, impairment losses).
    Working capital: decrease in receivables = positive, increase in payables = positive.
  - Direct method: cash receipts = POSITIVE, cash payments = NEGATIVE.
  - Investing/financing: inflows = positive, outflows = NEGATIVE.
  - Do not import SOPL sign rules into SOCF. A "loss" adjustment may be a
    positive add-back, while a "payment" or "purchase" is normally negative
    because it is a cash outflow.

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

- "Purchase of property, plant and equipment" is a NEGATIVE value (cash outflow).
- "Proceeds from disposal" is a POSITIVE value (cash inflow).

- Some entities combine operating + investing, or have no financing activities — this
  is normal. Leave unused sections blank.
