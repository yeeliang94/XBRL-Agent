# Experiment brief: what is the real ceiling of the mTool prose channel?

**For:** an AI coding agent working with the operator on the Windows machine.
**Time:** about 90 minutes, mostly waiting on the operator.
**Prerequisites:** Windows, Microsoft Word, Excel with the SSM mTool add-in
installed, PowerShell, a real Word financial statement, and a copy of this
repository.

---

## 1. Why this experiment exists

We fill prose notes into the SSM mTool template by writing bytes directly into
the saved workbook file. Prose lives in hidden rows keyed by `fn_*` strings in
column A, stored as an XHTML payload. Our patcher is `mtool/offline_fill.py`
(stdlib only, zip and text surgery, no Excel).

When a **human** copies a table out of Word and pastes it into the mTool editor,
the formatting survives well. When **we** write markup into the file, much of it
does not — undeclared cell edges pick up a default grey grid, CSS widths are
ignored, and we have been reverse-engineering the rules by trial and error.

The working theory is that these are two different channels:

- The human paste goes through the Windows clipboard, where Word offers several
  formats at once and the editor picks the richest one it understands. The mTool
  editor is a TX Text Control, whose native rich-text format is RTF. So the
  paste probably never involves HTML at all.
- Our path writes the editor's **save** format, which is a flattened version of
  its internal model, and therefore expresses less.

**This experiment tests that theory and, more importantly, captures a
known-good sample of the save format that we can build against.**

Everything below is measurement. Do not change any product code during this
experiment.

---

## 2. Ground rules

- Work on **copies**. Never save over the operator's original Word document or
  a real mTool template. Put working copies in a scratch folder.
- Do not enter credentials anywhere, and do not upload any client document to an
  external service.
- The operator drives Word, Excel and mTool. You drive PowerShell, file
  inspection and note-taking. Ask before anything that changes a file.
- Record every result, including the ones that fail. A format that does not
  survive is as informative as one that does.
- If a step's result contradicts the theory in section 1, say so plainly and
  stop rather than adjusting the test until it agrees.

---

## 3. What to collect

Create a folder `mtool-ceiling-probe/` and keep everything in it:

```
mtool-ceiling-probe/
  01-clipboard-formats.txt
  02-source.docx              (copy of the Word file, or a cut-down extract)
  03-pasted.xlsx              (mTool workbook after the human paste + save)
  04-stored-payload.xhtml     (what mTool wrote for that note)
  05-our-payload.xhtml        (what our generator produces for the same table)
  06-writeback.xlsx           (mTool workbook after we write 04 back in)
  screenshots/                (Word, mTool after paste, mTool after write-back)
  NOTES.md                    (running log — see section 8)
```

---

## 4. Experiment A — what does Word actually put on the clipboard?

**Question:** does Word offer RTF, and does it offer HTML?

1. Operator: open the Word statement, select one complete note table
   (a real one with borders, a shaded or bold header row, and right-aligned
   numbers), press Ctrl+C.
2. You: run this in PowerShell. It must run single-threaded-apartment, which is
   why the job wrapper is there.

```powershell
$job = Start-Job { Add-Type -AssemblyName System.Windows.Forms; [Windows.Forms.Clipboard]::GetDataObject().GetFormats() }
Receive-Job $job -Wait | Tee-Object mtool-ceiling-probe\01-clipboard-formats.txt
```

3. Record the full list. Note specifically whether these appear: `Rich Text
   Format`, `HTML Format`, `Embed Source`, `Object Descriptor`, plain text,
   bitmap or metafile entries.

**Interpretation:** if `Rich Text Format` is present, the RTF theory is live. If
`HTML Format` is *also* present, we still cannot conclude which one the editor
consumed — experiment B settles that.

---

## 5. Experiment B — capture mTool's own output (the important one)

**Question:** when mTool itself stores a well-formatted table, what markup does
it write?

1. Operator: open a **copy** of the mTool template in Excel, go to a prose note
   slot, open the mTool text editor for it, and paste the table from
   experiment A. Confirm on screen that it looks correct. Screenshot it.
2. Operator: save and close the workbook. Save it as `03-pasted.xlsx` in the
   probe folder.
3. You: extract the stored payload. An `.xlsx` is a zip file. Either unzip it
   and read the sheet XML and `sharedStrings.xml` directly, or reuse the repo's
   existing reader — `mtool/offline_fill.py` already has a function that reads
   the footnote rows, and it is the shape we care about. Do not modify it; call
   it or copy the read logic into a scratch script.
4. Save the exact payload string for that note as `04-stored-payload.xhtml`.
   Pretty-print a second copy for reading, but keep the raw one byte-exact.

**Record in NOTES.md:**

