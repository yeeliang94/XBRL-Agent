"""Central registry of the 5 MBRS statement types and their variants.

Every downstream component (scout, extraction sub-agents, verifier, cross-checks)
resolves template paths and variant signals through this registry rather than
hard-coding strings. Adding or renaming a template only touches this file.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class StatementType(str, Enum):
    """The 5 primary MBRS face statements."""
    SOFP = "SOFP"   # Statement of Financial Position
    SOPL = "SOPL"   # Statement of Profit or Loss
    SOCI = "SOCI"   # Statement of Comprehensive Income
    SOCF = "SOCF"   # Statement of Cash Flows
    SOCIE = "SOCIE" # Statement of Changes in Equity


@dataclass(frozen=True)
class Variant:
    """A specific presentation of a statement.

    Each variant maps 1:1 to an MBRS template file. Some statements (SOCIE) only
    have a single variant; others (SOFP, SOPL, SOCI, SOCF) ship two.
    """
    statement: StatementType
    name: str                # e.g. "CuNonCu", "Function", "Indirect"
    template_filename: str   # file under XBRL-template-MFRS/
    # Section-header phrases the scout can look for on the face page to confirm
    # the variant. Lowercase, substring match.
    detection_signals: tuple[str, ...] = field(default_factory=tuple)


# Absolute path to the template directory — resolved once so callers don't
# need to know about working-directory quirks.
TEMPLATE_DIR = Path(__file__).resolve().parent / "XBRL-template-MFRS"


# Starter registry. Detection signals are the minimal evidence a human would
# look for on the printed statement to pick the variant. The scout expands
# these in Phase 3.
VARIANTS: dict[tuple[StatementType, str], Variant] = {
    (StatementType.SOFP, "CuNonCu"): Variant(
        statement=StatementType.SOFP,
        name="CuNonCu",
        template_filename="01-SOFP-CuNonCu.xlsx",
        detection_signals=("non-current assets", "current assets", "non-current liabilities"),
    ),
    (StatementType.SOFP, "OrderOfLiquidity"): Variant(
        statement=StatementType.SOFP,
        name="OrderOfLiquidity",
        template_filename="02-SOFP-OrderOfLiquidity.xlsx",
        # No current/non-current split; assets are presented in order of liquidity.
        # Positive signals include the explicit phrase plus common patterns seen
        # on liquidity-order sheets. Absence-based and negative-signal logic in
        # variant_detector.py handles the case where these don't appear explicitly.
        detection_signals=(
            "order of liquidity", "by liquidity",
            "total assets", "total liabilities",
            "deposits from customers", "loans and advances",
        ),
    ),
    (StatementType.SOPL, "Function"): Variant(
        statement=StatementType.SOPL,
        name="Function",
        template_filename="03-SOPL-Function.xlsx",
        detection_signals=("cost of sales", "distribution", "administrative expenses"),
    ),
    (StatementType.SOPL, "Nature"): Variant(
        statement=StatementType.SOPL,
        name="Nature",
        template_filename="04-SOPL-Nature.xlsx",
        detection_signals=("changes in inventories", "raw materials", "employee benefits expense"),
    ),
    (StatementType.SOCI, "BeforeTax"): Variant(
        statement=StatementType.SOCI,
        name="BeforeTax",
        template_filename="05-SOCI-BeforeTax.xlsx",
        detection_signals=("before tax", "income tax relating to"),
    ),
    (StatementType.SOCI, "NetOfTax"): Variant(
        statement=StatementType.SOCI,
        name="NetOfTax",
        template_filename="06-SOCI-NetOfTax.xlsx",
        detection_signals=("net of tax",),
    ),
    # NotPrepared: no standalone SOCI page. This happens when the company
    # uses a combined SOPL+OCI statement, has zero OCI items, or the MBRS
    # scoping chose "Not prepared". No template — coordinator skips extraction.
    (StatementType.SOCI, "NotPrepared"): Variant(
        statement=StatementType.SOCI,
        name="NotPrepared",
        template_filename="",  # no template — extraction is skipped
        detection_signals=(),   # never auto-detected by signal matching
    ),
    (StatementType.SOCF, "Indirect"): Variant(
        statement=StatementType.SOCF,
        name="Indirect",
        template_filename="07-SOCF-Indirect.xlsx",
        # Indirect method starts from profit before tax and adjusts for non-cash items.
        detection_signals=("profit before tax", "adjustments for", "depreciation"),
    ),
    (StatementType.SOCF, "Direct"): Variant(
        statement=StatementType.SOCF,
        name="Direct",
        template_filename="08-SOCF-Direct.xlsx",
        # Direct method shows gross cash receipts/payments.
        detection_signals=("cash receipts from customers", "cash paid to suppliers"),
    ),
    (StatementType.SOCIE, "Default"): Variant(
        statement=StatementType.SOCIE,
        name="Default",
        template_filename="09-SOCIE.xlsx",
        detection_signals=("share capital", "retained earnings", "total equity"),
    ),
}


def get_variant(statement: StatementType, variant_name: str) -> Variant:
    """Look up a variant by (statement, variant_name). Raises if unknown."""
    try:
        return VARIANTS[(statement, variant_name)]
    except KeyError as exc:
        known = sorted(f"{s.value}/{n}" for s, n in VARIANTS)
        raise KeyError(
            f"No variant registered for {statement.value}/{variant_name}. "
            f"Known: {known}"
        ) from exc


def template_path(statement: StatementType, variant_name: str) -> Path:
    """Absolute filesystem path to the template for a given (statement, variant).

    Raises ValueError for meta-variants like NotPrepared that have no template.
    """
    v = get_variant(statement, variant_name)
    if not v.template_filename:
        raise ValueError(
            f"{statement.value}/{variant_name} has no template — "
            f"extraction should be skipped for this variant"
        )
    return TEMPLATE_DIR / v.template_filename


def variants_for(statement: StatementType) -> list[Variant]:
    """All registered variants for a statement type, in insertion order."""
    return [v for (s, _), v in VARIANTS.items() if s == statement]
