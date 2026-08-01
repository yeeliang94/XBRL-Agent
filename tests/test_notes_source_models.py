"""Source-integrity vocabulary and mode — plan Phase 3, Steps 3.2 / 3.5.

The vocabularies are closed on purpose. "Not relevant" as free text is how a
completeness count quietly becomes meaningless: every awkward block gets a
bespoke excuse and nothing is ever unresolved. A fixed list means each excuse
was approved once, by a person, and can be counted.
"""
import pytest

from notes.source_models import (
    EXCLUSION_REASONS,
    UNRESOLVED_REASONS,
    ContentOrigin,
    Disposition,
    GenerationStatus,
    IntegrityMode,
    OwnerKind,
    integrity_mode,
    is_resolved,
    validate_disposition,
)


# --------------------------------------------------------------------------
# mode (peer-review finding 9: a boolean cannot express shadow)
# --------------------------------------------------------------------------

def test_mode_defaults_to_off():
    assert integrity_mode({}) is IntegrityMode.OFF


@pytest.mark.parametrize("raw,expected", [
    ("off", IntegrityMode.OFF),
    ("shadow", IntegrityMode.SHADOW),
    ("enforce", IntegrityMode.ENFORCE),
    ("ENFORCE", IntegrityMode.ENFORCE),
    ("  shadow  ", IntegrityMode.SHADOW),
])
def test_mode_parses_the_three_values(raw, expected):
    assert integrity_mode({"XBRL_NOTES_SOURCE_INTEGRITY": raw}) is expected


def test_legacy_boolean_values_still_work():
    """Operators and old .env files write 1/0. Accepting them avoids a run
    silently enforcing (or silently not) because of a stale value."""
    assert integrity_mode({"XBRL_NOTES_SOURCE_INTEGRITY": "1"}) is IntegrityMode.ENFORCE
    assert integrity_mode({"XBRL_NOTES_SOURCE_INTEGRITY": "0"}) is IntegrityMode.OFF


def test_an_unrecognised_mode_fails_closed_to_off():
    """A typo must not silently enforce and start failing runs."""
    assert integrity_mode({"XBRL_NOTES_SOURCE_INTEGRITY": "enfroce"}) is IntegrityMode.OFF


def test_only_enforce_changes_run_status():
    assert IntegrityMode.OFF.changes_run_status is False
    assert IntegrityMode.SHADOW.changes_run_status is False
    assert IntegrityMode.ENFORCE.changes_run_status is True


def test_shadow_still_computes():
    assert IntegrityMode.OFF.computes is False
    assert IntegrityMode.SHADOW.computes is True
    assert IntegrityMode.ENFORCE.computes is True


# --------------------------------------------------------------------------
# dispositions and reasons
# --------------------------------------------------------------------------

def test_every_disposition_is_accounted_for():
    assert {d.value for d in Disposition} == {
        "included", "structured_consumed", "routed", "excluded", "unresolved",
    }


def test_excluded_requires_a_reason_from_the_closed_list():
    validate_disposition(Disposition.EXCLUDED, "PAGE_FOOTER")
    with pytest.raises(ValueError):
        validate_disposition(Disposition.EXCLUDED, None)
    with pytest.raises(ValueError):
        validate_disposition(Disposition.EXCLUDED, "it looked unimportant")


def test_included_needs_no_reason():
    validate_disposition(Disposition.INCLUDED, None)


def test_unreadable_is_a_reason_but_never_resolves_a_block():
    """The one reason that must not close a block: 'we could not read it' is a
    description of the problem, not a decision about it."""
    assert "UNREADABLE_NEEDS_REVIEW" in EXCLUSION_REASONS
    assert "UNREADABLE_NEEDS_REVIEW" in UNRESOLVED_REASONS
    assert is_resolved(Disposition.EXCLUDED, "UNREADABLE_NEEDS_REVIEW") is False
    assert is_resolved(Disposition.EXCLUDED, "PAGE_FOOTER") is True


def test_unresolved_disposition_is_never_resolved():
    assert is_resolved(Disposition.UNRESOLVED, None) is False
    assert is_resolved(Disposition.UNRESOLVED, "PAGE_FOOTER") is False


@pytest.mark.parametrize("d", [
    Disposition.INCLUDED, Disposition.STRUCTURED_CONSUMED, Disposition.ROUTED,
])
def test_positive_dispositions_resolve(d):
    assert is_resolved(d, None) is True


def test_the_reason_list_matches_the_design_document():
    """Changing this list changes what 'complete' means, so it needs product
    and accounting sign-off — pinning it here makes an edit visible in review."""
    assert EXCLUSION_REASONS == frozenset({
        "PAGE_HEADER",
        "PAGE_FOOTER",
        "PAGE_NUMBER",
        "REPEATED_CONTINUATION_HEADING",
        "DUPLICATE_SOURCE_ARTIFACT",
        "DOCUMENT_METADATA",
        "OUTSIDE_SELECTED_FILING_SCOPE",
        "EXPLICIT_POLICY_ROUTE",
        "APPROVED_DUPLICATE_ROUTE",
        "UNREADABLE_NEEDS_REVIEW",
    })


# --------------------------------------------------------------------------
# other vocabularies
# --------------------------------------------------------------------------

def test_owner_kinds():
    assert {o.value for o in OwnerKind} == {
        "note", "furniture", "metadata", "unresolved",
    }


def test_generation_lifecycle():
    assert {g.value for g in GenerationStatus} == {
        "building", "active", "superseded", "failed",
    }


def test_content_origin_separates_copied_from_composed():
    values = {c.value for c in ContentOrigin}
    assert {"source_exact", "human_modified", "legacy"} <= values
