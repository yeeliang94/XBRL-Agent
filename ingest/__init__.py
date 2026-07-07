"""Document ingest helpers — turning uploaded source documents into the
canonical inputs the pipeline expects.

Today this covers Microsoft Word (.docx) input (docs/PLAN-word-input.md):

- ``word_convert`` converts a .docx to a text PDF so the entire page-based
  pipeline (scout, page hints, evidence citations, PDF viewer) runs unchanged.
- ``docx_html`` extracts the Word body as HTML for the notes source-formatting
  side-channel.

Both are deliberately lightweight (LibreOffice / Word COM / mammoth) — nothing
like the removed docconvert stack (docling/torch); see
docs/PLAN-deprecate-docconvert.md.
"""
