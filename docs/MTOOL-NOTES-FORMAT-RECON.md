# mTool Notes-Formatting Recon — FALLBACK Diagnostic

**Audience:** the operator on the Windows box where SSM's mTool 2.1 is installed.
**Requested by:** the xbrl-agent team (Mac side).
**Status:** NOT normally needed. Formatting is already handled (see below). Use
this only if a SPECIFIC construct still renders wrong in mTool after decoration.

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
