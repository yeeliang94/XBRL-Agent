"""Shared label-normalisation primitives for the notes pipeline.

`notes.writer` and `notes.coverage` both compare template/payload
labels, and their comparators MUST agree on what "the same label"
means — if they drift, the coverage validator can reject a claim for
a label the writer happily accepted. This module owns the canonical
strip rules so both call sites import from one source of truth.

The one knob worth explaining: taxonomy type suffixes like
`[text block]`, `[abstract]`, `[axis]`, `[member]`, `[table]`. These
appear on MPERS template rows (because the MPERS bundle uses SSM
ReportingLabel, which includes the type) but NOT on MFRS rows
(generated from XBRL 2003 StandardLabel, bare concept names). Agents
tend to emit the bare form regardless of standard — that's the root
cause of the run-#105 silent drop. Stripping the suffix before
comparison makes the match taxonomy-style-independent.
"""
from __future__ import annotations

import re

# SSM ReportingLabel type suffixes appended to MPERS template labels.
# Extend this tuple if a future MPERS generator run surfaces a new
# type bracket — both the writer and the coverage validator will
# pick it up because they import from the same constant.
_TAXONOMY_SUFFIXES: tuple[str, ...] = (
    "[text block]",
    "[abstract]",
    "[axis]",
    "[member]",
    "[table]",
)

# Pre-compiled match: one trailing type-suffix (with any surrounding
# whitespace) stripped from the end. Case-insensitive so "[Text Block]"
# gets normalised the same way. Anchored with ``\s*$`` so mid-string
# brackets (e.g. "EBITDA [reconciliation shown below]") are left alone —
# we only care about terminal taxonomy annotations.
_SUFFIX_RE = re.compile(
    r"\s*(?:" + "|".join(re.escape(s) for s in _TAXONOMY_SUFFIXES) + r")\s*$",
    re.IGNORECASE,
)


def normalize_label(s: str) -> str:
    """Canonical label-comparison key.

    Rules (applied in order):
    1. Strip surrounding whitespace.
    2. Strip a leading `*` marker (our template convention for required
       rows) and re-strip whitespace.
    3. Lower-case.
    4. Strip a single trailing taxonomy type suffix
       (`[text block]` / `[abstract]` / etc.). Only one pass — a label
       with two bracketed suffixes is data we haven't seen and the
       second pass would be speculative.

    The function is idempotent: `normalize_label(normalize_label(x)) ==
    normalize_label(x)` for any input. Callers can pre-normalise
    without corrupting values.
    """
    base = s.strip().lstrip("*").strip().lower()
    # Strip one trailing taxonomy suffix if present. The regex already
    # handles the optional leading whitespace.
    return _SUFFIX_RE.sub("", base)


__all__ = ["normalize_label"]
