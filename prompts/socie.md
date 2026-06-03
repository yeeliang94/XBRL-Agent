=== STATEMENT: SOCIE (Statement of Changes in Equity) — {{VARIANT}} ===

=== TEMPLATE STRUCTURE ===

Single sheet with a MATRIX layout — rows are equity movements, columns are equity
components. This is the most complex template structure.

**Column layout (B through X):**
- B: Issued capital
- C: Retained earnings
- D: Treasury shares
- E-L: Non-distributable reserves (capital, hedging, FX translation, share-based,
  revaluation, statutory, warrant, other)
- M: Sub-total non-distributable (FORMULA: SUM E:L)
- N-R: Distributable reserves (fair value, held-for-sale, consolidation, warranty, other)
- S: Sub-total distributable (FORMULA: SUM N:R)
- T: Reserves total (FORMULA: M+S)
- U: Equity attributable to owners (FORMULA: B+C+D+T)
- V: Equity other components
- W: Non-controlling interests
- X: Total (FORMULA: U+V+W)

**Row layout (duplicated for two periods) — MFRS Company linear template:**
- Rows 6-25: Current period movements
- Rows 30-49: Prior period movements

These ranges are the **MFRS Company** starting anchor ONLY. ALWAYS confirm the
actual movement-row numbers from read_template() before writing — they DIFFER
on Group filings (a 4-block layout at rows 3-25 / 27-49 / 51-73 / 75-97,
described in the Group overlay below) and can shift if the template is
regenerated. Anchor on read_template()'s row labels, not these literals.

Each period: opening balance → accounting policy changes → restated opening → profit/loss
→ OCI → TCI → contributions/distributions (dividends, share issuance, etc.) → total change
→ closing balance.

**Most rows are FORMULAS** that SUM across columns. You write values into specific
CELL INTERSECTIONS (row × column), not entire rows.

=== STRATEGY ===

1. Call read_template() FIRST and read off the ACTUAL row number for each
   movement you will write — profit/(loss), OCI/revaluation, dividends paid,
   share issuance, share-based payments, and equity-at-end. The row numbers in
   the examples below are illustrative; read_template()'s labels are
   authoritative (the literals drift on Group / regenerated templates).
2. View the Statement of Changes in Equity page in the PDF.
3. Identify which equity components the entity has (columns to fill). Columns
   are stable: B = Issued capital, C = Retained earnings, D = Treasury shares,
   reserves E-L, NCI = W, Total = X (a FORMULA — never write it).
4. For each movement row, fill the value in the CORRECT column using EXPLICIT
   ROW + COL coordinates (not label matching — this is a matrix template).
   Confirm the ROW from read_template(); the COLUMN is fixed by the movement
   type. One worked write_facts example per movement type (replace <row> with
   the read_template() row for that movement):
   - Profit/(loss) → column 3 (C = Retained earnings):
     {"sheet": "SOCIE", "row": <profit row>, "col": 3, "value": 1250000, "evidence": "..."}
   - OCI item, e.g. revaluation surplus → its reserve column (revaluation = column 9 = I):
     {"sheet": "SOCIE", "row": <OCI row>, "col": 9, "value": 80000, "evidence": "..."}
   - Dividends paid → column 3 (C = Retained earnings), POSITIVE magnitude
     (the Total increase/decrease formula subtracts this row):
     {"sheet": "SOCIE", "row": <dividends row>, "col": 3, "value": 500000, "evidence": "..."}
   - Share issuance → column 2 (B = Issued capital):
     {"sheet": "SOCIE", "row": <share-issue row>, "col": 2, "value": 2000000, "evidence": "..."}
   - Share-based payments → column 8 (H):
     {"sheet": "SOCIE", "row": <SBP row>, "col": 8, "value": 35000, "evidence": "..."}
5. Fill BOTH current period (Company linear: rows 6-25) and prior period
   (rows 30-49) — but read the actual ranges from read_template() (Group uses
   the 4-block layout in the overlay below).
6. Call write_facts(), verify_totals() (reports status only), and save_result().

=== CRITICAL RULES ===

- **Do NOT fill formula columns** (M, S, T, U, X). These auto-calculate.
- **Leave cells BLANK** where there is no activity. Do not enter zeros.
- **Profit always goes to Retained earnings column (C)** — even if the entity calls it
  "accumulated fund", "revenue reserve", or "retained profit".
- **Closing equity will be cross-checked against SOFP total equity LATER.** You
  only see the SOCIE here, so you cannot perform that check yourself — enter the
  SOCIE movements correctly from this statement so closing equity is right.
- **PY closing balance = CY opening balance** — the template should auto-link these.
- **NCI column (W)** only for group/consolidated accounts.
- **Dividends paid are entered as POSITIVE magnitudes.** The SOCIE template
  subtracts the dividends row in the Total increase/decrease formula
  (`... - dividends paid ...`), so a positive dividend input reduces retained
  earnings. Do NOT enter dividends as negative unless the live formula no
  longer subtracts the row.
- **Share buybacks/treasury shares** → column D as negative.
- Do not apply the SOPL "expenses/losses are positive" convention here.
  SOCIE is an equity movement statement: follow the formula sign for each
  row so closing equity reconciles to SOFP.
- **Two complete periods required** — both CY and PY blocks must be filled.

- For simple entities (single equity component like "accumulated fund"), only column C
  gets values. All other columns stay blank. This is normal and correct.
