"""A tool's description must not contradict the prompt that governs it.

Peer review, 2026-08-01. Four places where the notes agents were told two
different things. A tool description sits closer to the decision than the
static body does, so where they disagree the description tends to win — which
is the wrong way round when the body carries the contract.

1. `_notes_base.md` promised "a standard house style applies instead" when
   `format_ops` is omitted. The deterministic house-style floor was REMOVED on
   2026-07-07 (gotcha #16) precisely because it invented borders the source
   did not have. The same file already said, correctly, that an omitted or
   invalid op set renders plain and "nothing adds borders, rules or fills on
   your behalf".

2. `submit_batch_coverage` offered "skipped" for "notes that don't fit any
   Sheet-12 row", while `notes_listofnotes.md` says such a note is "**never**
   skipped — it goes to the catch-all row". A note skipped under the tool's
   wording is a real disclosure silently dropped from the filing.

3. `write_note_from_source` told the agent to "record why with the disposition
   tool". No disposition tool is registered on an extraction agent;
   `record_block_dispositions` exists only on the notes REVIEWER.

4. `notes_reviewer.md` and its generated packet hardcoded the MFRS slot
   numbers. MPERS puts the same notes one slot higher (gotcha #15), so an
   MPERS reviewer was directed at the wrong sheets.

5. The notes reviewer prompt told the model to merge one Sheet-12 placement
   into another and clear the source, while the deterministic guard now
   preserves same-sheet routing precision. The prompt and packet must direct
   that semantic decision to a human instead of teaching a tool call that is
   refused.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from notes.agent import _apply_cross_sheet_tokens
from notes.reviewer_agent import build_notes_reviewer_packet


_PROMPTS = Path(__file__).resolve().parent.parent / "prompts"
_NOTES_BASE = (_PROMPTS / "_notes_base.md").read_text(encoding="utf-8")
_LISTOFNOTES = (_PROMPTS / "notes_listofnotes.md").read_text(encoding="utf-8")
_REVIEWER = (_PROMPTS / "notes_reviewer.md").read_text(encoding="utf-8")


def _tool_descriptions(agent) -> dict[str, str]:
    out: dict[str, str] = {}
    for ts in getattr(agent, "toolsets", []) or []:
        tools = getattr(ts, "tools", {}) or {}
        if isinstance(tools, dict):
            for name, tool in tools.items():
                desc = getattr(tool, "description", None)
                if desc is None:
                    fn = getattr(tool, "function", None)
                    desc = getattr(fn, "__doc__", "") or ""
                out[name] = desc or ""
    return out


# --------------------------------------------------------------------------
# 1. Formatting ownership
# --------------------------------------------------------------------------

def test_notes_base_does_not_promise_a_house_style():
    assert "house style applies" not in _NOTES_BASE
    assert "standard house style" not in _NOTES_BASE


def test_notes_base_assigns_styling_to_the_formatter():
    assert "format_ops" not in _NOTES_BASE
    assert "dedicated notes formatter" in _NOTES_BASE


# --------------------------------------------------------------------------
# 2. Sheet-12 skip contract
# --------------------------------------------------------------------------

def test_listofnotes_body_still_forbids_skipping_a_real_disclosure():
    """The contract this test defends — if it moves, the tool moves with it."""
    assert "is **never** skipped" in _LISTOFNOTES
    assert "The catch-all is the sink, not a bin" in _LISTOFNOTES


def test_batch_coverage_tool_matches_the_never_skip_contract(tmp_path):
    from pydantic_ai.models.test import TestModel

    from notes import agent as notes_agent
    from notes_types import NotesTemplateType

    agent, _deps = notes_agent.create_notes_agent(
        template_type=NotesTemplateType.LIST_OF_NOTES, pdf_path="/tmp/no.pdf",
        inventory=[], filing_level="company", model=TestModel(),
        output_dir=str(tmp_path), batch_note_nums=[1, 2],
    )
    desc = _tool_descriptions(agent).get("submit_batch_coverage", "")
    assert desc, "submit_batch_coverage not registered in Sheet-12 mode"
    flat_desc = " ".join(desc.split())
    # The old wording offered a skip for a note that fits no row. That is the
    # exact case the body says must go to the catch-all.
    assert "don't fit any Sheet-12 row or belong on" not in flat_desc
    assert "NEVER skipped" in flat_desc
    assert "catch-all" in flat_desc
    assert "(Accounting Policies or Corporate Information)" in flat_desc
    assert (
        "Related Party Transactions is an intentional dual placement and is "
        "NOT a valid skip"
    ) in flat_desc


# --------------------------------------------------------------------------
# 3. Phantom disposition tool
# --------------------------------------------------------------------------

def test_source_write_tool_does_not_cite_a_tool_it_does_not_have(tmp_path):
    """Every tool named in a description must be registered on that agent."""
    import notes.agent as notes_agent
    src = Path(notes_agent.__file__).read_text(encoding="utf-8")
    assert "record why with the disposition tool" not in src
    assert "disposition tool" not in src


# --------------------------------------------------------------------------
# 4. Reviewer sheet numbering
# --------------------------------------------------------------------------

def test_reviewer_prompt_carries_no_bare_sheet_numbers():
    import re
    assert not re.findall(r"Sheets? ?-?1[0-5]\b", _REVIEWER), (
        "notes_reviewer.md must use {{CROSS_SHEET:*}} tokens, not MFRS slots"
    )


@pytest.mark.parametrize("standard,policies,listofnotes", [
    ("mfrs", "11", "12"),
    ("mpers", "12", "13"),
])
def test_reviewer_prompt_resolves_per_standard(standard, policies, listofnotes):
    rendered = _apply_cross_sheet_tokens(_REVIEWER, standard)
    assert "CROSS_SHEET" not in rendered
    assert f"Sheet {policies}" in rendered
    assert f"Sheet {listofnotes}" in rendered


@pytest.mark.parametrize("standard,policies,listofnotes", [
    ("mfrs", "11", "12"),
    ("mpers", "12", "13"),
])
def test_reviewer_packet_resolves_per_standard(standard, policies, listofnotes):
    """The dynamic packet hardcoded the same numbers as the static body, so
    fixing only the body would leave the contradiction in place."""
    context = {"duplicates": [
        {"note_ref": "5", "sheet_11": {"row": 7}, "sheet_12": {"row": 9}},
    ]}
    packet = build_notes_reviewer_packet(context)
    # Tokens must survive f-string rendering (`{{` collapses to `{`).
    assert "{{CROSS_SHEET:accounting_policies}}" in packet
    rendered = _apply_cross_sheet_tokens(packet, standard)
    assert "CROSS_SHEET" not in rendered
    assert f"Sheet {policies} row 7" in rendered
    assert f"Sheet {listofnotes} row 9" in rendered


# --------------------------------------------------------------------------
# 5. Same-sheet clear safety
# --------------------------------------------------------------------------

def test_reviewer_prompt_routes_same_sheet_consolidation_to_human():
    assert "refuses same-Sheet-{{CROSS_SHEET:list_of_notes}} consolidation" in _REVIEWER
    assert "raise_flag(kind='needs_human'" in _REVIEWER
    assert "then `clear_note_cells` the fragment" not in _REVIEWER


def test_reviewer_packet_matches_same_sheet_clear_guard():
    packet = build_notes_reviewer_packet({
        "topline_splits": [{
            "note_num": 9,
            "sheet": "Notes-Listofnotes",
            "rows": [
                {"row": 48, "row_label": "Dedicated"},
                {"row": 49, "row_label": "Broad"},
            ],
            "source_note_refs": ["9"],
        }],
    })
    assert "clear guard refuses same-sheet consolidation" in packet
    assert "raise a needs_human flag" in packet
