"""Unit class per concept — the missing dimension behind finding 2.

``run_concept_facts`` stores a bare number. Nothing on the fact or on
``concept_nodes`` says whether that number is ringgit, a count of shares, or a
percentage — the concept-model parser mints UUIDs from
``(template_id, sheet, row, label)`` and drops the XBRL concept id.

That gap is why the mTool exporter's original single filing-wide ``scale``
multiplier was unsafe. MFRS sheet 13 puts "Number of shares issued and fully
paid" three rows above "Amount of shares issued and fully paid": a blanket
thousands conversion multiplies the share COUNT by 1,000 and files it.

This module restores the dimension by reading it from the authority. Every XBRL
element in ``SSMxT_2022v1.0/`` declares its item type; the committed index
(``concept_model/concept_units_{standard}.json``, built by
``scripts/generate_concept_units.py``) maps normalised label -> unit class. The
label is the join key because it is the only thing a template row and a
taxonomy concept still have in common at runtime.

Four classes matter to the translation layer:

* ``monetary``  — a money amount; the ONLY class a denomination scale may touch
* ``shares``    — a count of shares
* ``per_share`` — money per share (EPS/DPS); already per-unit
* ``pure``      — percentages, ratios, plain counts

plus ``non_numeric`` for text/date/domain concepts, and ``None`` for a label
the taxonomy doesn't recognise. **Unknown is reported, never guessed**: the
translation layer refuses to apply a non-identity rule to a value whose class
it doesn't know (see ``mtool/translation.py``).
"""
from __future__ import annotations

import json
from pathlib import Path

from notes.labels import normalize_label

_THIS_DIR = Path(__file__).resolve().parent
_INDEX_DIR = _THIS_DIR.parent / "concept_model"

SUPPORTED_STANDARDS = ("mfrs", "mpers")

MONETARY = "monetary"
SHARES = "shares"
PER_SHARE = "per_share"
PURE = "pure"
NON_NUMERIC = "non_numeric"
AMBIGUOUS = "ambiguous"

# Classes that can legitimately carry a figure the exporter writes.
NUMERIC_CLASSES = (MONETARY, SHARES, PER_SHARE, PURE)

_CACHE: dict[str, dict[str, str]] = {}


def load_unit_index(standard: str) -> dict[str, str]:
    """Load (and cache) the ``{normalised label -> unit class}`` index.

    A missing index file is NOT fatal: it degrades to "every label is unknown",
    which the identity translation passes through unchanged and any other
    translation refuses. That is the safe direction — a deployment that forgot
    to ship the index cannot silently mis-scale a filing.
    """
    std = (standard or "").lower()
    if std not in SUPPORTED_STANDARDS:
        return {}
    if std in _CACHE:
        return _CACHE[std]
    path = _INDEX_DIR / f"concept_units_{std}.json"
    try:
        index = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        index = {}
    _CACHE[std] = index
    return index


def unit_class_for_label(label: str, standard: str) -> str | None:
    """The unit class for a template row label, or ``None`` when unknown.

    ``None`` covers three cases that are all "we don't know": no entry, an
    ``ambiguous`` entry (one label, two different numeric classes in the
    taxonomy), and an unrecognised standard. Callers must treat them alike.
    """
    key = normalize_label(label or "")
    if not key:
        return None
    found = load_unit_index(standard).get(key)
    if found in (None, AMBIGUOUS):
        return None
    return found


__all__ = [
    "load_unit_index", "unit_class_for_label", "SUPPORTED_STANDARDS",
    "MONETARY", "SHARES", "PER_SHARE", "PURE", "NON_NUMERIC", "AMBIGUOUS",
    "NUMERIC_CLASSES",
]
