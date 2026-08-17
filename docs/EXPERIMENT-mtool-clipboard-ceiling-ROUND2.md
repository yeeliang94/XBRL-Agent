# Round 2 brief: finish the mTool prose ceiling probe

**For:** the AI coding agent working with the operator on the Windows machine.
**Read first:** `docs/EXPERIMENT-mtool-clipboard-ceiling.md` (the original brief)
and your own round-1 `NOTES.md`. This document does not replace them; it says
what to do next and corrects one conclusion.

**Time:** about 25 minutes of desk work with no operator, then 60–70 minutes
with the operator.

---

## 1. Where round 1 got to

**Answered:**

- **Experiment A.** Word offers both `Rich Text Format` and `HTML Format`. The
  RTF theory is live but unproven. Nothing further is needed here.
- **Experiment B.** A byte-exact sample of mTool's own markup was captured.
  This is the round's real deliverable. Its measured dialect: point units,
  `<colgroup>` plus both legacy `width=` attributes and CSS widths,
  `border-top` and `border-bottom` only with no left or right borders anywhere,
  almost no fill, and no surviving Word-specific markup.

**Not answered:**

- **Experiment C.** All ten rows still pending.
- **Experiment D.** Reported as a negative result. That conclusion is being
  set aside — see section 2.
- **Size comparison.** `05-our-payload.xhtml` was never produced.

---

## 2. Correction: Experiment D's conclusion does not hold yet

Round 1 concluded that the write-back does not render identically, on the basis
that the write-back displays leading text `ABC` and the manual paste does not.

Identical bytes cannot render two different ways. If the two differ, mTool is
not reading the string that was captured. That is a lookup problem, not a
fidelity finding, and the original brief's section 9 requires ruling it out
before drawing any conclusion.

Two specific causes to eliminate, both already documented in this repository:

1. **Duplicate `fn_` key.** mTool joins the visible cell to its payload by the
   column-A string and reads the **first** matching row.
   `mtool/offline_fill.py::read_footnote_rows` keys by column A and keeps the
   **last** row. If `fn_49` appears on more than one row, round 1 captured a
   different row's payload than the one mTool renders. See the comment at
   `mtool/offline_fill.py:1560` and the detector `_detect_duplicate_fn_keys`.
2. **Column disagreement in the round-1 notes.** Experiment B records the
   payload in column F; Experiment D writes to column C. The repository states
   the layout as `A = fn_N`, `B = visible sheet`, `C = XHTML payload`
   (`mtool/offline_fill.py:243`). One of the two notes is wrong.

Note also that in the write-back screenshot **the table itself rendered**. That
is evidence toward the positive outcome, and it was not reflected in the round-1
conclusion.

Treat Experiment D as void, not negative, until section 5 is re-run.

---

## 3. Step 1 — settle the size measurement (desk work, no operator)

Round 1 reports the stored payload as 35,601 characters and observes that this
exceeds Excel's documented 32,767-character limit.

State plainly which length was measured:

- **Escaped length**, as the characters sit in `xl/sharedStrings.xml`, where
  every `<` is stored as `&lt;` — four characters instead of one.
- **Decoded length**, which is what Excel's limit applies to and what
  `EXCEL_CELL_CHAR_LIMIT` in `mtool/offline_fill.py` guards.

The sample contains roughly 1,500 tags. Escaping alone inflates the count by
several thousand characters, so a decoded length near 26,000 is plausible.
`get_shared_strings` unescapes, so a length taken through the repository's own
reader is already decoded and the finding stands as reported.

**Why this is first:** a prior recon (2026-07-09) reported an over-limit payload
that measured about 27,500 characters once decoded. If the same thing has
happened again, there is no size finding and C10 is testing nothing. If it has
not, mTool stores more than Excel documents, which reopens our export limits.

Record the answer as one line in `NOTES.md`: which length was measured, the
method, and the decoded number.

---

## 4. Step 2 — produce our own payload (desk work, no operator)

`05-our-payload.xhtml` is missing, so there is no size or shape comparison to
make. Generate what our current pipeline produces for the **same** table from
`02-source.docx`, save it in the probe folder, and record its decoded character
count next to mTool's.

This does not require Windows, Word or mTool. If it is easier to run on the
Mac side, say so and it will be produced there instead.

---

## 5. Step 3 — clear the blocker, then re-run Experiment D (operator needed)

Before booking operator time:

1. Run the duplicate-key check over `03-pasted.xlsx` and over the blank
   template. Report how many rows carry `fn_49` in column A.
2. Resolve the column C-versus-F discrepancy and state which is correct.

If either check shows a problem, re-capture the payload from the row **mTool
actually reads** (the first column-A match) and record that you did.

Then re-run Experiment D exactly as the original brief specifies: byte-exact
write into a fresh copy of the blank template, operator opens it, screenshot
the note beside the round-1 manual-paste screenshot.

Read the result as the original brief section 7 sets out:

| Outcome | Meaning |
|---|---|
| Renders identically | The file-write channel is not the limitation. Our generator produces the wrong markup and should be rebuilt to match mTool's dialect. |
| Renders differently | Describe precisely what differs, element by element. This is the finding that would push us toward the clipboard route. |
| Empty, or will not open | Mechanical. Return to the checks above; do not report it as a ceiling finding. |

If the payload is over the cell limit, keep using the low-level write for this
measurement only, as round 1 did, and say so. Do not change the guard in
`mtool/offline_fill.py`.

---

## 6. Step 4 — Experiment C, the fidelity ladder (operator-heavy)

All ten rows in the original brief's section 6 are outstanding. This is the
largest remaining call on operator time.

**If time is short, prioritise in this order:**

1. **C2** — table with no borders at all.
2. **C3** — table with only a rule under the header row.
3. **C4** — shaded or filled header cells.
4. **C10** — long note. Run this after step 1, since it tests the same limit.
5. Everything else.

C2 and C3 rank first because both depend on what the source document does
**not** declare, and that is where our output has diverged from the source
before. Capture clear screenshots for those two.

Round 1's Experiment B finding — that mTool declares only top and bottom
borders and never left or right — is a prediction C1 and C2 will either confirm
or break. Note explicitly which happens.

---

## 7. Step 5 — write up

Use the six headings in section 8 of the original brief, in that order.

Two standing instructions from the original brief, repeated because round 1
drifted on both:

- **Report facts. Do not propose a design and do not change product code.** The
  design decision is made with the operator afterwards.
- **If a result contradicts the theory, say so and stop.** Do not adjust the
  test until it agrees. A mechanical symptom is not a conclusion; label it as
  mechanical and fix it before interpreting it.

Also carry forward from round 1, unchanged: work on copies, never save over an
operator original, do not upload any client document to an external service,
and ask before anything that changes a file.

---

## 8. Definition of done

- The size measurement is stated as escaped or decoded, with a decoded number.
- `05-our-payload.xhtml` exists, with its character count recorded.
- The duplicate-key and payload-column questions are both answered.
- Experiment D has a screenshot pair and one of the three outcomes above.
- Experiment C has at least C2, C3, C4 and C10 filled in, with screenshots.
- `NOTES.md` carries all six write-up sections.
