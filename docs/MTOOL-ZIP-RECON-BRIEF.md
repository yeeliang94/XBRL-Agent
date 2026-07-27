# M-Tool XBRL Zip Reconnaissance Brief

**Audience:** AI agent (or human) working in the Windows environment where SSM's
MBRS Preparation Tool (mTool 2.1) is installed.
**Requested by:** the xbrl-agent team (Mac side).
**Date:** 2026-07-02

## Why you're doing this

We are building a feature that generates the MBRS filing zip **directly** from
our extraction app's database, so users no longer copy-paste figures and notes
into mTool by hand. Our acceptance bar for v1 is: **mTool 2.1 must be able to
open our generated zip via its "Edit Filing" button and validate it cleanly.**

To build that generator we need to know exactly what a genuine mTool output zip
looks like inside — none of this is publicly documented. You have the only
environment with mTool installed, so you are our eyes.

Everything below is read/inspect/report work plus creating throwaway test
filings. **Do not upload anything to the real SSM mPortal.**

## Ground rules

- Use **dummy/test company data** wherever possible. If you must use a real
  filing, flag it clearly so we treat the artifacts as confidential.
- When a question can't be answered, say so explicitly — "not observable" is a
  valid finding. Do not guess silently.
- Preserve original files byte-for-byte: copy zips before unzipping; never
  re-save the original in place.
- We use the SSMxT_2022 v1.0 taxonomy on our side. Note the exact mTool
  version (Help/About screen) and taxonomy version it reports.

## Deliverables — TEXT ONLY (email body, no attachments)

Your channel back to us is **email text only — you cannot send files**. Every
deliverable below is therefore plain text you paste into the email body. Send
multiple emails if needed; number them ("Part 1 of N") and repeat the filing
variant name at the top of each part.

1. **The findings report**: answer every numbered question below, in order,
   citing the question number. Markdown format.
2. **Verbatim exemplar snippets.** You do the analysis in your environment;
   we only need your conclusions PLUS the small verbatim excerpts that Task 3
   explicitly marks "paste verbatim" (namespace block, context blocks, unit
   blocks, example facts, one text-block fact, the DEI block). These excerpts
   are non-negotiable and must be copied EXACTLY, character-for-character —
   we will use them as literal templates for our generator, so a paraphrased
   or "cleaned up" excerpt becomes an invisible bug on our side. Rules to
   survive email mangling:
   - Use fenced code blocks (``` … ```) and send the email as plain text,
     not HTML, so the mail client can't reformat the XML.
   - Do not re-indent, re-wrap, or abbreviate with `...` inside an excerpt.
   - **Keep the extracted instance XML file safely stored on your side**, and
     report its SHA-256 + byte size (`Get-FileHash -Algorithm SHA256`,
     `(Get-Item file.xml).Length`). If our excerpts turn out to miss
     something, we'll ask follow-up questions against that stored file — or,
     as a last resort, ask you to paste the full file in chunks. Don't paste
     the full file unprompted.
3. **Zip anatomy as command output** (Task 2) — paste the literal output of
   the PowerShell commands given there. We reconstruct the zip's structure
   from your listings; we do not need the binary itself.
4. **Screen transcriptions instead of screenshots** (Task 6) — for each
   screen, list every label, field, dropdown (with all its options), checkbox
   and button text verbatim, top-to-bottom. Tedious but this replaces images.
