"""Per-entity advisory memory (item 28 of PLAN-orchestration-hardening).

Re-processing the same entity's next-year filing starts from zero: variant,
scale unit, and page offset are re-discovered every time, with fresh chances for
error on facts that rarely change year-over-year. This module matches the
scout's observed ``entity_name`` against prior *completed* runs and, when it
finds one, builds a small **advisory** that downstream prompts render — wrapped
in loud "(prior-year run — VERIFY against THIS PDF)" framing.

Design constraints (from the plan):

- **Strictly advisory.** Hints inform agent judgement; they never bypass it,
  never restrict pages (gotcha #13), and never enter the notes *writing* path as
  a deterministic match (gotcha #14). Every field is framed "verify against THIS
  PDF" because entity-name collisions and year-over-year changes are real.
- **No new tables (v1).** Matching reads existing ``runs`` rows plus the
  on-disk ``infopack.json`` each run persists in its output dir.
- **Opt-outable.** Gated by ``XBRL_ENTITY_MEMORY`` (default on), mirroring
  ``server._auto_review_enabled``'s resolver semantics.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# Filename the run's infopack is persisted under, inside the run's output dir.
INFOPACK_FILENAME = "infopack.json"

# Corporate-form suffixes stripped before comparing entity names. Two filings of
# the same company can spell the legal form differently ("Bhd" vs "Berhad"); the
# match is advisory so a conservative strip is safe — a wrong match still lands
# behind "VERIFY against THIS PDF".
_SUFFIX_WORDS = {
    "berhad", "bhd", "sdn", "sendirian", "ltd", "limited", "plc", "llp",
    "inc", "incorporated", "co", "company", "group", "holdings", "holding",
}


def entity_memory_enabled() -> bool:
    """True unless ``XBRL_ENTITY_MEMORY`` is explicitly set to a false-y value."""
    return os.environ.get("XBRL_ENTITY_MEMORY", "true").lower() == "true"


def normalize_entity_name(name: Optional[str]) -> str:
    """Normalise an entity name for matching: lowercase, strip punctuation and
    common corporate-form suffix words, collapse whitespace.

    Returns "" for empty / None input — an empty key never matches (the caller
    treats "" as "no entity observed, skip matching").

    Code-review fix (2026-06-13): suffix-stripping must leave >= 2 surviving
    tokens. "ABC Holdings Berhad" and an unrelated "ABC Sdn Bhd" both used to
    collapse to "abc" and cross-pollinate advisories between distinct
    entities. When stripping would leave fewer than 2 tokens, the suffix
    words are KEPT (so only same-spelling names match); a name made entirely
    of suffix words still normalises to "" (never a match key).
    """
    if not name:
        return ""
    # Lowercase, drop anything that isn't a letter/digit/space.
    cleaned = re.sub(r"[^a-z0-9\s]", " ", name.lower())
    all_tokens = [t for t in cleaned.split() if t]
    tokens = [t for t in all_tokens if t not in _SUFFIX_WORDS]
    if not tokens:
        return ""  # all suffix words — never a usable match key
    if len(tokens) < 2:
        # Stripping would leave a single (often generic) token — skip the
        # strip so distinct entities can't collide on it.
        return " ".join(all_tokens)
    return " ".join(tokens)


@dataclass
class PriorYearAdvisory:
    """A matched prior run's reusable, slowly-changing observations."""

    prior_run_id: int
    pdf_filename: str
    created_at: str
    entity_name: str
    filing_standard: Optional[str] = None
    filing_level: Optional[str] = None
    scale_unit: Optional[str] = None
    page_offset: Optional[int] = None
    # statement value (e.g. "SOFP") -> prior variant suggestion.
    variants: dict[str, str] = field(default_factory=dict)

    def to_prompt_dict(self, statement_value: Optional[str]) -> dict[str, Any]:
        """The per-statement payload the prompt renderer consumes.

        ``statement_value`` selects this statement's prior variant; pass None
        (notes path) to omit the per-statement variant line.
        """
        return {
            "prior_run_id": self.prior_run_id,
            "pdf_filename": self.pdf_filename,
            "entity_name": self.entity_name,
            "filing_standard": self.filing_standard,
            "filing_level": self.filing_level,
            "scale_unit": self.scale_unit,
            "page_offset": self.page_offset,
            "variant": self.variants.get(statement_value) if statement_value else None,
        }


def persist_infopack(output_dir: str | Path, infopack: Any) -> Optional[Path]:
    """Write the run's infopack to ``{output_dir}/infopack.json`` for future
    matching. Best-effort — a write failure is logged and swallowed (the run
    proceeds; only future entity matches lose this run as a candidate).
    """
    if not output_dir or infopack is None:
        return None
    try:
        path = Path(output_dir) / INFOPACK_FILENAME
        path.write_text(infopack.to_json(), encoding="utf-8")
        return path
    except Exception:  # noqa: BLE001 — advisory persistence, never fatal
        logger.warning("Failed to persist infopack.json to %s", output_dir, exc_info=True)
        return None


