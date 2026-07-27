---
paths:
  - "ingest/**"
  - "notes/source_snippets.py"
  - "tests/test_word_convert.py"
  - "tests/test_docx_html.py"
  - "tests/test_notes_source_snippets.py"
  - "tests/test_notes_source_prompt.py"
  - "tests/test_upload_docx.py"
  - "tests/test_run_cli_docx.py"
  - "web/src/components/UploadPanel*"
---
# Word (.docx) input (gotcha #29)

> Extracted verbatim from the root CLAUDE.md (2026-07-25 context-slimming pass).
> This file is the authoritative detail for its gotchas; the root CLAUDE.md keeps
> a summary stub pointing here. Keep the two in sync the same way you would any
> other cross-file invariant (docs/SYNC-MATRIX.md).

### 29. Word (.docx) input — convert at the door; PDF stays the spine


Uploads accept Microsoft Word (`.docx`) as well as PDF (docs/PLAN-word-input.md).
A `.docx` is converted to a **text PDF at upload time** and stored as the run's
`uploaded.pdf`, so the entire page-based pipeline (scout, page hints, evidence
citations "PDF page N", the PdfSourcePane viewer) runs UNCHANGED — it just sees
crisp real text instead of a scan. Excel input is deliberately out of scope
(a spreadsheet has no pages; it belongs as a future companion channel, not a
primary input).

- **Both files are kept in the session dir:** `uploaded.docx` (original,
  formatting source) + `uploaded.pdf` (canonical for extraction + viewer). The
  `uploaded.pdf` naming contract is preserved — nothing downstream learns a new
  path. PDF uploads are byte-for-byte unchanged (land straight as
  `uploaded.pdf`, no sidecar).
- **`ingest/word_convert.py` is the single converter seam** (`convert_docx_to_pdf`).
  Platform-native + lightweight, NOT the removed docling/torch stack (gotcha
  #26): **Word COM via `docx2pdf` on Windows** (Word is installed there),
  **LibreOffice `soffice --convert-to pdf` on Mac/Linux/cloud**. Override with
  `XBRL_DOCX_CONVERTER` (`soffice`|`docx2pdf`) / `XBRL_SOFFICE_PATH`.
  `_run_conversion` is the monkeypatch point in tests (no real converter in CI).
- **Conversion failure is a 422, never a crash.** The upload endpoint tears down
  the whole session dir and returns `WordConversionError.user_message` verbatim
  (plain-language, tells the operator to Save-As-PDF in Word and re-upload — the
  always-available fallback, since the pipeline can't tell a hand-saved PDF from
  a server-converted one). CLI (`run._stage_input_document`) lets it propagate.
- **Notes source-formatting side-channel (Phase 2).** `ingest/docx_html.py`
  extracts the Word body to `source.html` (via `mammoth`, small pure-Python) —
  **best-effort, never blocks the upload**. `notes/source_snippets.py` slices it
  per top-level note (navigation only, keyed on note-number headings like scout
  hints — gotcha #13; NO deterministic label-matching enters the notes
  pipeline). `create_notes_agent` registers the `read_source_note(note_num)`
  tool + a prompt block ONLY when `source.html` exists for the run (derived from
  the PDF's parent dir); PDF-only runs are byte-identical to before. The agent
  **COPIES the source table's markup — inline `style=` included — straight into
  `content`** (verbatim passthrough, 2026-07-19); it does NOT re-describe that
  styling as `format_ops`, which was the original design and is what run 74
  showed the model getting wrong. **PROSE still stays style-free**, enforced in
  code by `notes/writer.py::_strip_non_table_styles` — the narrowing is TABLES
  ONLY. Such tables are stamped `data-source-styled` so no renderer adds its own
  grid. `format_ops` remains the channel for styling the agent *observes* rather
  than copies (PDF runs, and any table with no source counterpart). See gotcha
  #16, which owns the full rule — this bullet must not drift from it again.
- **No DB schema change** — files live on disk (hybrid-storage, gotcha #6). The
  inert `doc_conversions` table (gotcha #11) is NOT reused.

Pinned by `tests/test_word_convert.py`, `test_docx_html.py`,
`test_notes_source_snippets.py`, `test_notes_source_prompt.py`,
`test_upload_docx.py`, `test_run_cli_docx.py`, and the `UploadPanel` web tests.
Phase 0 converter spike + real-run validation (Steps 6/10) and Windows
enablement (Step 11) are operator/hardware gates, still open. Plan:
docs/PLAN-word-input.md.
