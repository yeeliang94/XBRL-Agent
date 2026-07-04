"""detect_topline_splits — unit pins (Phase 2 of
docs/PLAN-notes-coverage-and-routing.md).

The detector reports top-level notes fragmented across ≥2 rows of the List
of Notes sheet ONLY (Codex review 2026-07-04: every other sheet
legitimately multi-rows one note — policies fan-out/carve-outs, Corporate
Information field rows, numeric sheets). gotcha-#14-safe: candidates by
note_num + coordinates only; the reviewer judges content against the PDF.
"""
from __future__ import annotations

from notes.detectors import detect_topline_splits


S12 = "Notes-Listofnotes"
S11 = "Notes-SummaryofAccPol"
S10 = "Notes-CI"
S13 = "Notes-Issuedcapital"


def _entry(sheet, row, refs, label=""):
    return {
        "sheet": sheet,
        "row": row,
        "row_label": label,
        "source_note_refs": refs,
        "content_preview": "",
    }


def test_split_across_two_rows_is_flagged():
    """The PP&E/leases failure mode: note 12 content on the PP&E row AND
    the leases row of Sheet 12."""
    entries = [
        _entry(S12, 31, ["12"], "Disclosure of property, plant and equipment"),
        _entry(S12, 44, ["12.2"], "Disclosure of leases"),
    ]
    splits = detect_topline_splits(entries)
    assert len(splits) == 1
    s = splits[0]
    assert s["note_num"] == 12
    assert s["sheet"] == S12
    assert [r["row"] for r in s["rows"]] == [31, 44]
    assert s["source_note_refs"] == ["12", "12.2"]


def test_single_placement_not_flagged():
    entries = [
        _entry(S12, 31, ["12"]),
        _entry(S12, 48, ["9"]),
    ]
    assert detect_topline_splits(entries) == []


def test_policies_sheet_fan_out_not_flagged():
    """Direction 2: the policies note fanning out across many Sheet-11 rows
    is the legitimate multi-field case."""
    entries = [
        _entry(S11, 10, ["3.2"]),
        _entry(S11, 14, ["3.5"]),
        _entry(S11, 22, ["3.7"]),
    ]
    assert detect_topline_splits(entries) == []


def test_carve_out_pair_not_flagged():
    """Direction 1: note 9 disclosure on Sheet 12 + its labelled policy
    sub-section carved out to Sheet 11 is one placement per sheet — no
    same-sheet fragmentation."""
    entries = [
        _entry(S12, 48, ["9"], "Disclosure of investment property"),
        _entry(S11, 14, ["9"], "Description of accounting policy for investment property"),
    ]
    assert detect_topline_splits(entries) == []


def test_multiple_payloads_same_row_not_flagged():
    """A note written in two halves to the SAME row (writer concatenates)
    is one placement, not a split."""
    entries = [
        _entry(S12, 60, ["13"]),
        _entry(S12, 60, ["13.1"]),
    ]
    assert detect_topline_splits(entries) == []


def test_one_finding_per_note_and_sheet_with_all_rows():
    """Three-way fragmentation yields ONE finding listing all three rows,
    not pairwise noise."""
    entries = [
        _entry(S12, 20, ["18"]),
        _entry(S12, 25, ["18(a)"]),
        _entry(S12, 30, ["18(b)"]),
    ]
    splits = detect_topline_splits(entries)
    assert len(splits) == 1
    assert [r["row"] for r in splits[0]["rows"]] == [20, 25, 30]


def test_refless_entries_ignored():
    entries = [
        _entry(S12, 31, []),
        _entry(S12, 44, None),
    ]
    assert detect_topline_splits(entries) == []


def test_corporate_info_multi_row_not_flagged():
    """Corporate Information legitimately splits the one corporate-info
    note across its field rows (name, directors, registered office…)."""
    entries = [
        _entry(S10, 6, ["1"], "Name of company"),
        _entry(S10, 8, ["1"], "Registered office"),
        _entry(S10, 10, ["1"], "Principal activities"),
    ]
    assert detect_topline_splits(entries) == []


def test_numeric_sheet_multi_row_not_flagged():
    """Numeric sheets (13/14) populate several rows from one note by
    design — and the reviewer only edits prose sheets anyway."""
    entries = [
        _entry(S13, 12, ["15"]),
        _entry(S13, 14, ["15"]),
    ]
    assert detect_topline_splits(entries) == []


def test_distinct_notes_on_distinct_rows_not_flagged():
    """Two different notes each owning their own row is the healthy state —
    a collision (2 notes, 1 row) is a different detector's job."""
    entries = [
        _entry(S12, 31, ["12"]),
        _entry(S12, 44, ["24"]),
    ]
    assert detect_topline_splits(entries) == []


