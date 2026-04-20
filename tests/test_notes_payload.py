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


def test_notes_payload_rejects_unknown_numeric_keys():
    # Review I4: wrong numeric_values keys (typos) used to silently fall
    # through dict.get in the writer and land as empty cells. The payload
    # now validates keys up front.
    with pytest.raises(ValueError, match="unknown key"):
        NotesPayload(
            chosen_row_label="Shares issued and fully paid",
            content="",
            evidence="Page 42",
            source_pages=[42],
            numeric_values={"grup_cy": 1000.0},  # typo: grup vs group
        )


def test_notes_payload_rejects_string_numeric_value():
    # Peer review finding 3: {'company_cy': 'abc'} used to slip through
    # and be written as TEXT into a numeric cell, breaking the movement-
    # table formulas. Must be rejected at parse time.
    with pytest.raises(ValueError, match="expected int or float"):
        NotesPayload(
            chosen_row_label="Shares issued and fully paid",
            content="",
            evidence="Page 42",
            source_pages=[42],
            numeric_values={"company_cy": "abc"},
        )


def test_notes_payload_rejects_nan_and_inf_numeric_values():
    import math
    with pytest.raises(ValueError, match="non-finite"):
        NotesPayload(
            chosen_row_label="Shares issued and fully paid",
            content="",
            evidence="Page 42",
            source_pages=[42],
            numeric_values={"company_cy": math.nan},
        )
    with pytest.raises(ValueError, match="non-finite"):
        NotesPayload(
            chosen_row_label="Shares issued and fully paid",
            content="",
            evidence="Page 42",
            source_pages=[42],
            numeric_values={"company_cy": math.inf},
        )


def test_notes_payload_rejects_bool_numeric_value():
    # bool is a subclass of int -- must be rejected so True/False can't
    # land in numeric cells.
    with pytest.raises(ValueError, match="bool"):
        NotesPayload(
            chosen_row_label="Shares issued and fully paid",
            content="",
            evidence="Page 42",
            source_pages=[42],
            numeric_values={"company_cy": True},
        )


def test_notes_payload_coerces_int_to_float():
    # Integers are valid but get normalised to float so downstream
    # consumers see a single type.
    p = NotesPayload(
        chosen_row_label="Shares issued and fully paid",
        content="",
        evidence="Page 42",
        source_pages=[42],
        numeric_values={"company_cy": 42},
    )
    assert isinstance(p.numeric_values["company_cy"], float)
    assert p.numeric_values["company_cy"] == 42.0


def test_notes_payload_accepts_all_canonical_numeric_keys():
    NotesPayload(
        chosen_row_label="Shares issued and fully paid",
        content="",
        evidence="Page 42",
        source_pages=[42],
        numeric_values={
            "group_cy": 1.0, "group_py": 2.0,
            "company_cy": 3.0, "company_py": 4.0,
        },
    )
    # The generic company-filing aliases cy / py are also valid.
    NotesPayload(
        chosen_row_label="Shares issued and fully paid",
        content="",
        evidence="Page 42",
        source_pages=[42],
        numeric_values={"cy": 1.0, "py": 2.0},
    )
