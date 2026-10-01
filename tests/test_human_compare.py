"""Comparison formulas for a run versus its human mTool file (eval/human_compare.py)."""
from __future__ import annotations

from eval.human_compare import compare_figures, compare_notes, derive_totals


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
        "human_filled": 50, "both_filled": 45, "same_value": 40,
        "ai_only": 3, "zero_blank_excluded": 0}}
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
        "Group": {"human_filled": 2, "both_filled": 1, "same_value": 1,
                  "ai_only": 0, "zero_blank_excluded": 0},
        "Company": {"human_filled": 1, "both_filled": 1, "same_value": 1,
                    "ai_only": 0, "zero_blank_excluded": 0},
    }
    assert [s["status"] for s in result["slots"]
            if s["period"] == "PY"] == ["missed"]


def test_totals_are_shown_but_never_scored_and_unaddressable_slots_are_left_out():
    human = {
        ("total", "CY", "Company", ""): {"value": 90.0, "calculated": True},
        ("blank_total", "CY", "Company", ""): {"value": None, "calculated": True},
        ("u", "CY", "Company", ""): _typed(5.0),
    }
    ai = {("u", "CY", "Company", ""): 5.0,
          ("elsewhere", "CY", "Company", ""): 7.0}
    ai_totals = {**ai, ("total", "CY", "Company", ""): 99.0}

    result = compare_figures(human, ai, ai_totals)

    assert result["totals"]["Company"] == {
        "human_filled": 1, "both_filled": 1, "same_value": 1,
        "ai_only": 0, "zero_blank_excluded": 0}
    assert result["excluded"] == {"calculated": 2, "not_addressable": 1}
    total = next(s for s in result["slots"] if s["concept_uuid"] == "total")
    assert (total["status"], total["calculated"], total["human_value"],
            total["ai_value"]) == ("different", True, 90.0, 99.0)
    assert "blank_total" not in {s["concept_uuid"] for s in result["slots"]}


def test_human_totals_are_derived_from_the_human_inputs():
    # total = a - b; grand = total + c. A typed total is kept; a total with
    # no numeric child stays blank; each period is its own sum.
    edges = {"total": [("a", 1.0), ("b", -1.0)],
             "grand": [("total", 1.0), ("c", 1.0)],
             "empty": [("x", 1.0)]}
    values = {("a", "CY", "Company", ""): 10.0, ("b", "CY", "Company", ""): 3.0,
              ("c", "CY", "Company", ""): 1.0, ("a", "PY", "Company", ""): 4.0,
              ("grand", "PY", "Company", ""): 50.0}
    keys = [(u, p, "Company", "") for u in ("total", "grand", "empty")
            for p in ("CY", "PY")]

    assert derive_totals(values, edges, keys) == {
        ("total", "CY", "Company", ""): 7.0,
        ("grand", "CY", "Company", ""): 8.0,
        ("total", "PY", "Company", ""): 4.0,
        ("grand", "PY", "Company", ""): 50.0,
    }


def test_human_zero_ai_blank_stays_visible_but_does_not_affect_figures_totals():
    human = {
        ("excluded", "CY", "Company", ""): _typed(0),
        ("true_zero", "PY", "Company", ""): _typed(0),
        ("missed", "CY", "Company", ""): _typed(10),
        ("other_scope", "CY", "Group", ""): _typed(0),
    }
    ai = {("true_zero", "PY", "Company", ""): 0.0}

    result = compare_figures(human, ai)

    assert result["totals"] == {
        "Company": {"human_filled": 2, "both_filled": 1, "same_value": 1,
                    "ai_only": 0, "zero_blank_excluded": 1},
        "Group": {"human_filled": 0, "both_filled": 0, "same_value": 0,
                  "ai_only": 0, "zero_blank_excluded": 1},
    }
    assert {s["concept_uuid"]: s["status"] for s in result["slots"]} == {
        "excluded": "zero_blank", "true_zero": "agree",
        "missed": "missed", "other_scope": "zero_blank",
    }


def test_notes_compare_placement_only():
    result = compare_notes({"a": "<p>x</p>", "b": "<p>y</p>"}, {"a", "c"})

    assert result["totals"] == {"human_filled": 2, "both_filled": 1, "ai_only": 1}
    assert {f["concept_uuid"]: f["status"] for f in result["fields"]} == {
        "a": "agree", "b": "missed", "c": "ai_only"}
