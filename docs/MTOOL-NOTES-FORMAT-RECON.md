# mTool Notes-Formatting Recon — FALLBACK Diagnostic

**Audience:** the operator on the Windows box where SSM's mTool 2.1 is installed.
**Requested by:** the xbrl-agent team (Mac side).
**2026-07-10 update:** for broken-file root-cause retesting, use
[`GUIDE-mtool-broken-file-windows-retest.md`](GUIDE-mtool-broken-file-windows-retest.md).
It supersedes the size-only procedure below with static package diagnostics,
identical-content full/compact A/B files, exact Excel boundary fixtures, real
Excel `.Value2.Length`, TX re-save controls, and Validate/Generate evidence.
**Status:** the original render-fidelity recon (sections below) is NOT normally
needed — formatting is already handled. The size recon (bottom section) was
**RUN 2026-07-09 — results + decisions in
docs/RECON-RESULTS-mtool-size-2026-07-09.md** (headline: mTool's native
serialisation is ~5× heavier than ours; mTool is an Excel add-in; the test
payload's 34,431 stored chars decode to ~27.5k for Excel — UNDER the limit,
so Excel was never tested past 32,767 and the guard stays). Still open from
it: the Step-4 doubling probe (find the actual ceiling) — run it on a future
session before any relaxation of the 32,767 guard.

## Status — read this first

**The formatting problem is fixed.** The notes fill path now **decorates** the
style-free DB HTML with inline styles before writing it into the `fn_*` payload
(`mtool/notes_decorate.py` — the backend port of the render-proven clipboard
decorator). That is the same styling the manual "Copy → paste into mTool"
workflow has applied for months, so mTool's TX27 editor renders borders, fills,
fonts, headings, lists and numeric alignment.

The earlier theory in this doc — *"mTool's TX27 dialect differs, so we must
translate our HTML"* and *"we drop our editor's HTML in as-is"* — was
**wrong/incomplete**. TX27 renders our tags fine; what it needed was the inline
`style=` declarations that the sanitiser strips from the DB copy. Injecting them
(decoration) was the whole fix — no dialect translation, no native-sample
dependency.

## When you WOULD still need this

Only as a fallback: if a particular decorated construct (say, a specific
double-underline totals row, or a merged cell) still renders wrong in mTool.
Then the useful move is to author that same construct **inside mTool**, dump how
mTool itself stores it natively, and compare against what our decorator emits —
to find the exact inline-style form TX Text Control wants for that one case.
This is a narrow per-construct comparison, not a wholesale translator.

## Ground rules

- Use a **dummy/test filing**. Do not upload anything to the real SSM mPortal.
- Preserve originals byte-for-byte: copy the zip before inspecting; never re-save
  in place.
- Report the exact mTool version + taxonomy (Help / About).
- Channel back is **text only** — paste the payloads into the email body inside
  fenced code blocks (```), as **plain-text** email, so nothing re-wraps the XML.

## What to produce, step by step

### 1. Author ONE formatted note inside mTool

In a throwaway test filing, open any prose-note text-block popup and type a
short note that exercises the formatting we care about. Please include **all** of
these in a single note so one payload shows every case:

1. A line with **bold** text and a line with *italic* text.
2. A line with underlined text.
3. A short heading-style line (whatever the editor offers — bold/larger).
4. A 2-column × 3-row **table** with:
   - the header row **filled** with a background colour,
   - visible **cell borders**,
   - one cell's text coloured (e.g. red),
   - one numeric cell right-aligned.
5. A two-item bullet list.

Save/close the filing normally through mTool so **mTool itself writes the
payload** (this is the whole point — we need mTool's serialisation, not ours).

Note down which visible note you typed into (sheet + row/label) so we can find
its `fn_*`.

### 2. Find the `fn_*` key for that note

From the repo root (Mac side we'll do this, but if you can, run it there):

```
python mtool/examples/inspect_fn_slot.py --workbook <that-filing>.xlsx --keys fn_1
```

Easier: just run the dumper in step 3 with no `--keys` — it prints every
populated payload, and yours will be the one with the table in it.

### 3. Dump the native payload verbatim

```
python mtool/examples/dump_fn_payload.py --workbook <that-filing>.xlsx --repr
```

- `--repr` prints the exact string (whitespace + any `_x000D_` carriage-return
  tokens intact), which is what survives email/paste.
- Also run it **without** `--repr` once, so we get a human-readable copy too.

Paste **both** outputs back (the whole thing for the note you authored — do not
trim or `...`-elide the middle; the middle is exactly where the table markup
lives).

If the box has no Python, fall back to unzipping and reading the raw part:
`xl/sharedStrings.xml` (find the `<si>` containing your note text) and
`xl/worksheets/…` for the `+FootnoteTexts` sheet — but the dumper is far easier.

### 4. Answer these specific questions

For the payload you dumped, tell us:

1. **Bold/italic/underline** — what tag or style does mTool emit? (`<b>` vs
   `<span style="font-weight:bold">` vs something else; same for italic/underline.)
2. **Headings** — is there a distinct tag, or just a styled `<p>`/`<span>`?
3. **Tables** — the exact `<table>`/`<tr>`/`<td>` shape. Are borders/fills on the
   cell as inline `style=`, as attributes (`bgcolor`, `border`), or on a
   `<colgroup>`? Paste one full `<td>` verbatim.
4. **Colour** — how is text colour and cell fill expressed (named? `#rrggbb`?
   `rgb()`?).
