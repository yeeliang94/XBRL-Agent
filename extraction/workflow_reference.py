"""On-demand workflow-reference loader (skill-first harness, Phase 1).

The ~53 KB of per-statement fill know-how in ``docs/workflows/*.md`` used to be
developer-only documentation the extraction agents never saw. This module
promotes the *canonicalized* copies under ``prompts/references/`` into an
agent-loadable reference shelf, following the progressive-disclosure pattern
(``docs/PROPOSAL-skill-first-harness.md``):

  Discovery   → the statement prompt names the tool and when to call it.
  Activation  → the agent calls ``load_workflow_reference()`` once when it
                starts a statement that needs depth (SOCIE / SOCF).
  Execution   → the agent follows the reference, confirming live coordinates
                against ``read_template()``.

Design constraints (peer-review-hardened, see the proposal §8 Phase 1):

  * **No model-supplied path.** The agent passes nothing; the file is resolved
    from a STATIC map keyed by the run's ``ExtractionDeps``
    (statement_type / variant / filing_standard), mirroring how
    ``cell_resolver`` scopes by ``template_id`` and how ``render_prompt``
    chooses a prompt file. This is never a general file-read primitive.
  * **Precedence mirrors ``render_prompt``** — variant tier → standard tier →
    generic tier. Only the variant tier is populated today (the 9 workflow
    docs are per-variant); the resolver still walks the ladder so a future
    standard/generic reference drops in without code changes.
  * **Output is size-capped** so a pathological file can't blow the context.
  * **Cached process-globally** (like ``_render_template_summary``) and
    **deduped per-turn** by ``strip_duplicate_workflow_reference`` so a
    reloaded reference is billed once, not every turn.

The activation gate (``workflow_reference_gate_error``) is a pure helper so it
is unit-testable without constructing an Agent; ``extraction/agent.py`` calls
it from ``write_facts``.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

from statement_types import StatementType

logger = logging.getLogger(__name__)

# The canonicalized reference shelf lives alongside the prompts (proposal §11
# decision 4) — keep all agent-facing text in one tree.
REFERENCE_DIR = Path(__file__).resolve().parent.parent / "prompts" / "references"

# Banner wrapped around every loaded reference. Used by
# ``strip_duplicate_workflow_reference`` to detect a reference tool-return and
# by the size cap. MUST be distinct from the read_template marker ("=== Sheet:")
# so the two dedup processors never double-handle each other's payloads.
WORKFLOW_REFERENCE_MARKER = "=== WORKFLOW REFERENCE:"

# Hard cap on the rendered reference (chars). The largest canonicalized file is
# ~9 KB; this only guards against a pathological future file.
MAX_REFERENCE_CHARS = 24_000

# Statements whose reference is load-bearing enough to GATE the first write on
# it (deterministic activation, not "the agent remembers"). Other statements
# have references too, but loading them is advisory.
_GATED_STATEMENTS = frozenset({StatementType.SOCF, StatementType.SOCIE})

# Static (statement, key-suffix) → filename map. Keys are
# ``{statement.value.lower()}-{tier.lower()}`` where ``tier`` is the variant
# (populated today), the filing standard, or "" (generic). Only variant-tier
# entries exist now — one per ``docs/workflows/*.md``.
_REFERENCE_FILES: dict[str, str] = {
    "sofp-cunoncu": "sofp-cunoncu.md",
    "sofp-orderofliquidity": "sofp-orderofliquidity.md",
    "sopl-function": "sopl-function.md",
    "sopl-nature": "sopl-nature.md",
    "soci-beforetax": "soci-beforetax.md",
    "soci-netoftax": "soci-netoftax.md",
    "socf-indirect": "socf-indirect.md",
    "socf-direct": "socf-direct.md",
    "socie-default": "socie-default.md",
}

# Filing standards each reference is valid for. EVERY shipped reference is
# derived from the MFRS (FINCO) workflow docs; the SOCIE one in particular
# describes a 24-column MATRIX with explicit row/col writes, which directly
# CONTRADICTS the MPERS SOCIE layout (prompts/socie_mpers.md uses field_label,
# not a matrix — gotcha #15). Serving an MFRS reference to an MPERS run would
# reintroduce that MPERS-SOCIE failure class, so the shelf is MFRS-only for now:
# an MPERS run resolves to NO reference and falls back to its (correct, tested)
# MPERS prompt + read_template(). Author MPERS-specific references later, mark
# them frozenset({"mpers"}) (or both), and the resolver picks them up.
_REFERENCE_STANDARDS: dict[str, frozenset] = {
    key: frozenset({"mfrs"}) for key in _REFERENCE_FILES
}

# Process-global cache of rendered reference bodies, keyed by reference key.
# Mirrors ``extraction.agent._TEMPLATE_SUMMARY_CACHE``: the file is read +
# wrapped once per process, then reused.
_WORKFLOW_REFERENCE_CACHE: dict[str, str] = {}

_NO_REFERENCE_MSG = (
    "No workflow reference is available for this statement/variant. "
    "Proceed using the statement prompt and read_template() — there is no extra "
    "reference depth to load for this one."
)


def resolve_reference_key(
    statement_type: StatementType,
    variant: str,
    filing_standard: str = "mfrs",
) -> Optional[str]:
    """Resolve the reference-shelf key for a run, or ``None`` if none applies.

    Precedence mirrors ``prompts.render_prompt`` exactly:
        1. variant tier   ``{stmt}-{variant}``   (e.g. ``socf-indirect``)
        2. standard tier   ``{stmt}-{standard}``  (e.g. ``socie-mpers`` — none yet)
        3. generic tier   ``{stmt}``             (e.g. ``socf`` — none yet)

    A candidate only resolves if the run's ``filing_standard`` is in that
    reference's ``_REFERENCE_STANDARDS`` set — so an MFRS-only reference is
    NEVER served to an MPERS run (which would reintroduce the gotcha #15
    MPERS-SOCIE failure; the whole shelf is MFRS-only today). Returns the FIRST
    matching tier. The lookup keys are derived solely from typed run config —
    never from model input.
    """
    stmt = statement_type.value.lower()
    std = filing_standard.lower()
    candidates = [
        f"{stmt}-{variant.lower()}",
        f"{stmt}-{std}",
        stmt,
    ]
    for key in candidates:
        if key in _REFERENCE_FILES and std in _REFERENCE_STANDARDS.get(key, frozenset()):
            return key
    return None


def _render_reference(key: str) -> str:
    """Read + wrap + size-cap the reference body for ``key`` (cached)."""
    cached = _WORKFLOW_REFERENCE_CACHE.get(key)
    if cached is not None:
        return cached
    path = REFERENCE_DIR / _REFERENCE_FILES[key]
    try:
        body = path.read_text(encoding="utf-8").strip()
    except OSError as exc:  # pragma: no cover - shelf is shipped with the repo
        logger.warning("workflow reference %s unreadable: %s", key, exc)
        return _NO_REFERENCE_MSG
    header = f"{WORKFLOW_REFERENCE_MARKER} {key} ===\n"
    footer_room = len(header) + 80
    if len(body) > MAX_REFERENCE_CHARS - footer_room:
        body = (
            body[: MAX_REFERENCE_CHARS - footer_room]
            + "\n\n[reference truncated — confirm remaining detail against read_template()]"
        )
    rendered = header + body
    _WORKFLOW_REFERENCE_CACHE[key] = rendered
    return rendered


def load_reference_text(
    statement_type: StatementType,
    variant: str,
    filing_standard: str = "mfrs",
) -> str:
    """Return the wrapped reference body for a run, or the no-reference message.

    This is what the ``load_workflow_reference`` tool returns. All inputs are
    typed run config; there is no path argument and no model-controlled string
    ever reaches the filesystem.
    """
    key = resolve_reference_key(statement_type, variant, filing_standard)
    if key is None:
        return _NO_REFERENCE_MSG
    return _render_reference(key)


def workflow_reference_gate_armed() -> bool:
    """Whether the first-write activation gate is armed (read at call time).

    Default ON in production. ``tests/conftest.py`` defaults it OFF for the
    suite (like ``XBRL_SPOT_CHECK``) so the deterministic mocked-pipeline tests
    aren't perturbed; the dedicated gate test opts back in with
    ``monkeypatch.setenv``.
    """
    return os.getenv("XBRL_WORKFLOW_REFERENCE_GATE", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
        "",
    )


def workflow_reference_gate_error(
    statement_type: StatementType,
    variant: str,
    filing_standard: str,
    reference_loaded: bool,
) -> Optional[str]:
    """Return a refusal message if the first write should be blocked, else None.

    The gate fires only when ALL hold:
      * the gate is armed (env), AND
      * the statement is one whose reference is load-bearing (SOCIE / SOCF), AND
      * a reference actually EXISTS for this combo (so we never force a pointless
        call for, e.g., MPERS SoRE which has no reference), AND
      * the agent has not yet called ``load_workflow_reference`` this run.

    Once the agent calls the tool, ``reference_loaded`` flips True and the gate
    is satisfied — exactly like ``last_verify_result`` re-gates ``save_result``.
    The agent recovers by loading the reference and re-sending the write; no
    data is lost.
    """
    if reference_loaded:
        return None
    if not workflow_reference_gate_armed():
        return None
    if statement_type not in _GATED_STATEMENTS:
        return None
    if resolve_reference_key(statement_type, variant, filing_standard) is None:
        return None
    return (
        "write_facts refused: this is a matrix/articulation-heavy statement "
        f"({statement_type.value}) with a workflow reference you have not read "
        "yet. Call load_workflow_reference() first (it returns the fill workflow, "
        "sign conventions, and a worked example for this statement), then re-send "
        "this write. This costs one turn and prevents the classic SOCIE/SOCF "
        "sign and coordinate mistakes."
    )
