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
   e. Fill the Details section (opening/closing cash).
4. For **Direct method:**
   a. Map each cash receipt/payment line directly from the SOCF face.
   b. Map investing and financing activities.
   c. Fill the Details section.
5. Call fill_workbook(), verify_totals(), and save_result().

=== CRITICAL RULES ===

- **Sign conventions are critical for SOCF:**
  - Indirect method: non-cash add-backs are POSITIVE (depreciation, impairment losses).
    Working capital: decrease in receivables = positive, increase in payables = positive.
  - Direct method: cash receipts = POSITIVE, cash payments = NEGATIVE.
  - Investing/financing: inflows = positive, outflows = NEGATIVE.
  - Do not import SOPL sign rules into SOCF. A "loss" adjustment may be a
    positive add-back, while a "payment" or "purchase" is normally negative
    because it is a cash outflow.

- Closing cash MUST equal SOFP cash and bank balances — this is a P0 cross-check.
- Opening cash must equal prior year's closing cash.
- Net change = Operating + Investing + Financing.
- Opening + Net change = Closing.

- Lease payments under MFRS 16: principal → Financing activities, interest → Operating
  or Financing (entity's choice). Check the entity's classification policy.

- "Purchase of property, plant and equipment" is a NEGATIVE value (cash outflow).
- "Proceeds from disposal" is a POSITIVE value (cash inflow).

- Some entities combine operating + investing, or have no financing activities — this
  is normal. Leave unused sections blank.
