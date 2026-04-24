"""Unit tests for notes/payload.py — the NotesPayload dataclass."""
from __future__ import annotations

import pytest

from notes.payload import NotesPayload


_HEADING_2A = {"number": "2(a)", "title": "Financial Reporting Status"}
_HEADING_14 = {"number": "14", "title": "Issued Capital"}
_HEADING_5 = {"number": "5", "title": "Revenue"}


def test_notes_payload_constructs_prose_payload():
    p = NotesPayload(
        chosen_row_label="Financial reporting status",
        content="The Group is a going concern.",
        evidence="Page 14, Note 2(a)",
        source_pages=[14],
        parent_note=_HEADING_2A,
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
        parent_note=_HEADING_14,
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
        parent_note=_HEADING_5,
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
            parent_note=_HEADING_14,
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
            parent_note=_HEADING_14,
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
            parent_note=_HEADING_14,
        )
    with pytest.raises(ValueError, match="non-finite"):
        NotesPayload(
            chosen_row_label="Shares issued and fully paid",
            content="",
            evidence="Page 42",
            source_pages=[42],
            numeric_values={"company_cy": math.inf},
            parent_note=_HEADING_14,
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
            parent_note=_HEADING_14,
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
        parent_note=_HEADING_14,
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
        parent_note=_HEADING_14,
    )
    # The generic company-filing aliases cy / py are also valid.
    NotesPayload(
        chosen_row_label="Shares issued and fully paid",
        content="",
        evidence="Page 42",
        source_pages=[42],
        numeric_values={"cy": 1.0, "py": 2.0},
        parent_note=_HEADING_14,
    )


def test_notes_payload_has_source_note_refs():
    """Step 4.1: payloads carry an optional `source_note_refs` list so the
    Phase 5 post-validator can dedupe notes across Sheet 11 and Sheet 12."""
    p = NotesPayload(
        chosen_row_label="Disclosure of trade receivables",
        content="Trade receivables consist of...",
        evidence="Pages 30-32",
        source_pages=[30, 31, 32],
        source_note_refs=["5", "5.1", "5.2"],
        parent_note={"number": "5", "title": "Trade receivables"},
    )
    assert p.source_note_refs == ["5", "5.1", "5.2"]


def test_notes_payload_source_note_refs_defaults_to_empty_list():
    """Omitting source_note_refs must not break legacy payloads."""
    p = NotesPayload(
        chosen_row_label="Disclosure of revenue",
        content="Revenue is recognised...",
        evidence="Page 20",
        source_pages=[20],
        parent_note=_HEADING_5,
    )
    assert p.source_note_refs == []


# ---------------------------------------------------------------------------
# Structured headings (Phase 2 of the model+notes-heading plan).
#
# Every note cell must open with one or two <h3> heading lines identifying the
# note number + title. The agent supplies parent_note and (optional) sub_note
# as structured fields; the writer prepends the <h3> lines deterministically
# so the LLM cannot drift. These tests pin the payload-side contract.
# ---------------------------------------------------------------------------


def test_notes_payload_accepts_parent_note_heading():
    """A top-level disclosure note carries only parent_note (no sub_note)."""
    p = NotesPayload(
        chosen_row_label="Disclosure of revenue",
        content="Revenue is recognised when control transfers.",
        evidence="Page 20, Note 5",
        source_pages=[20],
        parent_note={"number": "5", "title": "Revenue"},
    )
    assert p.parent_note == {"number": "5", "title": "Revenue"}
    assert p.sub_note is None


def test_notes_payload_accepts_parent_and_sub_note_heading():
    """A sub-note carries both parent_note and sub_note so the writer can
    emit `<h3>{parent}</h3><h3>{sub}</h3>` before the body."""
    p = NotesPayload(
        chosen_row_label="Property, plant and equipment",
        content="Property, plant and equipment are stated at cost.",
        evidence="Page 27, Note 5.4",
        source_pages=[27],
        parent_note={"number": "5", "title": "Material Accounting Policies"},
        sub_note={"number": "5.4", "title": "Property, Plant and Equipment"},
    )
    assert p.parent_note["number"] == "5"
    assert p.sub_note["number"] == "5.4"


def test_notes_payload_sub_note_is_optional():
    """sub_note defaults to None — legacy payloads without it still validate."""
    p = NotesPayload(
        chosen_row_label="Disclosure of revenue",
        content="Revenue is recognised...",
        evidence="Page 20",
        source_pages=[20],
        parent_note={"number": "5", "title": "Revenue"},
    )
    assert p.sub_note is None


def test_notes_payload_rejects_parent_note_missing_number():
    """parent_note without a non-empty `number` is invalid — the <h3> line
    would render as " Title" (leading space), which is obviously broken."""
    with pytest.raises(ValueError, match="parent_note.*number"):
        NotesPayload(
            chosen_row_label="Disclosure of revenue",
            content="Revenue is recognised...",
            evidence="Page 20",
            source_pages=[20],
            parent_note={"number": "", "title": "Revenue"},
        )


def test_notes_payload_rejects_parent_note_missing_title():
    with pytest.raises(ValueError, match="parent_note.*title"):
        NotesPayload(
            chosen_row_label="Disclosure of revenue",
            content="Revenue is recognised...",
            evidence="Page 20",
            source_pages=[20],
            parent_note={"number": "5", "title": ""},
        )


def test_notes_payload_rejects_sub_note_missing_number_or_title():
    with pytest.raises(ValueError, match="sub_note.*number"):
        NotesPayload(
            chosen_row_label="Property, plant and equipment",
            content="PPE is stated at cost.",
            evidence="Page 27",
            source_pages=[27],
            parent_note={"number": "5", "title": "Material Accounting Policies"},
            sub_note={"number": "", "title": "Property, Plant and Equipment"},
        )
    with pytest.raises(ValueError, match="sub_note.*title"):
        NotesPayload(
            chosen_row_label="Property, plant and equipment",
            content="PPE is stated at cost.",
            evidence="Page 27",
            source_pages=[27],
            parent_note={"number": "5", "title": "Material Accounting Policies"},
            sub_note={"number": "5.4", "title": ""},
        )


def test_notes_payload_requires_parent_note_when_content_nonempty():
    """Parent heading is mandatory on any payload that actually writes
    prose. Mirrors the evidence-required gate: empty "I looked and found
    nothing" payloads remain exempt, consistent with existing behaviour.
    """
    with pytest.raises(ValueError, match="parent_note"):
        NotesPayload(
            chosen_row_label="Disclosure of revenue",
            content="Revenue is recognised when control transfers.",
            evidence="Page 20",
            source_pages=[20],
            # parent_note intentionally omitted — must fail validation.
        )


def test_notes_payload_requires_parent_note_when_numeric_nonempty():
    """Structured numeric notes (Sheets 13/14) also need a parent heading
    so the cell isn't an unlabelled number."""
    with pytest.raises(ValueError, match="parent_note"):
        NotesPayload(
            chosen_row_label="Shares issued and fully paid",
            content="",
            evidence="Page 42, Note 14",
            source_pages=[42],
            numeric_values={"company_cy": 1000.0, "company_py": 900.0},
        )


def test_notes_payload_empty_payload_exempt_from_parent_note():
    """An empty payload (no content AND no numeric_values) is a deliberate
    "I looked and there's nothing to say" signal. Mirrors how the evidence
    requirement is waived for empty payloads."""
    # Must not raise.
    p = NotesPayload(
        chosen_row_label="Disclosure of revenue",
        content="",
        evidence="",
        source_pages=[],
    )
    assert p.parent_note is None
    assert p.sub_note is None
