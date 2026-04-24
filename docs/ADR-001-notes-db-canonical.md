# ADR-001: Notes DB is canonical; xlsx is a render target

**Status:** Accepted (2026-04-24)
**Supersedes:** N/A
**Context:** Implemented under `docs/PLAN-NOTES-RICH-EDITOR.md`.
Also documented inline at `CLAUDE.md` gotcha #16 and
`docs/NOTES-PIPELINE.md`.

## Context

Notes cells (sheets 10–14 on MFRS, 11–15 on MPERS) carry agent-
authored rich text that users review and edit post-run. Two
competing designs for where the edited content lives were on the
table when the rich-editor feature landed:

1. **xlsx as canonical.** Agent writes to the filled workbook on
   disk; editor reads and writes the workbook directly;
   downloads stream the workbook verbatim.
2. **DB as canonical.** Agent writes HTML into an audit-DB table
   (`notes_cells`); editor reads and writes the DB row; downloads
   overlay the DB values onto a temp-copy of the workbook at
   stream time.

## Decision

We chose **DB as canonical** (option 2). The filled xlsx on disk
is a flattened snapshot from the original agent run; edits never
land in it. The download endpoint overlays current DB rows onto
a temporary copy of the xlsx and streams the result.

## Consequences

### Positive

- **Edits survive workbook regeneration.** The agent re-run
  (coordinator clobber) is still the only way to blow edits away,
  and the confirm dialog gates it explicitly.
- **Rich form is preserved.** HTML round-trips through the DB
  and the TipTap editor without lossy conversion. The Excel
  download is the only place the HTML→plaintext flatten happens
  (`notes/html_to_text.py`), and it happens on every download
  (no drift between DB and xlsx).
- **Copy-to-M-Tool is trivial.** The clipboard writes the DB's
  HTML straight as `text/html`; paste into M-Tool preserves
  tables/bold/lists. No xlsx indirection.
- **Cross-run isolation.** Editor state keys on `run_id`, so
  switching runs never cross-writes. The xlsx is just cached
  output; losing it is harmless.

### Negative

- **Download does load/save on every request.** Streaming the
  xlsx means openpyxl parses, overlays, and saves a temp copy per
  hit. Measured at ~100ms for a typical merged workbook; fine
  for desktop scale.
- **Formulas in face sheets round-trip through openpyxl.** Known
  to mangle some features (named ranges, complex data
  validations). Mitigated by
  `tests/test_overlay_on_merged_workbook.py`, which asserts face
  sheets stay byte-identical.
- **Two flatteners (JS clipboard + Python xlsx).** Both serve
  different targets (rich-paste vs Excel cells) so their
  behaviours legitimately differ. Cross-documented inline (peer-
  review #11) to prevent silent drift.
- **notes_cells grows unbounded across runs.** ~150 rows per run
  × retention → linear SQLite growth. No retention policy today
  (peer-review #10); revisit if the History query slows or the
  DB file grows past a few hundred MB.

### Trade-offs explicitly rejected

- **Bleach over BeautifulSoup for sanitisation** (peer-review #1):
  single-user desktop deployment + TipTap schema defence-in-depth
  means bespoke sanitisation is acceptable; swap to `bleach` if
  this ever ships as a multi-tenant service. Docstring in
  `notes/html_sanitize.py` captures the trust-model constraint.
- **Regenerate-notes-from-History auto-fires a rerun** (peer-
  review #2): deliberately left as manual — the user lands on the
  Extract page and re-uploads the PDF to start a fresh run. The
  automatic path would require end-to-end session-state
  restoration which is out of scope.

## Pointers

- Schema: `db/schema.py` (v3 adds `notes_cells`)
- Overlay implementation: `notes/persistence.py`
- Flattener: `notes/html_to_text.py` (backend) +
  `web/src/lib/clipboard.ts` (frontend)
- Sanitiser: `notes/html_sanitize.py`
- Editor: `web/src/components/NotesReviewTab.tsx`
