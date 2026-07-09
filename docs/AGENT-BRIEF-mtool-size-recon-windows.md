# AGENT BRIEF — mTool payload-size recon (Windows box)

**You are an AI coding agent running on the enterprise Windows machine where
SSM's mTool 2.1 is installed.** Your job is to run the payload-size recon
described in docs/MTOOL-NOTES-FORMAT-RECON.md ("Size recon" section) together
with the human operator, and produce one results file the operator can email
back to the Mac-side team.

Division of labour: **you run every shell/script step and every measurement;
the operator does the two GUI moments** (pasting into mTool's note editor and
saving the filing) — mTool is a desktop app you cannot drive. At each GUI
moment, give the operator ONE short, explicit instruction, then wait for their
confirmation before continuing.

## Why we're doing this (context you should keep in mind)

Our automated fill writes each prose note into one hidden spreadsheet cell.
Excel caps a cell at 32,767 characters. The note's words are never the
problem — our injected styling is (a 50-row table: ~1.7k of text, ~33k once
decorated). Yet users style notes heavily inside mTool's own editor and it
saves fine. This recon answers: **how does mTool itself store a big styled
note, and does the 32,767 limit actually bite in the mTool workflow?** The
Mac side will use your results to pick between mimicking mTool's storage
format, relaxing the limit, or shipping a compact styling tier
(docs/PLAN-mtool-compact-decoration.md).

## Hard rules — do not deviate

- **Dummy/test filing only.** Never touch a real filing or anything destined
  for the SSM mPortal. If the operator only has a real filing available, STOP
  and have them create a throwaway one first.
- **Never modify an original workbook.** Before inspecting any filing, copy it
  (`copy <file>.xlsx <file>.recon-copy.xlsx`) and only ever read the copy.
  Never open + re-save a filing in Excel or any script.
- **Read-only scripts.** The two scripts you will run
  (`mtool/examples/dump_fn_payload.py`, `mtool/examples/inspect_fn_slot.py`)
  only read. Do not run any script that writes into the workbook.
- **Set `set PYTHONUTF8=1`** before any Python invocation (Windows charmap
  crashes on Unicode otherwise — repo gotcha #1).
- Results must survive plain-text email: fenced code blocks, no trimming, no
  `...`-eliding the middle of payloads (the middle is where the table lives).

## Step 0 — preflight (you)

1. Confirm the repo is present and current enough: the files
   `mtool/examples/recon_size_test_note.html`,
   `mtool/examples/dump_fn_payload.py`, and
   docs/MTOOL-NOTES-FORMAT-RECON.md ("Size recon" section) must all exist.
   If `recon_size_test_note.html` is missing, the repo predates 2026-07-09 —
   ask the operator to pull, or STOP and report.
2. Find a working Python: try `py -3 --version` then `python --version`.
   Any Python ≥3.9 works for the dump scripts (they are stdlib-only).
3. Ask the operator: mTool version + taxonomy (Help/About). Record it.
4. Ask the operator to open (or create) a **throwaway test filing** in mTool
   and tell you its .xlsx path. Record the path. Make your read-only copy now
   if the file already exists.

## Step 1 — paste the big test note (operator, guided by you)

Tell the operator, verbatim:

> 1. Double-click `mtool\examples\recon_size_test_note.html` — it opens in the
>    browser as a styled note with a big 55-row table.
> 2. Press **Ctrl+A**, then **Ctrl+C**.
> 3. In the test filing in mTool, open any prose-note text-block popup and
>    press **Ctrl+V**.
> 4. Look at what pasted: does the table have visible borders, a grey header
>    row, and right-aligned numbers? Tell me yes or no.
> 5. If yes: save and close the filing normally in mTool. Tell me which note
>    you pasted into (sheet + row/label).

- If the paste came out as **plain text** (no table/borders): record that
  outcome — it is itself a finding (mTool's editor doesn't accept rich
  clipboard HTML from the browser). Then ask the operator to instead build a
  smaller styled table (say 5×3, with borders + a filled header + one
  right-aligned number column) **by hand inside mTool's editor**, save, and
  continue — the per-cell serialisation questions still get answered, only the
  headline size number becomes an estimate you extrapolate (chars-per-cell ×
  cell count).

## Step 2 — dump what mTool stored (you)

After the operator confirms the save:

```bat
set PYTHONUTF8=1
copy "<filing>.xlsx" "<filing>.recon-copy.xlsx"
py -3 mtool\examples\dump_fn_payload.py --workbook "<filing>.recon-copy.xlsx"
```

- Identify the payload containing `SIZE-RECON TEST NOTE` (or the operator's
  hand-built table). The dumper prints each payload's **character count** —
  that count is the headline result.
- Run it a second time with `--repr` for the exact-whitespace form.
- Save both full outputs. Do NOT trim them.

## Step 3 — the reopen check (operator, guided by you)

Ask the operator to reopen the same note popup in mTool and confirm the
styling is still intact (borders, header fill, alignment). Record yes/no and
any warning mTool showed on save or reopen.

## Step 4 — the over-32k probe (conditional)

Only if the Step 2 payload came out **under** 32,767 characters:

1. Ask the operator to reopen the note popup, select the table's rows,
   copy, and paste them again below (roughly doubling the table), then save.
2. Re-run the Step 2 dump on a fresh copy. Record the new count.
3. Ask the operator to reopen the note once more — intact or truncated? Did
   mTool's own **Validate** complain?

This tells us whether mTool tolerates payloads past Excel's limit.

## Step 5 — the workflow question (operator)

Ask the operator directly and record the answer verbatim:

> In the normal filing workflow, does anyone ever open the filled mTool
> workbook in **Excel itself** (not mTool) — for checking figures, printing,
> anything — and save it from Excel? Or does the file only ever pass through
> mTool between our fill and the SSM submission?

This single answer decides whether the 32,767 limit is a real constraint
(Excel truncates/corrupts oversized cells on save) or a theoretical one.

## Step 6 — assemble the results (you)

Write `RECON-RESULTS-mtool-size.md` next to the test filing, containing:

1. mTool version + taxonomy; date; filing path used.
2. **Headline:** payload character count(s) — Step 2 (and Step 4 if run) —
   each explicitly compared against 32,767.
3. Paste outcome (rich vs plain) and reopen-intact answers, incl. any
   warnings.
4. The **full verbatim dump** (both plain and `--repr`) in fenced code blocks.
5. Your own analysis of the serialisation: pull out one complete `<td>` and
   the opening `<table …>` tag; state how many characters mTool spends per
   cell vs per table, and whether styling repeats per cell, sits per
   table/row, or uses classes/stylesheet/other. Compare briefly against our
   decorator's ~81 chars per `<td>` (post-hoist).
6. The Step 5 workflow answer, verbatim.
7. Anything surprising (namespaces, `class=`, `<font>`, `mso-`, RTF-ish
   tokens, `_x000D_`).

Then tell the operator to email that file's contents to the Mac-side team as
**plain-text** (fenced blocks intact), or commit it on a branch if this box
pushes to the repo.

## If anything blocks you

Don't improvise around a blocker (missing files, no Python, mTool refusing
the paste in both rich and plain form). Record exactly what happened and how
far you got in `RECON-RESULTS-mtool-size.md`, and have the operator send that
instead — a partial result with an honest stopping point is still useful.
