"""Unit tests for notes/payload.py — the NotesPayload dataclass."""
from __future__ import annotations

import pytest

from notes.payload import NotesPayload


def test_notes_payload_constructs_prose_payload():
    p = NotesPayload(
        chosen_row_label="Financial reporting status",
        content="The Group is a going concern.",
        evidence="Page 14, Note 2(a)",
        source_pages=[14],
    )
    assert p.chosen_row_label == "Financial reporting status"
    assert p.content == "The Group is a going concern."
    assert p.source_pages == [14]
    assert p.sub_agent_id is None  # default
    assert p.numeric_values is None  # default


def test_notes_payload_allows_numeric_values_for_structured_notes():
    # Notes 13/14 use structured numeric values per column.
    p = NotesPayload(
        chosen_row_label="Shares issued and fully paid",
        content="",  # numeric rows may have empty content
        evidence="Page 42, Note 14",
        source_pages=[42],
        numeric_values={"group_cy": 1000.0, "group_py": 900.0,
                        "company_cy": 1000.0, "company_py": 900.0},
    )
    assert p.numeric_values["group_cy"] == 1000.0
    assert p.numeric_values["group_py"] == 900.0


def test_notes_payload_tracks_sub_agent_id():
    p = NotesPayload(
        chosen_row_label="Disclosure of revenue",
        content="Revenue consists of...",
        evidence="Page 30-31",
        source_pages=[30, 31],
        sub_agent_id="notes12_sub_2",
    )
    assert p.sub_agent_id == "notes12_sub_2"


def test_notes_payload_rejects_empty_row_label():
    with pytest.raises(ValueError, match="chosen_row_label"):
        NotesPayload(
            chosen_row_label="",
            content="some content",
            evidence="Page 1",
            source_pages=[1],
        )


def test_notes_payload_requires_evidence_when_content_nonempty():
    # Mandatory evidence contract (Section 2 #11): every non-empty payload
    # must cite at least one source page.
    with pytest.raises(ValueError, match="evidence"):
        NotesPayload(
            chosen_row_label="Financial reporting status",
            content="The Group is a going concern.",
            evidence="",
            source_pages=[],
        )
