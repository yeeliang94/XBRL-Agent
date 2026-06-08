"""Plan B — concept-definition index generator + runtime search.

Pins:
- the generator joins concept_id -> definition for BOTH standards and resolves
  the user's worked example (other current payables vs non-trade payables);
- the runtime search returns grouped results for a multi-term query, respects
  standard scoping, and returns an explicit no-match for unknown terms.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from concept_model import definitions as D

REPO = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------
# Committed index (generator output)
# --------------------------------------------------------------------------

@pytest.mark.parametrize("standard", ["mfrs", "mpers"])
def test_committed_index_exists_and_is_nonempty(standard: str) -> None:
    path = REPO / "concept_model" / f"concept_definitions_{standard}.json"
    assert path.exists(), f"missing committed index {path} — run the generator"
    entries = json.loads(path.read_text(encoding="utf-8"))
    assert len(entries) > 100
    sample = entries[0]
    assert set(sample) >= {"concept_id", "label", "label_normalized", "definition"}


@pytest.mark.parametrize("standard", ["mfrs", "mpers"])
def test_worked_example_concepts_have_distinct_definitions(standard: str) -> None:
    """The trade vs non-trade payables disambiguation resolves in both standards.

    (The bare "Other current payables" leaf is MPERS/IFRS-namespace vocabulary,
    not an MFRS template row — so the cross-standard contrast that genuinely
    exists, and that the agent actually faces, is trade vs non-trade.)
    """
    entries = json.loads(
        (REPO / "concept_model" / f"concept_definitions_{standard}.json").read_text("utf-8")
    )
    by_norm: dict[str, dict] = {}
    for e in entries:
        by_norm.setdefault(e["label_normalized"], e)

    non_trade = by_norm.get("other current non-trade payables")
    trade = by_norm.get("trade payables")
    assert non_trade is not None, "non-trade payables concept missing from index"
    assert trade is not None, "trade payables concept missing from index"
    # Distinct concepts, distinct official prose.
    assert non_trade["concept_id"] != trade["concept_id"]
    assert non_trade["definition"] != trade["definition"]
    assert non_trade["definition"].strip()


# --------------------------------------------------------------------------
# Runtime search
# --------------------------------------------------------------------------

def test_search_is_batched_and_grouped_by_query() -> None:
    res = D.search(
        ["other current non-trade payables", "accruals"],
        standard="mfrs",
    )
    assert set(res) == {"other current non-trade payables", "accruals"}
    for q, r in res.items():
        assert r["matches"], f"expected a match for {q!r}"
        assert {"concept_id", "label", "definition", "score"} <= set(r["matches"][0])


def test_search_exact_label_ranks_first() -> None:
    res = D.search(["other current non-trade payables"], standard="mfrs")
    top = res["other current non-trade payables"]["matches"][0]
    assert "non-trade payables" in top["label"].lower()
    assert top["score"] == 1.0  # exact normalised-label match scores top


def test_search_unknown_term_returns_explicit_no_match() -> None:
    res = D.search(["zzz not a real concept xyz"], standard="mfrs")
    r = res["zzz not a real concept xyz"]
    assert r["matches"] == []
    assert "no concept matched" in r["no_match"].lower()


def test_search_respects_standard_scoping() -> None:
    """An MPERS-only concept must not surface under MFRS (different uuids/sets)."""
    # SoRE / statement-of-retained-earnings vocabulary is MPERS-only.
    mpers = D.search(["retained earnings at end of the period"], standard="mpers")
    mfrs = D.search(["retained earnings at end of the period"], standard="mfrs")
    # Both may return *something* fuzzy, but the concept_ids must be namespaced
    # to their own standard — no MPERS concept_id leaks into an MFRS result.
    for r in mfrs.values():
        for m in r["matches"]:
            assert not m["concept_id"].startswith("ssmt-mpers_")
    for r in mpers.values():
        for m in r["matches"]:
            assert not m["concept_id"].startswith("ssmt-mfrs_")


def test_search_unknown_standard_raises() -> None:
    with pytest.raises(ValueError):
        D.search(["anything"], standard="gaap")


def test_truncated_flag_when_more_than_top_k() -> None:
    # A broad term ("payables") matches many concepts; with top_k=2 the result
    # must flag truncation rather than silently dropping the rest.
    res = D.search(["payables"], standard="mfrs", top_k=2)
    r = res["payables"]
    assert len(r["matches"]) <= 2
    assert r.get("truncated") is True
