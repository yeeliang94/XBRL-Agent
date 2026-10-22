=== ISSUED-CAPITAL NUMERIC RULES ===

Read generic leaf labels in their full section hierarchy. In particular:
- `Shares issued and fully paid > Amount of shares issued and fully paid >
  Balance at the beginning/end of period` is a **monetary amount**, not a
  share count.
- `Shares outstanding > Number of shares outstanding > Number of shares
  outstanding at beginning/end of period` is a **share count**, not money.
- `Shares outstanding > Amount of shares outstanding > Amount of shares
  outstanding at beginning/end of period` is a monetary amount.
Never target a generic `Balance at ...` label without first naming which of
these parent paths it belongs to. The same leaf wording can mean a different
unit under a different parent.

Account for every share class the document discloses. Preference, redeemable
or convertible shares are often disclosed in a separate note; check the notes
list and the face equity section before deciding the ordinary-share note is
the whole disclosure. Extract each class's movement table by line, one payload
set per share class. Check the opening and closing monetary balances
independently of the share counts.

=== UNITS ===

Read the unit of every column separately. A share-count column and a money
column in the same table can carry different scales.
- Number-of-shares rows take whole individual shares. When the source labels
  a count in thousands (for example "'000 shares" or "Number of shares ('000)"),
  multiply the printed figure by 1,000 with calculator and state the printed
  figure and its unit in `evidence`. A count of 12,000 in '000 shares is
  12,000,000 shares, not 12,000.
- Amount rows preserve the PDF's presentation scale exactly. If the note shows
  2,500 in RM '000, write 2,500 — never convert it to 2,500,000 RM. Do not
  multiply or divide any monetary amount.
- If the note's money unit conflicts with the face statements (for example the
  note prints "RM" while the statements use RM '000), do not choose a scale
  silently. Use the note's printed figures as shown, and name the conflict and
  both units in `evidence` and in the final response.

=== MOVEMENTS ===

- "Issued for cash under ESOS" and "Private placement" rows are only
  filled when the PDF specifically mentions these as movement causes.
  If the PDF shows a generic "Issue of shares" line, put it in
  "Shares issued during financial year" instead.
- If the company didn't issue / repurchase shares during the year the
  opening and closing balances will be equal — fill both rows.
- Use the writable fields supplied by `read_template`. A leading `*` is not
  permission to write a heading or formula. When heading and leaf labels repeat,
  target the writable leaf in its full hierarchy. The numeric writer rejects
  headings and formulas; never treat a scratch-workbook write as proof that the
  value was saved to the filing data.

After all numeric writes, call `submit_share_classes` listing every disclosed
class as written or not_written, then `save_result`.
