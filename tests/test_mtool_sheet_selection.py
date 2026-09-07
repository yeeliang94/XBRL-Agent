"""Sheet selection excludes both numeric and prose writes before resolution."""
import pytest

from mtool.sheet_selection import select_sheets, scope_notes, validate_note_destinations


def test_selection_filters_figures_and_aligned_note_revision_without_mutating_source():
    numeric = {"writes": [{"sheet": "SOFP", "value": 1}, {"sheet": "Notes-RelatedPartytran", "value": 2}],
               "sheets": {"SOFP": {}, "Notes-RelatedPartytran": {}}, "meta": {"counts": {"writes": 2}}}
    notes = {"footnotes": [{"source_sheet": "Notes-RelatedPartytran"}, {"source_sheet": "Notes-CI"}],
             "meta": {"notes_revision": [{"identity": "excluded"}, {"identity": "included"}], "counts": {"notes": 2}}}
    doc, selection = select_sheets('["SOFP", "Notes-CI"]', numeric, {n["source_sheet"] for n in notes["footnotes"]})
    scoped = scope_notes(notes, selection)
    assert doc["writes"] == [{"sheet": "SOFP", "value": 1}]
    assert set(doc["sheets"]) == {"SOFP"}
    assert scoped["meta"]["notes_revision"] == [{"identity": "included"}]
    assert scoped["meta"]["counts"]["notes"] == 1
    assert selection["excluded_sheets"] == ["Notes-RelatedPartytran"]
    assert selection["excluded_figures"] == selection["excluded_notes"] == 1
    assert len(numeric["writes"]) == len(notes["footnotes"]) == 2


@pytest.mark.parametrize("raw", ['[]', '{}', '[1]', '["Unknown"]', 'null', 'bad'])
def test_invalid_or_empty_selection_is_rejected(raw):
    with pytest.raises(ValueError):
        select_sheets(raw, {"writes": [], "sheets": {"SOFP": {}}}, set())


def test_explicit_note_destinations_cannot_cross_into_excluded_sheets():
    selection = {"selected_sheets": ["Notes-CI"]}
    for target in ({"sheet": "notes-ci", "cell": "E14"}, {"sheet": "Notes-RelatedPartytran", "cell": "E12"}, {"key": "fn_2"}):
        with pytest.raises(ValueError, match="selected sheets"):
            validate_note_destinations({"footnotes": [target]}, selection,
                                       {"fn_2": {"sheet": "Notes-RelatedPartytran"}})
