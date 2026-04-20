"""Shared constants for the notes pipeline.

Kept in its own module so both ``notes.coordinator`` and
``notes.listofnotes_subcoordinator`` can import without either being
forced to read from the other. Before this module existed the constant
lived on ``notes.coordinator`` and the sub-coordinator imported it from
there, which forced a function-scoped import of
``run_listofnotes_subcoordinator`` inside the coordinator to break the
circular dependency (PR B.2).
"""
from __future__ import annotations


# Tool-name → phase mapping. Mirrors coordinator.PHASE_MAP so the frontend
# timeline can colour-code notes-agent phases identically to face agents.
NOTES_PHASE_MAP = {
    "read_template": "reading_template",
    "view_pdf_pages": "viewing_pdf",
    "write_notes": "writing_notes",
    "save_result": "complete",
}
