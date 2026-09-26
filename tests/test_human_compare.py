"""Comparison formulas for a run versus its human mTool file (eval/human_compare.py)."""
from __future__ import annotations

from eval.human_compare import compare_figures, compare_notes


def _typed(value):
    return {"value": value, "calculated": False}


def test_found_same_value_and_ai_only_follow_the_agreed_formulas():
    # The human filled 50 slots. The AI filled 45 of them (40 equal, 5 not),
    # left 5 empty, and filled 3 slots the human left blank.
    human = {(f"u{i}", "CY", "Company", ""): _typed(float(i)) for i in range(50)}
    human.update({(f"x{i}", "CY", "Company", ""): _typed(None) for i in range(3)})
    ai = {(f"u{i}", "CY", "Company", ""): float(i) for i in range(40)}
    ai.update({(f"u{i}", "CY", "Company", ""): i + 0.5 for i in range(40, 45)})
    ai.update({(f"x{i}", "CY", "Company", ""): 1.0 for i in range(3)})

    result = compare_figures(human, ai)

    assert result["totals"] == {"Company": {
        "human_filled": 50, "both_filled": 45, "same_value": 40, "ai_only": 3}}
    statuses = {s["concept_uuid"]: s["status"] for s in result["slots"]}
    assert statuses["u0"] == "agree"
    assert statuses["u41"] == "different"
    assert statuses["u47"] == "missed"
    assert statuses["x0"] == "ai_only"


def test_each_period_and_scope_is_its_own_slot():
    human = {
        ("u", "CY", "Group", ""): _typed(10.0),
        ("u", "PY", "Group", ""): _typed(9.0),
        ("u", "CY", "Company", ""): _typed(4.0),
    }
    ai = {("u", "CY", "Group", ""): 10.0, ("u", "CY", "Company", ""): 4.0}

    result = compare_figures(human, ai)

    assert result["totals"] == {
        "Group": {"human_filled": 2, "both_filled": 1, "same_value": 1, "ai_only": 0},
        "Company": {"human_filled": 1, "both_filled": 1, "same_value": 1, "ai_only": 0},
    }
    assert [s["status"] for s in result["slots"]
            if s["period"] == "PY"] == ["missed"]


def test_calculated_and_unaddressable_slots_are_left_out():
    human = {
        ("total", "CY", "Company", ""): {"value": None, "calculated": True},
        ("u", "CY", "Company", ""): _typed(5.0),
    }
    ai = {("total", "CY", "Company", ""): 99.0,
          ("u", "CY", "Company", ""): 5.0,
          ("elsewhere", "CY", "Company", ""): 7.0}

    result = compare_figures(human, ai)

    assert result["totals"]["Company"] == {
        "human_filled": 1, "both_filled": 1, "same_value": 1, "ai_only": 0}
    assert result["excluded"] == {"calculated": 1, "not_addressable": 1}


def test_notes_compare_placement_only():
    result = compare_notes({"a": "<p>x</p>", "b": "<p>y</p>"}, {"a", "c"})

    assert result["totals"] == {"human_filled": 2, "both_filled": 1, "ai_only": 1}
    assert {f["concept_uuid"]: f["status"] for f in result["fields"]} == {
        "a": "agree", "b": "missed", "c": "ai_only"}
