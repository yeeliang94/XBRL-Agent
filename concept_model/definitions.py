"""Runtime loader + search for the official SSM concept-definition index.

Backs the ``lookup_definitions`` agent tool (Plan B,
docs/PLAN-extraction-judgement-improvements.md). The index itself is the
committed JSON produced by ``scripts/generate_concept_definitions.py``; this
module loads it once per standard and answers free-text queries an agent makes
when it is torn between similar template rows.

Design notes:
- **Label-keyed search.** The agent searches by the term it sees on a row
  ("other current non-trade payables"), not by a concept_id it never sees.
- **Stdlib only.** Ranking uses ``difflib`` so we add no dependency. The
  corpus is ~1500 entries per standard, so a linear scan per query is plenty
  fast for interactive tool use.
- **Explicit no-match.** A query that matches nothing returns a clear
  "no concept matched" marker rather than an empty list, so the agent knows
  the lookup ran and found nothing (vs. silently errored).
"""
from __future__ import annotations

import json
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

# normalize_label is the shared "same label" key used across the notes
# pipeline and the index generator — reuse it so queries are normalised the
# same way the index was built.
from notes.labels import normalize_label

_THIS_DIR = Path(__file__).resolve().parent

# Standards we ship an index for. Mirrors the generator's _DOC_LINKBASES keys.
SUPPORTED_STANDARDS = ("mfrs", "mpers")

# Score below which a candidate is considered "not really a match". Tuned so an
# exact/substring label hit always clears it and unrelated noise does not.
_MATCH_THRESHOLD = 0.40

# Per-process cache: {standard -> list[entry]}. The index never changes during
# a run, so load each standard at most once.
_INDEX_CACHE: dict[str, list[dict[str, str]]] = {}


def _index_path(standard: str) -> Path:
    return _THIS_DIR / f"concept_definitions_{standard}.json"


def load_definitions(standard: str) -> list[dict[str, str]]:
    """Load (and cache) the definition index for one filing standard.

    Returns a list of ``{concept_id, label, label_normalized, definition}``.
    Raises ``ValueError`` for an unknown standard and ``FileNotFoundError``
    when the committed index is missing (regenerate via the generator script).
    """
    std = (standard or "").lower()
    if std not in SUPPORTED_STANDARDS:
        raise ValueError(
            f"Unknown filing standard {standard!r}; expected one of {SUPPORTED_STANDARDS}"
        )
    if std in _INDEX_CACHE:
        return _INDEX_CACHE[std]

    path = _index_path(std)
    if not path.exists():
        raise FileNotFoundError(
            f"Concept-definition index missing: {path}. Run "
            f"`python3 scripts/generate_concept_definitions.py` to (re)build it."
        )
    entries = json.loads(path.read_text(encoding="utf-8"))
    _INDEX_CACHE[std] = entries
    return entries


def _tokens(text: str) -> set[str]:
    return {t for t in normalize_label(text).split() if t}


def _score(query_norm: str, query_tokens: set[str], entry: dict[str, str]) -> float:
    """Rank one index entry against a normalised query in [0, 1].

    Priority, high to low:
    1. Exact normalised-label equality.
    2. One label is a substring of the other (the common "agent typed a near
       variant of the row label" case).
    3. Token overlap on the label + fuzzy ratio.
    4. A weak boost when the query's words appear in the definition prose, so
       a conceptual query ("money owed to suppliers") can still surface
       "trade payables" even when the labels don't overlap.
    """
    label_norm = entry.get("label_normalized") or normalize_label(entry.get("label", ""))
    if not label_norm:
        return 0.0

    if query_norm == label_norm:
        return 1.0

    if query_norm in label_norm or label_norm in query_norm:
        # Scale by how much of the longer string is shared so a tiny query
        # inside a long label doesn't outrank a near-exact match.
        longer = max(len(query_norm), len(label_norm))
        shorter = min(len(query_norm), len(label_norm))
        return 0.80 + 0.15 * (shorter / longer)

    label_tokens = _tokens(label_norm)
    overlap = (
        len(query_tokens & label_tokens) / len(query_tokens) if query_tokens else 0.0
    )
    fuzzy = SequenceMatcher(None, query_norm, label_norm).ratio()
    label_score = max(overlap * 0.75, fuzzy * 0.65)

    # Definition-prose boost (capped low so it can refine ranking but never
    # outrank a genuine label hit).
    definition = (entry.get("definition") or "").lower()
    if query_tokens:
        def_hits = sum(1 for t in query_tokens if t in definition)
        def_boost = 0.20 * (def_hits / len(query_tokens))
    else:
        def_boost = 0.0

    return min(1.0, label_score + def_boost)


def search(
    queries: list[str],
    standard: str,
    top_k: int = 5,
) -> dict[str, dict[str, Any]]:
    """Look up one or more terms in the definition index for ``standard``.

    Returns ``{original_query -> result}`` where each result is either::

        {"matches": [{concept_id, label, definition, score}, ...],
         "truncated": bool}

    or, when nothing clears the match threshold::

        {"matches": [], "no_match": "no concept matched '<q>' in MFRS"}

    ``top_k`` caps matches per query; ``truncated`` flags when more candidates
    cleared the threshold than were returned (no silent capping).
    """
    entries = load_definitions(standard)
    std_upper = standard.upper()
    results: dict[str, dict[str, Any]] = {}

    for raw_query in queries:
        query = (raw_query or "").strip()
        if not query:
            results[raw_query] = {
                "matches": [],
                "no_match": "empty query — provide a concept label or term to look up",
            }
            continue

        query_norm = normalize_label(query)
        query_tokens = _tokens(query)

        scored: list[tuple[float, dict[str, str]]] = []
        for entry in entries:
            s = _score(query_norm, query_tokens, entry)
            if s >= _MATCH_THRESHOLD:
                scored.append((s, entry))

        scored.sort(key=lambda pair: pair[0], reverse=True)

        if not scored:
            results[raw_query] = {
                "matches": [],
                "no_match": f"no concept matched '{query}' in {std_upper}",
            }
            continue

        top = scored[:top_k]
        results[raw_query] = {
            "matches": [
                {
                    "concept_id": entry["concept_id"],
                    "label": entry["label"],
                    "definition": entry["definition"],
                    "score": round(score, 3),
                }
                for score, entry in top
            ],
            "truncated": len(scored) > top_k,
        }

    return results


def lookup_as_json(queries: list[str], standard: str) -> str:
    """Agent-tool wrapper: run ``search`` and return a JSON string.

    Shared by the extraction, reviewer, and notes agents so the three tool
    registrations stay identical. Tolerant of bad input — a missing index or
    unknown standard returns a structured ``error`` payload rather than
    raising into the agent loop (which would surface as an opaque tool crash).
    """
    if not isinstance(queries, list) or not queries:
        return json.dumps(
            {"error": "Pass a non-empty list of terms, e.g. ['accruals', 'deferred income']."}
        )
    try:
        results = search(queries, standard)
    except FileNotFoundError as exc:
        return json.dumps({"error": f"Definition index unavailable: {exc}"})
    except ValueError as exc:
        return json.dumps({"error": str(exc)})
    return json.dumps({"standard": standard.lower(), "results": results}, ensure_ascii=False)


__all__ = ["load_definitions", "search", "lookup_as_json", "SUPPORTED_STANDARDS"]
