# notes/ — Agent Rules

These rules add to the root `AGENTS.md`. Detail: invariants 14, 16, 27, and 31
in `CLAUDE-REFERENCE.md`.

- `notes_cells` is the canonical notes store. Downloads overlay live rows and
  deletion tombstones onto a temporary workbook.
- A later Sheet-12 call for the same structured note number and resolved row
  replaces earlier calls; chunks within one call remain combinable. Structured
  identity, not prose, determines replacement.
- Notes matching remains model judgment. Do not add deterministic label
  matching, synonym dictionaries, or OCR to the notes pipeline.
- Extraction and reviewer agents author content, not formatting. The formatter
  may apply validated style-only patches and may not change rendered text,
  numbers, table geometry, structure, or placement.
- Source Word table markup passes through only under the
  `data-source-styled="true"` contract. Prose stays style-free. Keep the
  sanitizer, TipTap schema, review CSS, mTool decorator, and clipboard behavior
  synchronized.
- Source-integrity assessment that is incomplete is unresolved, never clean.
  Read `tests/test_notes_integrity_false_greens.py` before changing this area.
