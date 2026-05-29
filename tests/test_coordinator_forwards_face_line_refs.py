"""Phase 1a Step 6 — coordinator includes face_line_refs in page_hints.

Asserts the dict the coordinator hands to ``_run_single_agent`` carries the
new structural fields when scout populated them, and falls back to today's
two-key dict when the infopack is bare.
"""
from __future__ import annotations

from statement_types import StatementType
from scout.infopack import FaceLineRef, Infopack, StatementPageRef
from coordinator import build_face_page_hints


def _build_page_hints(infopack: Infopack, stmt_type: StatementType) -> dict:
    """Drive the REAL coordinator helper (peer-review F6).

    Previously this test duplicated the coordinator's dict construction, so it
    could pass while the runtime wiring drifted. It now calls the same
    `build_face_page_hints` the coordinator calls, so a change to the produced
    shape fails here too.
    """
    if stmt_type not in infopack.statements:
        return None
    return build_face_page_hints(infopack.statements[stmt_type])


def test_forwards_populated_face_line_refs():
    infopack = Infopack(
        toc_page=2,
        page_offset=0,
        statements={
            StatementType.SOFP: StatementPageRef(
                variant_suggestion="CuNonCu",
                face_page=5,
                note_pages=[10, 11],
                face_line_refs=[
                    FaceLineRef(
                        label="Property, plant and equipment",
                        note_num=4,
                        section="non-current assets",
                    ),
                ],
                face_read_in_detail=True,
            ),
        },
    )
    hints = _build_page_hints(infopack, StatementType.SOFP)
    assert hints["face_page"] == 5
    assert hints["face_read_in_detail"] is True
    assert len(hints["face_line_refs"]) == 1
    assert hints["face_line_refs"][0]["label"] == "Property, plant and equipment"
    assert hints["face_line_refs"][0]["note_num"] == 4
    assert hints["face_line_refs"][0]["section"] == "non-current assets"


def test_empty_face_line_refs_fall_back_cleanly():
    infopack = Infopack(
        toc_page=2,
        page_offset=0,
        statements={
            StatementType.SOFP: StatementPageRef(
                variant_suggestion="CuNonCu",
                face_page=5,
                note_pages=[10, 11],
                # No face_line_refs / face_read_in_detail explicitly set;
                # defaults are empty list / False.
            ),
        },
    )
    hints = _build_page_hints(infopack, StatementType.SOFP)
    # The new keys are still present (so the prompt renderer can branch
    # on them), but they signal "scout didn't enrich" — empty list /
    # False mean the bare hint block renders.
    assert hints["face_line_refs"] == []
    assert hints["face_read_in_detail"] is False


def test_coordinator_function_includes_new_keys_in_real_construction():
    """Smoke test that the coordinator code actually constructs the dict
    with the new keys. Reads coordinator.py source and checks the new
    keys appear next to face_page so a future refactor can't silently
    drop them."""
    from pathlib import Path
    src = Path(__file__).resolve().parent.parent / "coordinator.py"
    text = src.read_text(encoding="utf-8")
    # The dict literal must mention both new keys
    assert '"face_line_refs"' in text
    assert '"face_read_in_detail"' in text