# --------------------------------------------------------------------------
# Reviewer wiring — packet rendering + finding identity
# --------------------------------------------------------------------------

def _split_finding():
    return {
        "note_num": 12,
        "sheet": S12,
        "rows": [
            {"row": 31, "row_label": "Disclosure of property, plant and equipment"},
            {"row": 44, "row_label": "Disclosure of leases"},
        ],
        "source_note_refs": ["12", "12.2"],
    }


def test_packet_renders_topline_split_block():
    import notes.reviewer_agent as ra

    packet = ra.build_notes_reviewer_packet({"topline_splits": [_split_finding()]})
    assert "TOP-LINE SPLIT" in packet
    assert "row 31" in packet and "row 44" in packet
    # The three verdict paths the reviewer must weigh.
    assert "merely because a topic is MENTIONED" in packet
    assert "material/significant accounting policy" in packet
    assert "raise_flag" in packet


def test_packet_duplication_block_names_the_carve_out_partition():
    """The dup block must present the carve-out partition as a legitimate
    shape BEFORE telling the reviewer to clear anything — otherwise a
    correct Direction-1 carve-out gets deleted as a 'duplicate'."""
    import notes.reviewer_agent as ra

    packet = ra.build_notes_reviewer_packet({
        "duplicates": [{"note_ref": "9",
                        "sheet_11": {"row": 14}, "sheet_12": {"row": 48}}],
    })
    assert "CARVE-OUT PARTITION" in packet
    assert "leave both" in packet


def test_finding_keys_include_topline_split():
    import notes.reviewer_agent as ra

    keys = ra.finding_keys({"topline_splits": [_split_finding()]})
    assert ("topline_split", 12, S12, (31, 44)) in keys


def test_clean_packet_unaffected():
    import notes.reviewer_agent as ra

    packet = ra.build_notes_reviewer_packet({"topline_splits": []})
    assert "No structural findings" in packet


# --------------------------------------------------------------------------
# FINDING_FAMILIES — the single source of truth for "does the reviewer run"
# (Codex review 2026-07-04: topline_splits was in the packet but not the
# server skip gate, so a split-only run skipped the reviewer entirely)
# --------------------------------------------------------------------------

_MINIMAL_FINDING_BY_FAMILY = {
    "duplicates": {"note_ref": "9", "sheet_11": {"row": 14}, "sheet_12": {"row": 48}},
    "overlap_candidates": {"score": 0.9, "sheet_11": {"row": 14}, "sheet_12": {"row": 48}},
    "coverage_gaps": 7,
    "row_collisions": {"row": 49, "row_label": "FV", "note_nums": [4, 20],
                       "source_note_refs": ["4.1", "20.7"]},
    "subnote_gaps": {"note_num": 3, "cited_subnote_refs": ["3.1"],
                     "missing_subnote_refs": ["3.2"], "all_subnote_refs": ["3.1", "3.2"]},
    "topline_splits": _split_finding(),
    "title_issues": {"sheet": S12, "row": 49, "row_label": "X"},
}


def test_finding_families_covers_every_packet_family():
    """Every family, alone, must make the packet non-clean — i.e. the
    FINDING_FAMILIES tuple and the packet renderer agree. A family the
    packet renders but the tuple omits would be skipped by the server gate
    (the original bug); a tuple entry the packet ignores would run the
    reviewer with an empty packet."""
    import notes.reviewer_agent as ra

    assert set(_MINIMAL_FINDING_BY_FAMILY) == set(ra.FINDING_FAMILIES)
    for family, finding in _MINIMAL_FINDING_BY_FAMILY.items():
        packet = ra.build_notes_reviewer_packet({family: [finding]})
        assert "No structural findings" not in packet, family


def test_server_skip_gate_uses_the_shared_family_tuple():
    """The n_items skip gate in _run_notes_reviewer_pass must derive from
    FINDING_FAMILIES, not a hand-copied tuple that can drift.

    The gate now goes through ``count_open_items`` (which folds the coverage
    checklist's unresolved rows into the family sum), so assert BOTH that the
    server uses that shared helper AND that the helper itself iterates
    FINDING_FAMILIES — the same no-drift guarantee, one indirection deeper."""
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    server_src = (root / "server.py").read_text(encoding="utf-8")
    assert "count_open_items(context)" in server_src

    ra_src = (root / "notes" / "reviewer_agent.py").read_text(encoding="utf-8")
    # count_open_items sums over FINDING_FAMILIES, so a new detector can never be
    # counted by one layer and ignored by the other.
    assert "def count_open_items" in ra_src
    assert "for k in FINDING_FAMILIES" in ra_src
