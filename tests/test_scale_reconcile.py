"""Pinning tests for the scale-unit poisoning circuit breaker (Plan 1).

Guards `scout/scale_reconcile.py`: an authoritative prior-year conflict coerces
scout's value to "unknown" (re-arming the loud VERIFY prompt), a weak
declared-denomination conflict only flags, and matching / abstaining sources
never manufacture a conflict.
"""

import pytest

from scout.scale_reconcile import reconcile_scale_unit


def test_agreement_is_clean():
    r = reconcile_scale_unit("thousands", "thousands", "thousands")
    assert r.resolved_unit == "thousands"
    assert r.conflict_note is None
    assert r.severity == "ok"


def test_prior_year_conflict_coerces_to_unknown():
    # Scout says millions, but the matched prior-year run filed in thousands —
    # authoritative conflict: remove the poison, re-arm VERIFY.
    r = reconcile_scale_unit("millions", "thousands", "thousands")
    assert r.resolved_unit == "unknown"
    assert r.severity == "coerced"
    assert "prior-year" in r.conflict_note


def test_declared_denomination_conflict_only_flags():
    # No prior-year signal; scout disagrees with the (weak) declared denom —
    # keep scout's value, just flag it.
    r = reconcile_scale_unit("millions", None, "thousands")
    assert r.resolved_unit == "millions"
    assert r.severity == "flag"
    assert "declared denomination" in r.conflict_note


def test_scout_unknown_never_conflicts():
    # Scout abstained — the loud prompt already covers this; never invent a
    # conflict against a prior or declared value.
    r = reconcile_scale_unit("unknown", "thousands", "millions")
    assert r.resolved_unit == "unknown"
    assert r.conflict_note is None
    assert r.severity == "ok"


def test_none_scout_treated_as_unknown():
    r = reconcile_scale_unit(None, "thousands", "thousands")
    assert r.resolved_unit == "unknown"
    assert r.severity == "ok"


def test_prior_unknown_falls_through_to_declared():
    # A prior run with no usable unit is not authoritative; the declared denom
    # still gets a flag-only check.
    r = reconcile_scale_unit("thousands", "unknown", "millions")
    assert r.resolved_unit == "thousands"
    assert r.severity == "flag"


def test_default_denomination_agreement_no_false_alarm():
    # The common case: scout reads "thousands", run defaults to "thousands",
    # no prior. Must be silent — no noisy flag on the default.
    r = reconcile_scale_unit("thousands", None, "thousands")
    assert r.severity == "ok"
    assert r.conflict_note is None


def test_case_insensitive_inputs():
    r = reconcile_scale_unit("Thousands", "THOUSANDS", "thousands")
    assert r.severity == "ok"


@pytest.mark.parametrize("bad", ["", None, "lakhs"])
def test_uncomparable_signals_ignored(bad):
    # Non-vocabulary prior/declared values are not usable signals.
    r = reconcile_scale_unit("thousands", bad, bad)
    assert r.resolved_unit == "thousands"
    assert r.severity == "ok"
