# SOCF-Direct Fill Workflow

**Template:** `08-SOCF-Direct.xlsx`
**Sheets:** `SOCF-Direct` (single sheet, 82 rows)
**Based on:** FINCO FY2021 Audited Financial Statements

---

## Overview Strategy

The Direct method presents actual cash receipts and payments for operating activities
rather than reconciling from profit. Less common in Malaysia (most companies use indirect)
but used by some entities including FINCO.

**FINCO uses Direct method** — the SOCF maps directly to this template.

---

## Template Structure Summary

### Single Sheet (`SOCF-Direct`)
- Columns: A (labels), B (current year), C (prior year)
- 7 section headers: Operating, Investing, Financing, Details of cash flows
- 71 data-entry rows (almost entirely direct input)
- Only 2 formula rows: Cash totals in Details section
- **No cross-sheet references**

### Key Sections

**Operating Activities:**
- Cash receipts from customers, interest received, dividends received, grants received,
  insurance proceeds, tax refunds, other receipts
- Cash paid to suppliers, employees, interest paid, tax paid, other payments

**Investing Activities:**
- Same as Indirect template: PPE, intangibles, investments, subsidiaries, etc.

**Financing Activities:**
- Same as Indirect template: leases, shares, borrowings, dividends, etc.

**Details of Cash:**
- Opening/closing cash, bank overdraft, adjustments

---

## Field-by-Field Mapping Table

| PDF Line Item (FINCO SOCF) | Template Field | Template Section | Rule |
|---|---|---|---|
| Cash received from members | Receipts from customers | Operating activities | 3,196,875 CY / 3,652,396 PY |
| Cash paid to employees | Payments to employees | Operating activities | (897,501) CY / (793,604) PY — enter negative |
| Payment for other expenses | Payments to suppliers | Operating activities | (4,707,944) CY / (1,460,661) PY — enter negative |
| Purchase of equipment | Purchase of property, plant and equipment | Investing activities | (8,420) CY — outflow |
| Rental payments under lease | Repayment of lease liabilities | Financing activities | (35,875) CY — although FINCO classifies under investing |
| Cash at beginning | Cash and cash equivalents at beginning | Details | 5,003,869 CY / 3,605,738 PY |
| Cash at end | Cash and cash equivalents at end | Details | 2,551,004 CY / 5,003,869 PY |

---

## Common Mistakes / Sign Conventions

1. **Cash receipts are POSITIVE, payments are NEGATIVE.** This differs from the indirect
   method where adjustments have varying signs.

2. **"Cash received from members"** maps to "Receipts from customers" — the template uses
   generic commercial terminology. Adapt entity-specific labels.

3. **"Payment for other expenses"** maps to "Payments to suppliers" — it's the catch-all
   for non-employee operating payments.

4. **Lease payments:** FINCO classifies under investing activities, but MFRS 16 says
   principal → financing, interest → operating or financing. The agent should follow the
   entity's actual classification in the SOCF.

5. **Net change must reconcile:** Operating + Investing + Financing = Net change.
   Opening + Net change = Closing. Closing must match SOFP cash.

---

## Worked Example: FINCO FY2021

```json
{"fields": [
  {"sheet": "SOCF-Direct", "field_label": "Receipts from customers", "section": "operating activities", "col": 2, "value": 3196875, "evidence": "SOCF face, cash received from members CY"},
  {"sheet": "SOCF-Direct", "field_label": "Receipts from customers", "section": "operating activities", "col": 3, "value": 3652396, "evidence": "SOCF face, PY"},
  {"sheet": "SOCF-Direct", "field_label": "Payments to employees", "section": "operating activities", "col": 2, "value": -897501, "evidence": "SOCF face, cash paid to employees CY"},
  {"sheet": "SOCF-Direct", "field_label": "Payments to employees", "section": "operating activities", "col": 3, "value": -793604, "evidence": "SOCF face, PY"},
  {"sheet": "SOCF-Direct", "field_label": "Payments to suppliers", "section": "operating activities", "col": 2, "value": -4707944, "evidence": "SOCF face, payment for other expenses CY"},
  {"sheet": "SOCF-Direct", "field_label": "Payments to suppliers", "section": "operating activities", "col": 3, "value": -1460661, "evidence": "SOCF face, PY"},
  {"sheet": "SOCF-Direct", "field_label": "Purchase of property, plant and equipment", "section": "investing activities", "col": 2, "value": -8420, "evidence": "SOCF face"},
  {"sheet": "SOCF-Direct", "field_label": "Repayment of lease liabilities", "section": "investing activities", "col": 2, "value": -35875, "evidence": "SOCF face, rental payments (FINCO classifies under investing)"},
  {"sheet": "SOCF-Direct", "field_label": "Cash and cash equivalents at beginning of the financial year", "section": "details", "col": 2, "value": 5003869, "evidence": "SOCF face"},
  {"sheet": "SOCF-Direct", "field_label": "Cash and cash equivalents at beginning of the financial year", "section": "details", "col": 3, "value": 3605738, "evidence": "SOCF face, PY"},
  {"sheet": "SOCF-Direct", "field_label": "Cash and bank balances", "section": "details", "col": 2, "value": 2551004, "evidence": "SOFP CY cash"},
  {"sheet": "SOCF-Direct", "field_label": "Cash and bank balances", "section": "details", "col": 3, "value": 5003869, "evidence": "SOFP PY cash"}
]}
```

### Verification

| Check | CY | PY |
|---|---|---|
| Net cash from operating | (2,408,570) | 1,398,131 |
| Net cash from investing | (44,295) | 0 |
| Net cash from financing | 0 | 0 |
| Net change | (2,452,865) | 1,398,131 |
| Opening + Net change | 5,003,869 - 2,452,865 = 2,551,004 | 3,605,738 + 1,398,131 = 5,003,869 |
| Matches SOFP cash? | Yes | Yes |
