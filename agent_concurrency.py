"""Bounded agent fan-out (PLAN-extraction-harness-efficiency, Step 7).

``coordinator.run_extraction`` and ``notes.coordinator.run_notes_extraction``
each create one task per statement / notes template with no cap, so a full
run launches up to ~10 top-level agents at once (plus the Sheet-12 sub-agent
fan-out inside one of them) and real provider 429s are already on record in
``agent_events``. ``XBRL_MAX_CONCURRENT_AGENTS`` bounds how many top-level
agents may be RUNNING at once; the rest wait for a slot.

Design constraints, all deliberate:

- **Default is unbounded (0 / unset)** — a no-op that reproduces today's
  behaviour exactly. Rollback is unsetting the variable.
- **Gating happens INSIDE the task**, not around ``create_task``. Every task
  is still created and registered up-front, so per-agent cancellation, the
  ``task_registry`` abort API, the ``CancelledError`` grace-period path and
  the sentinel push in ``finally`` (gotcha #10) are untouched. A queued task
  that is cancelled raises ``CancelledError`` out of ``acquire()`` and lands
  in the coordinator's result loop as ``cancelled`` — never left ``running``.
- **One semaphore per event loop** (gotcha #2a) — the face and notes
  coordinators share the server's loop and therefore share the cap; the
  reviewer / suite threads have their own loops and their own semaphore.
- **Only TOP-LEVEL agents take a slot.** The Sheet-12 sub-agents run inside
  their parent's slot; gating them too would deadlock a cap smaller than the
  fan-out width (parent holds a slot while its children wait for one).
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from typing import AsyncIterator, Optional

logger = logging.getLogger(__name__)

ENV_VAR = "XBRL_MAX_CONCURRENT_AGENTS"

# The semaphore lives ON the event loop object (a private attribute), not in
# a module registry. An asyncio primitive must only be awaited from the loop
# that created it (gotcha #2a); attaching it to that loop guarantees it and
# — unlike an id(loop)-keyed dict — cannot leak: a contended Semaphore holds
# a strong reference to its loop, so a registry entry keeps a finished loop
# alive forever (peer review, 2026-08-18). The cap is stored alongside so a
# changed env var (tests) rebuilds it.
_LOOP_ATTR = "_xbrl_agent_slot"


def max_concurrent_agents() -> int:
    """The configured cap; ``0`` means unbounded (the default). A negative or
    unparsable value is treated as unbounded and logged once — a bad config
    must never stall a run."""
    raw = os.environ.get(ENV_VAR, "").strip()
    if not raw:
        return 0
    try:
        cap = int(raw)
    except ValueError:
        logger.warning("%s=%r is not an integer — running unbounded", ENV_VAR, raw)
        return 0
    return cap if cap > 0 else 0


def _semaphore_for_current_loop(cap: int) -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    entry = getattr(loop, _LOOP_ATTR, None)
    if entry is None or entry[0] != cap:
        entry = (cap, asyncio.Semaphore(cap))
        setattr(loop, _LOOP_ATTR, entry)
    return entry[1]


@contextlib.asynccontextmanager
async def agent_slot(label: Optional[str] = None) -> AsyncIterator[None]:
    """Hold one concurrency slot for the duration of an agent's run.

    A no-op when the cap is unbounded. Cancellation while waiting propagates
    as ``CancelledError`` (the slot is never taken), and a slot is always
    released on exit — including exceptions and cancellation mid-run.
    """
    cap = max_concurrent_agents()
    if cap <= 0:
        yield
        return
    sem = _semaphore_for_current_loop(cap)
    if sem.locked():
        logger.info("%s: waiting for an agent slot (cap %d)", label or "agent", cap)
    async with sem:
        yield
