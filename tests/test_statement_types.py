"""Tests for the statement-type registry (Phase 1, Step 1.1)."""
from __future__ import annotations

import pytest

from statement_types import (
    StatementType,
    VARIANTS,
    get_variant,
    template_path,
    variants_for,
)


def test_registry_has_all_variants() -> None:
    """StatementType enum + VARIANTS dict cover the expected 9 templates."""
    # Enum has all 5 statement types
    assert {s.value for s in StatementType} == {"SOFP", "SOPL", "SOCI", "SOCF", "SOCIE"}

    # Expected (statement, variant_name) -> template filename.
    expected = {
        (StatementType.SOFP, "CuNonCu"): "01-SOFP-CuNonCu.xlsx",
        (StatementType.SOFP, "OrderOfLiquidity"): "02-SOFP-OrderOfLiquidity.xlsx",
        (StatementType.SOPL, "Function"): "03-SOPL-Function.xlsx",
        (StatementType.SOPL, "Nature"): "04-SOPL-Nature.xlsx",
        (StatementType.SOCI, "BeforeTax"): "05-SOCI-BeforeTax.xlsx",
        (StatementType.SOCI, "NetOfTax"): "06-SOCI-NetOfTax.xlsx",
        (StatementType.SOCF, "Indirect"): "07-SOCF-Indirect.xlsx",
        (StatementType.SOCF, "Direct"): "08-SOCF-Direct.xlsx",
        (StatementType.SOCIE, "Default"): "09-SOCIE.xlsx",
    }
    assert set(VARIANTS) == set(expected)
    for key, fname in expected.items():
        assert VARIANTS[key].template_filename == fname


def test_template_paths_resolve_to_real_files() -> None:
    """Each registered template file exists on disk."""
    for (statement, variant_name) in VARIANTS:
        p = template_path(statement, variant_name)
        assert p.exists(), f"missing template: {p}"


def test_variants_for_statement() -> None:
    assert {v.name for v in variants_for(StatementType.SOFP)} == {"CuNonCu", "OrderOfLiquidity"}
    assert {v.name for v in variants_for(StatementType.SOCIE)} == {"Default"}


def test_unknown_variant_raises() -> None:
    with pytest.raises(KeyError):
        get_variant(StatementType.SOFP, "NotAVariant")


def test_detection_signals_present() -> None:
    """Every variant has at least one detection signal — scout needs them."""
    for v in VARIANTS.values():
        assert v.detection_signals, f"{v.statement.value}/{v.name} has no detection signals"
