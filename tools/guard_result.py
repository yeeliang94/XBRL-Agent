"""GuardResult — one structured verdict contract for deterministic guards.

Harness-learnings Item 2 (docs/PLAN-pydantic-ai-v2.md D.3): the codebase has
~6 hand-rolled guards (abstract-row / formula-cell / double-booking in
``tools/fill_workbook.py``, the reviewer grounding gate in
``notes/reviewer_agent.py``, the format gate, the save-gate) and each
reported rejections in its own ad-hoc shape — a ``(kind, message)`` tuple
here, a free-text ``errors`` list entry there. This module gives them one
closed vocabulary so cross-guard telemetry ("how often does each guard fire,
and does the agent recover?") stops being ad hoc.

The contract (pattern borrowed from pydantic-ai-harness guardrails,
re-implemented natively):

- ``allow``   — the write/action proceeds. ``message`` optional (advisory
  warning that does NOT flip success — e.g. the double-booking detector).
- ``retry``   — the action is refused WITH an actionable correction the
  agent should act on ("first view the PDF page…"). This is the normal
  verdict for our in-run guards: the message goes back as the tool result
  and the agent gets another attempt under its existing turn budget.
- ``block``   — the action is refused with no self-correction path; the
  caller decides how to surface it.
- ``replace`` — the action proceeds with ``replacement`` substituted for
  the offending value (no current adopter; part of the closed vocabulary
  so a sanitising guard can join without inventing a new shape).

``kind`` is the stable, machine-countable slug for telemetry/tallies
(e.g. ``"ungrounded"``, ``"abstract_row"``); ``message`` is the exact text
shown to the agent — adopting guards MUST keep their pinned wording.

Adoption is guard-by-guard and behavior-preserving: existing classifier
functions (e.g. ``classify_notes_fix_guard``) stay exported and pinned;
a thin ``evaluate_*`` wrapper lifts their result into a GuardResult.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

GuardAction = Literal["allow", "retry", "block", "replace"]


@dataclass(frozen=True)
class GuardResult:
    """A single guard verdict. Construct via the classmethods, not directly."""

    action: GuardAction
    message: Optional[str] = None
    kind: Optional[str] = None
    replacement: object = None

    def __post_init__(self) -> None:
        # Closed contract per action — a malformed verdict is a programming
        # error at the guard, caught at construction rather than downstream.
        if self.action in ("retry", "block"):
            if not self.message:
                raise ValueError(f"GuardResult({self.action!r}) requires a message")
            if not self.kind:
                raise ValueError(f"GuardResult({self.action!r}) requires a kind")
        elif self.action == "replace":
            if self.replacement is None:
                raise ValueError("GuardResult('replace') requires a replacement")
        elif self.action == "allow":
            if self.kind and not self.message:
                raise ValueError(
                    "GuardResult('allow') with a kind must carry the advisory message"
                )
        else:  # pragma: no cover — Literal keeps this unreachable
            raise ValueError(f"unknown guard action {self.action!r}")

    # --- constructors -----------------------------------------------------

    @classmethod
    def allow(cls, *, warning: Optional[str] = None, kind: Optional[str] = None) -> "GuardResult":
        """Proceed; optional advisory warning that does not flip success."""
        return cls(action="allow", message=warning, kind=kind)

    @classmethod
    def retry(cls, message: str, *, kind: str) -> "GuardResult":
        """Refuse with an actionable correction the agent should act on."""
        return cls(action="retry", message=message, kind=kind)

    @classmethod
    def block(cls, message: str, *, kind: str) -> "GuardResult":
        """Refuse with no self-correction path."""
        return cls(action="block", message=message, kind=kind)

    @classmethod
    def replace(cls, replacement: object, *, kind: Optional[str] = None) -> "GuardResult":
        """Proceed with the offending value substituted."""
        return cls(action="replace", replacement=replacement, kind=kind)

    # --- ergonomics ---------------------------------------------------------

    @property
    def allowed(self) -> bool:
        """True when the guarded action should proceed (allow / replace)."""
        return self.action in ("allow", "replace")

    @classmethod
    def from_kind_message(
        cls, kind: Optional[str], message: Optional[str], *, fallback_kind: str = "rejected"
    ) -> "GuardResult":
        """Bridge from the legacy ``(kind, message)`` classifier tuple shape.

        ``(None, None)`` → allow; anything with a message → retry (our guards'
        rejections are always actionable corrections).
        """
        if message is None:
            return cls.allow()
        return cls.retry(message, kind=kind or fallback_kind)