- Which tags appear, and which do not.
- How borders are expressed — inline `style`, legacy attributes, or something
  else. Note whether every cell edge is declared explicitly or only some.
- How column widths are expressed, if at all.
- How alignment, bold, shading and merged cells are expressed.
- Whether any Word-specific markup survived (anything starting `mso-`, `<o:`,
  `<w:`, or conditional comments). Its presence or absence tells us whether the
  editor re-authored the markup or passed something through.
- The exact character count of the payload.

**This file is the deliverable that matters most.** It is a correct example,
written by the tool itself, of markup we know renders properly.

---

## 6. Experiment C — the fidelity ladder

**Question:** which formatting features survive the human paste at all?

Operator pastes each of the following into separate note slots in the same
workbook copy. After each, screenshot the editor and note whether it looks like
Word. Then save once at the end and extract all the payloads.

| # | Feature to test | What to look for |
|---|---|---|
| C1 | Table with all borders drawn | Do the lines match Word? |
| C2 | Table with **no** borders at all | Does a grey grid appear where Word had none? |
| C3 | Table with only a rule under the header row | Does the single rule survive without extra lines? |
| C4 | Shaded / filled header cells | Does the fill survive, and its colour? |
| C5 | Merged cells (a header spanning two columns) | Does the merge survive? |
| C6 | Right-aligned number column | Does alignment survive? |
| C7 | Bold, italic, underline in prose | Do they survive? |
| C8 | Indented paragraph or a bulleted list | Does indentation survive? |
| C9 | Superscript (note references) | Does it survive? |
| C10 | A long note, roughly two pages | Does anything truncate? Record the size. |

C2 and C3 are the ones that have bitten us. Give them extra attention and get
clear screenshots.

---

## 7. Experiment D — the decisive test: can our channel reach the same result?

**Question:** if we write mTool's own markup back in with our patcher, does it
render identically?

This is the test that decides whether the file-write approach has a real ceiling
or whether we have simply been generating the wrong markup.

1. Start from a **fresh copy** of the blank mTool template (not `03-pasted.xlsx`).
2. Use `mtool/offline_fill.py` as it exists today to write the byte-exact
   contents of `04-stored-payload.xhtml` into the same note slot. Do not
   transform it, do not run it through any of our decorators, do not
   pretty-print it. Save as `06-writeback.xlsx`.
3. Operator: open `06-writeback.xlsx` in Excel, open that note in the mTool
   editor, screenshot it.
4. Compare the screenshot against the paste screenshot from experiment B.

**Read the result as follows:**

- **Identical** — the file-write channel is not the limitation. Our generator is
  producing the wrong markup, and the fix is to generate markup shaped like
  `04-stored-payload.xhtml`. This is the good outcome and it makes the rebuild
  straightforward.
- **Different** — the paste carries something the saved file does not, and the
  file-write channel genuinely cannot reach paste quality. Describe precisely
  what differs. This pushes us toward the clipboard fallback.
- **Fails to open, or the note is empty** — a mechanical problem with the write,
  not a fidelity finding. Check the column-A key. Note that mTool joins the
  visible cell to its payload by that column-A string and reads the *first*
  match, so a duplicate key silently produces an empty popup. Fix and retry
  before drawing any conclusion.

Also record the character count of `05-our-payload.xhtml` (what our current
generator produces for the same table) against `04-stored-payload.xhtml`. Excel
caps a cell at 32,767 characters. If mTool's own markup is materially smaller
than ours, that is a second finding worth reporting.

---

## 8. What to write up

Put this in `NOTES.md` and report it back in this order:

1. **Answer to the headline question:** did the write-back render identically to
   the paste? Yes, no, or blocked — with the two screenshots side by side.
2. **The dialect specification** — a plain-language description of how mTool's
   own markup expresses borders, fills, widths, alignment and merges, quoting
   short snippets from `04-stored-payload.xhtml`.
3. **The fidelity ladder table** from experiment C, filled in.
4. **Size comparison** — mTool's payload versus ours, in characters.
5. **Clipboard format list** from experiment A.
6. **Anything that contradicted the theory in section 1.**

Do not propose a design or start changing code. The output of this experiment is
a set of facts. The design decision comes afterwards, with the operator.

---

## 9. If something blocks you

- **The mTool add-in will not load or the editor will not open** — stop and
  report. This is an environment problem, not a finding.
- **The workbook will not open after the write-back** — that is a patcher bug,
  not a ceiling finding. Report it separately and clearly labelled.
- **You cannot find the note slot's `fn_` key** — read how the repo's existing
  reader locates footnote rows and follow the same approach. Do not invent a
  new key.
- **The operator's document is confidential and cannot be copied into the probe
  folder** — cut one representative note down into a small new Word file and use
  that instead. Say in the write-up that you did.
