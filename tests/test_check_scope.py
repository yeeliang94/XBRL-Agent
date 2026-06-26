"""Shared cross-check scoping helper (docs/PLAN.md Step 4).

``resolve_check_scope`` is the single source of truth for mapping a run's
``(statement_type, variant)`` pairs to their cross-check scope (template_ids +
statements_to_run + variants). It replaces the loop that was duplicated between
``server._build_check_template_ids`` and the reviewer's
``run_verification_checks`` (gotcha #21).
"""
from __future__ import annotations

from cross_checks.framework import CheckScope, resolve_check_scope
from statement_types import StatementType


def test_resolves_a_valid_pair():
    scope = resolve_check_scope(
        [(StatementType.SOFP, "CuNonCu")],
        filing_level="company", filing_standard="mfrs",
    )
    assert scope.statements_to_run == {StatementType.SOFP}
    assert scope.variants == {StatementType.SOFP: "CuNonCu"}
    assert isinstance(scope.template_ids[StatementType.SOFP], str)
    assert scope.template_ids[StatementType.SOFP]      # non-empty id


def test_enum_and_string_statement_type_resolve_identically():
    # Call sites pass both shapes (in-memory enum vs DB/value string).
    enum_scope = resolve_check_scope([(StatementType.SOFP, "CuNonCu")])
    str_scope = resolve_check_scope([("SOFP", "CuNonCu")])
    assert enum_scope.statements_to_run == str_scope.statements_to_run
    assert enum_scope.template_ids == str_scope.template_ids
    assert enum_scope.variants == str_scope.variants


def test_pseudo_agent_row_is_skipped_not_raised():
    # CORRECTION / notes-validator / scout rows don't map to a StatementType.
    scope = resolve_check_scope([("CORRECTION", None), ("scout", None)])
    assert scope == CheckScope()           # empty, no crash


def test_unresolvable_variant_is_skipped():
    # An unregistered variant can't resolve a template_path → skipped, like the
    # pipeline's own (ValueError, KeyError) swallow, not a crash.
    scope = resolve_check_scope([("SOFP", "NoSuchVariant")])
    assert scope.statements_to_run == set()
    assert scope.template_ids == {}


def test_mix_keeps_valid_drops_invalid_and_outputs_stay_consistent():
    scope = resolve_check_scope(
        [("SOFP", "CuNonCu"), ("CORRECTION", None), ("SOPL", "Nature")],
    )
    # Only the two real statements survive; the three dicts/sets agree on keys.
    assert StatementType.SOFP in scope.statements_to_run
    assert StatementType.SOPL in scope.statements_to_run
    assert set(scope.template_ids) == scope.statements_to_run
    assert set(scope.variants) == scope.statements_to_run


def test_empty_input_yields_empty_scope():
    assert resolve_check_scope([]) == CheckScope()
