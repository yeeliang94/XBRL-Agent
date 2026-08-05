"""Unit- and sign-aware value translation for the mTool fill (Step 6).

The exporter used to take ``scale: float = 1.0`` and multiply every fact by it.
Peer-review finding 2: that has no unit dimension, so a thousands filing would
scale a share count as readily as a money amount. This module replaces it.

A **translation manifest** says what to do to a value on its way from our facts
into an mTool cell, per unit class, with room for evidence-backed per-concept
exceptions. It is versioned, and the version is stamped into the fill doc's
``meta`` and onto the fill receipt, so any workbook can be traced back to the
rules that produced it.

Two rules govern the design:

* **Identity is the shipped default.** ``IDENTITY`` applies no scale and no
  sign flip. That is not timidity — the Windows recon has not yet confirmed
  whether mTool stores the full unscaled figure or the on-sheet thousands one
  (docs/MTOOL-ZIP-RECON-BRIEF.md Task 3.6), and a wrong answer silently
  1000×-inflates an entire filing. Emitting exactly what we hold is the only
  claim we can support today.
* **A non-identity manifest fails loudly on the unknown.** If a rule would
  change a value and we don't know that value's unit class, we raise. Never a
  guess, and never a silent identity pass-through dressed up as a translation —
  that would be indistinguishable from a correct scaling right up until the
  filing was wrong.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from mtool.units import NUMERIC_CLASSES


class UnknownUnitClass(Exception):
    """A non-identity rule met a value whose unit class we can't establish.

    Carries the label so the report can name the row rather than the concept
    uuid the operator has never seen.
    """

    def __init__(self, label: str, sheet: str | None = None) -> None:
        where = f" on {sheet}" if sheet else ""
        super().__init__(
            f"Cannot translate {label!r}{where}: this row's unit (money, "
            "shares, per-share, …) is not in the SSM taxonomy index, so we "
            "cannot tell whether the scale applies to it. Refusing to guess.")
        self.label = label
        self.sheet = sheet


@dataclass(frozen=True)
class UnitRule:
    """What to do to a value of one unit class. ``sign`` is +1 or -1."""

    scale: float = 1.0
    sign: int = 1

    @property
    def is_identity(self) -> bool:
        return self.scale == 1.0 and self.sign == 1

    def apply(self, value: float) -> float:
        return value * self.scale * self.sign


@dataclass(frozen=True)
class ConceptOverride:
    """An evidence-backed exception for one row of one template.

    The slot exists because sign conventions in this codebase are per-concept
    facts, not per-class ones (ADR-002's SOCIE dividends are stored positive
    because that template's subtotal formula subtracts the row). It ships
    EMPTY: an override without evidence is exactly the guess this whole layer
    was built to avoid.
    """

    template_id: str
    label_normalized: str
    rule: UnitRule
    evidence: str


@dataclass(frozen=True)
class TranslationManifest:
    """A versioned set of translation rules keyed by unit class."""

    version: str
    units: dict[str, UnitRule] = field(default_factory=dict)
    overrides: tuple[ConceptOverride, ...] = ()
    description: str = ""

    @property
    def is_identity(self) -> bool:
        """True when nothing this manifest can do would change a value."""
        return (all(r.is_identity for r in self.units.values())
                and all(o.rule.is_identity for o in self.overrides))

    def rule_for(self, unit_class: str | None, *, template_id: str = "",
                 label_normalized: str = "") -> UnitRule | None:
        """The rule that applies, or ``None`` when nothing does."""
        for o in self.overrides:
            if (o.template_id == template_id
                    and o.label_normalized == label_normalized):
                return o.rule
        if unit_class is None:
            return None
        return self.units.get(unit_class)

    def translate(
        self,
        value: float,
        *,
        unit_class: str | None,
        label: str,
        sheet: str | None = None,
        template_id: str = "",
        label_normalized: str = "",
    ) -> float:
        """Translate one value, or raise :class:`UnknownUnitClass`.

        The identity manifest short-circuits: with no rule able to change
        anything, an unknown unit class is not a risk, so a taxonomy gap never
        blocks today's verbatim fill.
        """
        if self.is_identity:
            return value
        rule = self.rule_for(unit_class, template_id=template_id,
                             label_normalized=label_normalized)
        if rule is None:
            raise UnknownUnitClass(label, sheet)
        return rule.apply(value)

    def to_json(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "description": self.description,
            "units": {k: {"scale": r.scale, "sign": r.sign}
                      for k, r in sorted(self.units.items())},
            "overrides": [
                {"template_id": o.template_id,
                 "label_normalized": o.label_normalized,
                 "scale": o.rule.scale, "sign": o.rule.sign,
                 "evidence": o.evidence}
                for o in self.overrides],
        }


#: The shipped default — emit exactly the value we hold.
IDENTITY = TranslationManifest(
    version="identity-1",
    units={cls: UnitRule() for cls in NUMERIC_CLASSES},
    description=(
        "No scale, no sign change: the value written to mTool is the value in "
        "run_concept_facts. Stays the default until a Windows acceptance run "
        "confirms which unit mTool stores (plan Steps 1 and 7)."),
)


def thousands_manifest(version: str = "thousands-1") -> TranslationManifest:
    """A worked example of a NON-identity manifest — money ×1000, nothing else.

    Not wired to anything. It exists so the unit-awareness is demonstrable and
    testable *before* the recon answer arrives: it proves that a thousands
    conversion scales a monetary fact, leaves a share count alone, and refuses
    a row whose unit class is unknown. When the recon says mTool wants the
    unscaled figure, this becomes the shipped manifest; if it says otherwise,
    it is deleted and nothing else changes.
    """
    return TranslationManifest(
        version=version,
        units={
            "monetary": UnitRule(scale=1000.0),
            "shares": UnitRule(),
            "per_share": UnitRule(),
            "pure": UnitRule(),
        },
        description=("Facts held in thousands, mTool wants full units: money "
                     "amounts ×1000; share counts, per-share amounts and "
                     "ratios untouched."),
    )


_REGISTRY = {
    IDENTITY.version: IDENTITY,
    "thousands-1": thousands_manifest(),
}


def manifest_by_version(version: str | None) -> TranslationManifest:
    """Look up a manifest by version string; unknown/None -> identity."""
    if not version:
        return IDENTITY
    return _REGISTRY.get(version, IDENTITY)


__all__ = ["TranslationManifest", "UnitRule", "ConceptOverride",
           "UnknownUnitClass", "IDENTITY", "thousands_manifest",
           "manifest_by_version"]
