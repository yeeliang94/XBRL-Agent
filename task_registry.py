"""Loop-aware registry for asyncio tasks, keyed by (session_id, agent_id).

Suite children and background passes may run on event loops owned by worker
threads while HTTP stop requests arrive on the server loop. Cancellation is
therefore dispatched to each task's owning loop instead of invoking Task.cancel
from an unrelated thread.
"""

import asyncio
import threading
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class _TaskRecord:
    task: asyncio.Task
    loop: asyncio.AbstractEventLoop


# session_id -> {agent_id -> (task, owning loop)}
_tasks: dict[str, dict[str, _TaskRecord]] = {}
_lock = threading.RLock()

# Cancellation reason carried by asyncio.CancelledError for an explicit Stop
# request. Provider/transport cancellations are deliberately left untagged so
# reviewer passes can classify them as recoverable interruptions instead of
# falsely attributing them to the operator. Every deliberate cancellation of a
# registered agent/reviewer task MUST call ``task.cancel(USER_ABORT_REASON)``;
# a bare ``task.cancel()`` is intentionally interpreted as a provider-side
# interruption by the reviewer passes.
USER_ABORT_REASON = "user_abort"


def is_user_abort(exc: BaseException) -> bool:
    """Return True only for cancellations initiated by the Stop endpoints."""
    return bool(exc.args and exc.args[0] == USER_ABORT_REASON)


def register(session_id: str, agent_id: str, task: asyncio.Task) -> None:
    """Track a running agent task so it can be cancelled later."""
    record = _TaskRecord(task=task, loop=task.get_loop())
    with _lock:
        if session_id not in _tasks:
            _tasks[session_id] = {}
        _tasks[session_id][agent_id] = record


def _request_cancel(record: _TaskRecord) -> bool:
    """Request cancellation on the task's owning event loop."""
    task = record.task
    if task.done() or record.loop.is_closed():
        return False

    def cancel_if_running() -> None:
        if not task.done():
            task.cancel(USER_ABORT_REASON)

    try:
        current_loop = asyncio.get_running_loop()
    except RuntimeError:
        current_loop = None
    if current_loop is record.loop:
        cancel_if_running()
    else:
        try:
            record.loop.call_soon_threadsafe(cancel_if_running)
        except RuntimeError:
            # The owner may finish and close its loop between the check above
            # and this dispatch. There is no live task left to cancel.
            return False
    return True


def cancel_agent(session_id: str, agent_id: str) -> bool:
    """Cancel a single agent. Returns True if the task was found and cancelled."""
    with _lock:
        record = _tasks.get(session_id, {}).get(agent_id)
    if record is None:
        return False
    return _request_cancel(record)


def unregister(session_id: str, agent_id: str) -> None:
    """Remove a single task reference (e.g. after it completes)."""
    with _lock:
        session = _tasks.get(session_id)
        if session is not None:
            session.pop(agent_id, None)
            if not session:
                del _tasks[session_id]


def cancel_all(session_id: str) -> int:
    """Cancel all agents in a session. Returns number of tasks cancelled."""
    with _lock:
        records = list(_tasks.get(session_id, {}).values())
    return sum(1 for record in records if _request_cancel(record))


def remove_session(session_id: str) -> None:
    """Clean up all task references for a finished session."""
    with _lock:
        _tasks.pop(session_id, None)


def get_task(session_id: str, agent_id: str) -> Optional[asyncio.Task]:
    """Look up a task (for testing / inspection)."""
    with _lock:
        record = _tasks.get(session_id, {}).get(agent_id)
    return record.task if record is not None else None