5. **Optional, only if asked later**: the smallest zip base64-encoded as text
   (`certutil -encode file.zip file.b64` then paste `file.b64` contents, plus
   the zip's SHA-256). Do NOT do this unprompted — the XML paste + listings
   should cover us.

## Task 1 — Produce sample filings (the variant matrix)

Create minimal-but-representative filings in mTool and save the XBRL zip for
each. Priority order (do as many as time allows — #1 is mandatory):

| # | Entry point | Filing level | Must include |
|---|-------------|--------------|--------------|
| 1 | FS — MFRS | Company | See "fixture content" below |
| 2 | FS — MFRS | Group (consolidated + company columns) | Same |
| 3 | FS — MPERS | Company | Same |
| 4 | FS — MPERS | Group | Same |

**Fixture content for each filing** (this exercises every serialization case
our generator must handle):

- Filing information / scoping page filled (entity name, registration number,
  financial period start/end, prior-year period, currency = MYR, level of
  rounding — pick **thousands** for at least one filing and **units** for
  another, so we can compare).
- A handful of SOFP + SOPL numeric facts, **including at least one negative
  value** (e.g. accumulated losses) and one value entered in a parenthesised
  style if mTool supports it.
- SOCIE with at least two equity components filled (it's a matrix — we need to
  see how row×column cells serialize).
- At least two **notes text blocks** filled, one of which must contain:
  - a **table** (2×3 or larger),
  - **bold text**, a bulleted **list**,
  - a special character (`&`, `<`, a non-ASCII char like `é` or a Malay
    diacritic).
  Paste one note from MS Word and type the other directly, and note which is
  which.
- Leave plenty of cells **empty** — we need to see how absent data serializes
  (omitted fact vs. nil vs. zero).

## Task 2 — Zip anatomy

For each zip, report (paste literal command output — PowerShell examples
given; any equivalent is fine):

2.1 Full file listing: every entry name, size, compression method, and any
    directory structure. PowerShell:

    ```powershell
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $z = [System.IO.Compression.ZipFile]::OpenRead("C:\path\to\filing.zip")
    $z.Entries | Select-Object FullName, Length, CompressedLength, LastWriteTime | Format-Table -AutoSize
    $z.Dispose()
    ```
2.2 Is there anything besides the instance XML? (manifest, .json, .txt,
    viewer HTML, images, a copy of the taxonomy?) Include the full content of
    any small non-XML files.
2.3 Zip-level metadata: zip comment, timestamps, whether entry names follow a
    naming convention (does the filename encode company number / period /
    entry point?).
2.4 The zip's own filename as proposed by mTool's save dialog.
2.5 Text encoding of the XML (BOM? UTF-8? declaration line verbatim).

## Task 3 — Instance document anatomy (the core task)

For the FS-MFRS Company instance (and note any differences in the other
variants), report:

3.1 Root element + the **complete namespace declaration block** verbatim.
3.2 The `<link:schemaRef>` href — exact value. Relative path? Absolute URL to
    SSM's site? Does the zip carry the schema alongside?
3.3 **Contexts**: paste 3–4 representative `<context>` blocks verbatim —
    one current-year, one prior-year, one instant (SOFP) and one duration
    (SOPL). Report: context id naming scheme, entity identifier `scheme`
    attribute and value format (SSM company number format?), and whether
    dimensions live in `<segment>` or `<scenario>`.
3.4 **Group vs Company representation** (from the Group filing): how are the
    consolidated vs company columns distinguished? Look for a consolidation
    axis / `ConsolidatedAndSeparateFinancialStatementsAxis`-style explicit
    dimension. Paste the two contexts for the same period that differ only in
    this respect.
3.5 **Units**: all `<unit>` elements verbatim (expect ISO 4217 MYR; also check
    for a `pure`/shares unit anywhere).
3.6 **Numeric facts**: paste 5 examples verbatim covering: positive value,
    negative value, a value on a "thousands" filing vs a "units" filing
    (CRITICAL: does the instance store the **full unscaled value** or the
    thousands figure? what is the `decimals` attribute in each case?), and a
    SOCIE matrix cell (which dimensions does its context carry?).
3.7 **Text block facts**: paste one complete notes text-block fact verbatim
    (the one with the table). Report: is the HTML escaped (`&lt;table&gt;`) or
    CDATA or nested XHTML? Which tags survived the Word paste (style
    attributes? class? font tags?) vs the typed-in note? Any `xml:lang`?
3.8 **Empty data**: search the instance for a concept you left blank — is it
    absent entirely, present with `xsi:nil="true"`, or present as empty
    string/zero?
3.9 Element naming: paste 5 fact element qnames with their prefixes so we can
    match them against taxonomy element names on our side (e.g.
    `ssmt-cor:Assets` style vs full-IFRS `ifrs-full:Assets`).
3.10 Fact ordering: do facts appear grouped by statement/sheet, or unordered?
3.11 Any generator fingerprint: comments, `<!-- generated by ... -->`,
    processing instructions, tool-version attributes, hashes, GUIDs anywhere
    in the XML.
3.12 The DEI/filing-information facts: paste the block of facts that carry
    entity name, registration number, period, currency, rounding level,
    nature of accounts (audited?), MFRS/MPERS choice. These are mandatory
    facts our generator must emit.

## Task 4 — Tolerance experiments (Edit Filing round-trip)

These tell us how strict mTool is about zips it didn't create. For each
experiment start from a **copy** of the FS-MFRS Company zip, apply one change,
then try **Edit Filing** in mTool and report: opens fine / opens with warning
(quote it) / refuses (quote the error).

4.1 Baseline: re-open the untouched zip (sanity check).
4.2 Unzip and re-zip with a different tool (e.g. Windows Explorer "Send to
    compressed folder", or 7-Zip), same contents. (Tests: does mTool care
    about zip tool/compression/order?)
4.3 Pretty-print the instance XML (whitespace-only change), re-zip.
4.4 Rename the zip file itself (keep contents identical).
4.5 Rename the XML file **inside** the zip.
4.6 Change one numeric fact value in the XML by hand (keep it arithmetically
    consistent if easy; if not, note whether the complaint is about the sum,
    not the file).
4.7 Delete one non-essential file from the zip, if the zip contains more than
    just the instance XML.
4.8 After a successful Edit Filing open of experiment 4.2 or 4.3: run mTool's
    validation and re-save. Diff the re-saved instance XML against the
    original (`Compare-Object (Get-Content a.xml) (Get-Content b.xml)` or
    `fc.exe /N a.xml b.xml`) and paste the diff output — what did mTool
    normalise? Also compare the two zips' file listings (Task 2.1 command).

Experiment 4.2/4.3 passing = our generator has wide latitude. 4.8's diff shows
us mTool's canonical output form, which is what we'll imitate.

## Task 5 — mTool validation behaviour

5.1 Trigger at least one **ERROR**-severity validation (e.g. unbalanced SOFP)
    and one **WARNING**. Transcribe the validation panel verbatim into text —
    every rule ID, severity, and message shown. (We have the formula
    linkbases with these rules; we want to confirm the IDs match what mTool
    displays.)
5.2 Does mTool let you **save the zip while validation errors exist**? (Can an
    invalid instance exist as a zip at all, or is save gated?)
5.3 Anything in mTool's UI about "import", "external file", or an API/batch
    mode? Check menus, the install directory (config files, .properties,
    anything mentioning file formats), and note the install path.

## Task 6 — Screen transcriptions (text, not screenshots)

For each screen below, transcribe into text: every label, input field,
dropdown (with the FULL list of options it offers), checkbox, radio button,
and button caption, in top-to-bottom order. Where a choice changes what
appears next, note the branching ("selecting X reveals Y").

6.1 The scoping/filing-information page(s) — every question SSM asks before
    the templates open (these choices select the entry point; we must
    replicate the decision tree). The dropdown option lists are the critical
    part here.
6.2 The template list / navigation tree for FS-MFRS — the full list of
    sheet/section names in order (so we can map their sheet list to ours).
6.3 The notes text-block editor — list its toolbar controls (which formatting
    features exist: bold? tables? lists? font size? colours?).
6.4 The validation results panel (covered by Task 5.1's transcription).
6.5 Help → About — exact version strings for mTool and the taxonomy.

## Priority if time is short

1. Task 1 filing #1 + Task 2 + Task 3 (the anatomy of one real zip is 80% of
   the value).
2. Task 4 experiments 4.1–4.3 (tolerance).
3. Task 3.4 via filing #2 (Group representation).
4. Everything else.

---

# Addendum A — Follow-up after the 2026-07-04 offline-patch spike

**Added 2026-07-27** (docs/PLAN-mtool-fill-pipeline.md Step 1). The spike
worked: a workbook we patched offline opened in mTool and passed Validate +
Generate. These questions are about the **values inside** that filing, and the
answers gate the unit/sign translation (plan Step 6) and the exposure of the
patch/download action (Step 8A).

Please answer against the filing that already worked, so the comparison is
against a known-good file. Short answers are fine; verbatim paste beats
paraphrase everywhere.

## A.1 — Does the generated XBRL carry the full value or the on-sheet figure?

The sheet showed `MYR'000` and a figure like `1,000`. In the **generated
instance XML**, find that fact and paste the element verbatim, e.g.

```xml
<ifrs-full:PropertyPlantAndEquipment contextRef="..." unitRef="..."
  decimals="...">1000</ifrs-full:PropertyPlantAndEquipment>
```

We need to know whether it says `1000` (mTool files the on-sheet figure and the
scale lives in the unit/decimals) or `1000000` (mTool multiplies out). This is
the single answer that decides whether our exporter ships the identity
translation or a thousands one.

Also paste the matching `<xbrli:unit>` element and the `decimals=` attribute.

**Why we're asking rather than assuming:** we now read the sheet's own
`#UNITSCALE#` marker row (it said `MYR'000` in the sample template), so we can
see what the template DECLARES — but not what the generator DOES with it.

## A.2 — Did the negative test value keep its sign?

The spike wrote at least one negative figure. In the generated instance, does
that fact appear as `-250` (sign preserved), `250` with a sign-flipping
element, or something else? Paste the element.

Also: did mTool's validation complain about the sign at any point?

## A.3 — Were derived cells correct without `--force-recalc`?

- Did the `*Total …` rows show the right numbers when you opened the patched
  workbook, with the add-in loaded?
- If you re-ran the fill **with** `--force-recalc`, did the add-in behave
  normally, or did it complain / recalculate something it shouldn't?

## A.4 — Two more `inspect` outputs

Run the tool's `inspect` command and paste the output for:

- an **MPERS** template (any level), and
- a **Group** template (either standard).

We need two things from each: the sheet-name list, and one sheet's column
layout. Specifically, for a Group template, please paste the rows around the
`#PRIM#` / `#STDTENDTDATE#` / `#ENDT#` / `#UNITSCALE#` markers — we want to see
**how mTool distinguishes the Group columns from the Company ones**. Today our
detector refuses to auto-map any Group layout precisely because we have never
seen one, so this answer is what lets a Group filing proceed without the
operator hand-entering four column letters.

## A.5 — Column A on a real template

In the sample we hold, column A of every data row carries the taxonomy concept,
e.g. `full_ifrs-cor_2022-03-24.xsd#ifrs-full_PropertyPlantAndEquipment`.

- Is that true on every template you have (MPERS, Group, notes sheets)?
- Is it present on a template freshly exported from mTool, before any data is
  entered?

If yes, it is a unique per-row key, and it would let us stop addressing rows by
their visible label — which currently cannot distinguish the repeated "Cost" /
"Accumulated depreciation" rows on the SOFP sub-sheets.

## Answer block

> _(Windows operator: paste answers here, keeping the A.n numbering. Until this
> block is filled in, plan Steps 6 and 7 stay blocked and `XBRL_MTOOL_FILL`
> stays off by default.)_

- **A.1:**
- **A.2:**
- **A.3:**
- **A.4:**
- **A.5:**