def _load_infopack_from_run(run: Any) -> Optional[Any]:
    """Default loader: read a prior run's persisted infopack.

    Tries ``{output_dir}/infopack.json`` first (item 28's persisted artifact),
    then falls back to an ``infopack`` blob embedded in the run's config
    (older runs from before persistence existed). Returns None when neither is
    available or parseable — a missing pack just means that run can't be a
    candidate.
    """
    from scout.infopack import Infopack

    output_dir = getattr(run, "output_dir", "") or ""
    if output_dir:
        path = Path(output_dir) / INFOPACK_FILENAME
        if path.exists():
            try:
                return Infopack.from_json(path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001 — defensive; skip a bad pack
                logger.debug("Unparseable infopack.json for run %s", getattr(run, "id", "?"))

    config = getattr(run, "config", None) or {}
    embedded = config.get("infopack") if isinstance(config, dict) else None
    if embedded:
        import json

        try:
            return Infopack.from_json(json.dumps(embedded))
        except Exception:  # noqa: BLE001
            return None
    return None


def _is_registered_variant(statement_value: str, variant_name: str) -> bool:
    """True when ``variant_name`` is in the closed set of registered variants
    for the statement (statement_types.VARIANTS).

    ``variant_suggestion`` is free-form scout-LLM output that gets rendered
    verbatim into FUTURE runs' prompts via the prior-year advisory block — a
    cross-run prompt-injection channel unless laundered against the closed
    vocabulary here (code-review fix, 2026-06-13). Unknown statements and
    unknown variant names are both rejected.
    """
    try:
        from statement_types import StatementType, variants_for

        stmt = StatementType(statement_value)
        return variant_name in {v.name for v in variants_for(stmt)}
    except Exception:  # noqa: BLE001 — unknown statement value → reject
        return False


def _advisory_from_run(run: Any, infopack: Any) -> PriorYearAdvisory:
    """Build the advisory from a prior run row + its loaded infopack."""
    config = getattr(run, "config", None) or {}
    if not isinstance(config, dict):
        config = {}
    filing_standard = config.get("filing_standard") or (
        infopack.detected_standard
        if getattr(infopack, "detected_standard", "unknown") != "unknown"
        else None
    )
    variants: dict[str, str] = {}
    for stmt, ref in getattr(infopack, "statements", {}).items():
        stmt_value = stmt.value if hasattr(stmt, "value") else str(stmt)
        suggestion = getattr(ref, "variant_suggestion", None)
        if not suggestion:
            continue
        # Closed-vocabulary gate: a prior infopack's variant_suggestion is
        # rendered into future prompts — drop anything that isn't a
        # registered variant for that statement (prompt-injection laundering).
        if not _is_registered_variant(stmt_value, str(suggestion)):
            logger.warning(
                "Dropping unregistered prior-year variant_suggestion %r for "
                "%s (run %s)", suggestion, stmt_value, getattr(run, "id", "?"),
            )
            continue
        variants[stmt_value] = str(suggestion)
    return PriorYearAdvisory(
        prior_run_id=int(getattr(run, "id")),
        pdf_filename=getattr(run, "pdf_filename", "") or "",
        created_at=getattr(run, "created_at", "") or "",
        entity_name=infopack.entity_name or "",
        filing_standard=filing_standard,
        filing_level=config.get("filing_level"),
        scale_unit=(
            infopack.scale_unit
            if getattr(infopack, "scale_unit", "unknown") != "unknown"
            else None
        ),
        page_offset=getattr(infopack, "page_offset", None),
        variants=variants,
    )


@dataclass
class _RunRow:
    """Minimal prior-run view for matching — what the loaders/matcher read."""

    id: int
    pdf_filename: str
    created_at: str
    output_dir: str
    config: Optional[dict]


def fetch_prior_runs(conn: Any, *, exclude_run_id: Optional[int] = None, limit: int = 200) -> list[_RunRow]:
    """Read terminal/complete prior runs, most-recent-first, for entity matching.

    Reads only existing ``runs`` columns (no new table — plan constraint). A run
    qualifies as a candidate when it reached a completed terminal status; drafts,
    running, failed, and aborted (partial) runs are excluded so a half-done run
    never seeds a misleading prior-year hint.
    """
    import json

    rows = conn.execute(
        "SELECT id, pdf_filename, created_at, output_dir, run_config_json "
        "FROM runs WHERE status IN ('completed', 'completed_with_errors') "
        "ORDER BY datetime(created_at) DESC, id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    out: list[_RunRow] = []
    for row in rows:
        rid = int(row[0])
        if exclude_run_id is not None and rid == exclude_run_id:
            continue
        config = None
        if row[4]:
            try:
                config = json.loads(row[4])
            except Exception:  # noqa: BLE001 — a bad config blob just means no config hints
                config = None
        out.append(
            _RunRow(
                id=rid,
                pdf_filename=row[1] or "",
                created_at=row[2] or "",
                output_dir=row[3] or "",
                config=config,
            )
        )
    return out


def find_prior_year_match(
    prior_runs: list[Any],
    *,
    entity_name: Optional[str],
    exclude_run_id: Optional[int] = None,
    loader: Callable[[Any], Optional[Any]] = _load_infopack_from_run,
) -> Optional[PriorYearAdvisory]:
    """Find the most recent prior run whose entity name matches.

    ``prior_runs`` is a list of run rows (anything with ``.id``, ``.output_dir``,
    ``.config``, ``.pdf_filename``, ``.created_at``), expected pre-filtered to
    terminal/completed runs and ordered most-recent-first by the caller. The
    first candidate whose normalised entity name equals the current run's wins.

    The ``loader`` is injectable so tests don't need files on disk.
    """
    target = normalize_entity_name(entity_name)
    if not target:
        return None  # no entity observed → nothing to match on

    for run in prior_runs:
        if exclude_run_id is not None and getattr(run, "id", None) == exclude_run_id:
            continue
        infopack = loader(run)
        if infopack is None or not getattr(infopack, "entity_name", None):
            continue
        if normalize_entity_name(infopack.entity_name) == target:
            return _advisory_from_run(run, infopack)
    return None
