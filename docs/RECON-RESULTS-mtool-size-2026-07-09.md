# RECON RESULTS — mTool payload size (2026-07-09)

Operator-run recon per docs/MTOOL-NOTES-FORMAT-RECON.md ("Size recon" section)
+ docs/AGENT-BRIEF-mtool-size-recon-windows.md. mTool 2.1, taxonomy 2022v1.0,
Amgen MFRS test filing. Test note: the 55-row × 6-col styled table from
`mtool/examples/recon_size_test_note.html`, pasted into the
Notes-SummaryofAccPol row-11 popup (`fn_14`). Full verbatim dumps live in the
operator's email; this file keeps the decision-bearing evidence.

## Headline findings

1. **mTool's native serialisation is ~5× HEAVIER than ours, not lighter.**
   TX Text Control (TX27_HTM 27.0.700.500) stores styling inline, repeated on
   every cell, with no classes/stylesheet: each `<td>` carries width + 4
   padding props + 4 border props, PLUS a nested `<p>` inside every cell with
   its own 5 margin props. Measured: **~395 chars per cell block** vs our
   decorator's ~81 post-hoist. There is no compact trick to mimic.

2. **The payload was NOT actually over Excel's limit — CORRECTED 2026-07-09**
   (the first write-up of this finding over-claimed). The dumper counted
   **34,431 STORED chars**, but the stored form carries a 7-char `_x000D_`
   line-break token per line that Excel decodes to a single CR on load.
   TX's output has ~1,160 line breaks → ~7k of token overhead → **Excel's
   own count for that cell is ~27.5k, comfortably UNDER 32,767.** So this
   recon did NOT test Excel past the limit; Excel's over-limit
   truncate-and-repair behaviour (the 2026-07-06 incident that motivated
   the guard) remains the operative assumption. The doubling probe is the
   only way to measure the real ceiling.

3. **mTool is an Excel ADD-IN, not a standalone app** (operator, verbatim:
   "the mtool is an excel add in. they will need to open excel to check and
   save and generate xbrl"). Every save/Validate/Generate goes through
   Excel itself — the 32,767 limit is a fully real workflow constraint,
   with no mTool-only escape hatch.

## Evidence samples (verbatim from the dump)

Opening table tag (91 chars — table-level styling is thin):

```
<table cellspacing="0" cellpadding="0pt" style="width:1416.65pt;border-collapse:collapse;">
```

One complete cell block (395 chars — the per-cell weight):

```
<td valign="middle" style="width:441.7pt; padding-top:3pt; padding-right:6pt; padding-bottom:3pt; padding-left:6pt; border-top: 1pt solid #999999; border-right: 1pt solid #999999; border-bottom: 1pt solid #999999; border-left: 1pt solid #999999;">
<p style="text-indent:0pt;margin-left:0pt;margin-top:0pt;margin-bottom:0pt;margin-right:0pt;">Trade receivable category 1</p>
</td>
```

## Other observations

- Payload begins with a literal `ABC` prefix before the XML declaration
  (consistent with the known wrap shape); `_x000D_` CR tokens throughout;
  NBSP / em-dash round-trip as mojibake in the dump (`┬á`, `ÔÇö`) — useful
  for byte-level comparisons, not a defect.
- `fn_14`'s payload landed in **column F**, not the column C seen in other
  templates — the payload column varies by template; readers must stay
  column-flexible (the dumper already is).
- First open after paste rendered the first column over-wide (table looked
  horizontally truncated); on reopen mTool re-fit the table to page width.
  Layout normalises on reopen — a first-open editor behaviour, not a saved
  defect. Track width behaviour separately from size.

## What this decides (Step 5 of docs/PLAN-mtool-compact-decoration.md)

- **"Mimic mTool's compact form" — DEAD.** Nothing to mimic; ours is already
  ~5× lighter than native.
- **Compact tier — CONFIRMED as the fix.**
- **Do NOT relax the 32,767 guard.** Per corrected finding #2, Excel was
  never actually tested over the limit — the 2026-07-06 truncate-and-repair
  incident stands as the known over-limit behaviour. A future Windows
  session can run the doubling probe to measure the real ceiling; until
  then the guard is load-bearing.
- **Guard-accuracy note:** our guard measures the STORED length
  (`len(wrap_footnote_html(...))`), which overcounts vs Excel's decoded
  length by ~6 chars per line break. Our own wrapped output carries far
  fewer line breaks than TX's (the shell adds ~10; the decorated fragment
  is essentially one line), so for OUR writes the two counts are near
  identical — the guard is accurate where it matters and conservative
  otherwise. No change needed.
- **New durable risk — TX re-inflation:** if a user EDITS one of our notes
  inside mTool, TX re-serialises it in its ~5×-heavier native form on save
  (recon note: ~27.5k Excel-decoded for a 55×6 table). A large table we
  write compact could therefore cross the limit after a user edit, with
  Excel's truncate-and-repair as the failure mode. This is outside our
  control — it bounds how far "just write it smaller" ultimately
  stretches, and is the standing argument for keeping the degradation
  ladder + oversize flag even after the compact tier ships.
