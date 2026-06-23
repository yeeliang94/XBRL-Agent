"""Notes-reviewer no-fabrication guard (docs/PLAN.md Step 6).

One rejection case per kind + the accept case. Pure function, no agent.
"""
from __future__ import annotations

from notes.reviewer_agent import REJECTION_KINDS, classify_notes_fix_guard

_VIEWED = {19, 20}


def test_ungrounded_empty_source_pages():
    kind, msg = classify_notes_fix_guard(
        action="edit", source_pages=[], viewed_pages=_VIEWED,
    )
    assert kind == "ungrounded" and "ground" in msg.lower()


def test_ungrounded_page_not_viewed():
    kind, _ = classify_notes_fix_guard(
        action="edit", source_pages=[99], viewed_pages=_VIEWED,
    )
    assert kind == "ungrounded"


def test_not_leaf_when_target_missing():
    kind, _ = classify_notes_fix_guard(
        action="author", source_pages=[19], viewed_pages=_VIEWED,
        target_node=None, note_in_inventory=True,
    )
    assert kind == "not_leaf"


def test_not_leaf_when_target_is_abstract():
    kind, _ = classify_notes_fix_guard(
        action="author", source_pages=[19], viewed_pages=_VIEWED,
        target_node={"kind": "ABSTRACT"}, note_in_inventory=True,
    )
    assert kind == "not_leaf"


def test_occupied_target_refused():
    kind, _ = classify_notes_fix_guard(
        action="move", source_pages=[19], viewed_pages=_VIEWED,
        target_node={"kind": "LEAF"}, target_occupied=True,
    )
    assert kind == "occupied_target"


def test_author_for_unknown_note_refused():
    kind, _ = classify_notes_fix_guard(
        action="author", source_pages=[19], viewed_pages=_VIEWED,
        target_node={"kind": "LEAF"}, target_occupied=False,
        note_in_inventory=False,
    )
    assert kind == "note_not_in_inventory"


def test_grounded_leaf_author_accepts():
    kind, msg = classify_notes_fix_guard(
        action="author", source_pages=[19], viewed_pages=_VIEWED,
        target_node={"kind": "LEAF"}, target_occupied=False,
        note_in_inventory=True,
    )
    assert kind is None and msg is None


def test_grounded_edit_accepts_without_target_rules():
    kind, msg = classify_notes_fix_guard(
        action="edit", source_pages=[19], viewed_pages=_VIEWED,
    )
    assert kind is None and msg is None


def test_all_rejection_kinds_are_declared():
    # Every kind the guard can return must be in the telemetry vocabulary.
    seen = set()
    for case in [
        dict(action="edit", source_pages=[], viewed_pages=_VIEWED),
        dict(action="author", source_pages=[19], viewed_pages=_VIEWED,
             target_node=None, note_in_inventory=True),
        dict(action="author", source_pages=[19], viewed_pages=_VIEWED,
             target_node={"kind": "LEAF"}, target_occupied=True,
             note_in_inventory=True),
        dict(action="author", source_pages=[19], viewed_pages=_VIEWED,
             target_node={"kind": "LEAF"}, target_occupied=False,
             note_in_inventory=False),
    ]:
        k, _ = classify_notes_fix_guard(**case)
        if k:
            seen.add(k)
    assert seen <= set(REJECTION_KINDS)
    assert seen == {"ungrounded", "not_leaf", "occupied_target", "note_not_in_inventory"}