5. **Alignment** — how is right-align on a numeric cell expressed?
6. **Lists** — `<ul>/<li>`, or paragraphs with a bullet character, or something
   else?
7. **Line breaks / paragraphs** — `<p>`, `<br />`, the `_x000D_` token, or a mix?
8. Anything surprising (namespaces, `class=` referencing a stylesheet, `<font>`
   tags, `mso-` styles, etc.).

## What we do with it

For the ONE failing construct, compare mTool's native inline-style form against
what `mtool/notes_decorate.py` emits and adjust that decorator's style for that
case (then pin it with a fixture). This is a targeted tweak to the existing
decorator — NOT a new translator, and NOT a change to the default path, which
already works for the common cases.

## Our side, for reference — the HTML we emit (after decoration)

So you can compare against what mTool produces. Our sanitised DB HTML uses the
tags below; the decorator then injects the inline `style=` that makes TX27
render them (Arial 10pt, `border: 1px solid #999`, cell padding, header fill,
numeric `text-align: right`, etc.):

- Marks: `<strong>`, `<em>`, `<u>`, `<s>`, `<mark>`, `<sup>`, `<sub>`
- Blocks: `<p>`, `<h3>`, `<ul>/<ol>/<li>`
- Tables: `<table><thead><tbody><tr><th><td>` with inline
  `style="background-color: …; border-…: …; text-align: …"`, plus
  `<colgroup><col style="width: …">` for column widths
- Colour: `<span style="color: #rrggbb">`, cell fills via
  `background-color: #rrggbb`

---

## Size recon (2026-07-09) — REQUESTED

**Why:** our automated fill can bust Excel's 32,767-character-per-cell limit on
big tables — not because the note's words are long, but because our styling is
written out per table cell (measured: a 50-row × 6-column table is ~1.7k of
text but ~33k once decorated). Meanwhile, users can heavily style notes inside
mTool's own editor and it saves without complaint. We need to see **what mTool
itself writes** for a big styled note, so we can either mimic its compact form
or learn that the limit doesn't bite in the mTool workflow at all.

Same ground rules as above (dummy/test filing, copy the zip before inspecting,
plain-text email back).

### Step 1 — paste the ready-made big note into mTool

1. On the Windows box, open `mtool/examples/recon_size_test_note.html` in a
   browser (Edge/Chrome — double-click the file).
2. Select all (**Ctrl+A**), copy (**Ctrl+C**).
3. In a throwaway test filing, open any prose-note text-block popup and paste
   (**Ctrl+V**). Confirm the table pasted WITH its borders/fills/alignment —
   if it pasted as plain text, tell us and stop here.
4. Save/close the filing normally so **mTool writes the payload**.
5. Note which visible note you pasted into (sheet + row/label).

### Step 2 — dump what mTool stored

```
python mtool/examples/dump_fn_payload.py --workbook <that-filing>.xlsx
```

The dumper prints every populated payload **with its character count**. Find
the one containing "SIZE-RECON TEST NOTE".

### Step 3 — answer these questions

1. **Character count** of that payload (the dumper prints it). This is the
   headline number — over or under 32,767?
2. Did mTool **save and reopen** the note intact (styling still visible when
   you reopen the popup)? Any warning or truncation on save?
3. Paste back **one full `<td>` and the opening `<table …>` tag** verbatim from
   the dump — we want to see how many characters of styling mTool spends per
   cell vs per table.
4. Does mTool put styling on **every cell**, **once per table/row**, or use
   something else entirely (`class=` references, a stylesheet block, RTF-ish
   markup)?
5. **Does your normal filing workflow ever open the filled mTool workbook in
   Excel itself** (not mTool) — e.g. to check figures — and re-save it? This
   decides whether Excel's 32,767 limit is a real constraint or a theoretical
   one for this workflow.
6. If the payload came out UNDER 32,767: try making the table even bigger
   (copy-paste the table's rows a second time inside the mTool editor, roughly
   doubling it), save, dump again. Does mTool still save fine past ~33k? Any
   complaint from mTool's own Validate?

### What we do with it

- If mTool stores styling compactly (per-table, classes, or attributes) → we
  slim our decorator to match (see docs/PLAN-mtool-compact-decoration.md).
- If mTool happily exceeds 32,767 and Excel is never in the loop → we can relax
  the fill guard for the mTool path (currently it skips oversize notes).
- Either way we keep the degradation ladder as the safety net.
