# mtool/ — Agent Rules

These rules add to the root `AGENTS.md`. Detail: invariants 16 and 28 in
`CLAUDE-REFERENCE.md`. For authoring formatted notes for mTool injection, read
`docs/MTOOL-NOTES-AUTHORING.md`.

- `mtool/notes_exporter.py::prepare_note_output` owns review, Copy, and mTool
  output preparation. Clipboard code copies that HTML verbatim. TX-specific
  white borders and legacy widths stay in `mtool/notes_decorate.py`.
- Source Word table markup follows the `data-source-styled="true"` contract in
  `notes/AGENTS.md`; keep the decorator synchronized with it.
- `mtool/offline_fill.py` stays standard-library-only and remains the one
  patcher used by the CLI and server. Do not reserialize mTool files with
  openpyxl.
