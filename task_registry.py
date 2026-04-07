"""In-memory registry for asyncio tasks, keyed by (session_id, agent_id).

Allows the server to cancel individual agents or entire sessions.
No locks needed — all access happens on the single-threaded asyncio event loop.
"""

import asyncio
from typing import Optional

# session_id -> {agent_id -> Task}
_tasks: dict[str, dict[str, asyncio.Task]] = {}


def register(session_id: str, agent_id: str, task: asyncio.Task) -> None:
    """Track a running agent task so it can be cancelled later."""
    if session_id not in _tasks:
        _tasks[session_id] = {}
    _tasks[session_id][agent_id] = task


def cancel_agent(session_id: str, agent_id: str) -> bool:
    """Cancel a single agent. Returns True if the task was found and cancelled."""
    session = _tasks.get(session_id, {})
    task = session.get(agent_id)
    if task is None or task.done():
        return False
    task.cancel()
    return True


def cancel_all(session_id: str) -> int:
    """Cancel all agents in a session. Returns number of tasks cancelled."""
    session = _tasks.get(session_id, {})
    count = 0
    for task in session.values():
        if not task.done():
            task.cancel()
            count += 1
    return count


def remove_session(session_id: str) -> None:
    """Clean up all task references for a finished session."""
    _tasks.pop(session_id, None)


def get_task(session_id: str, agent_id: str) -> Optional[asyncio.Task]:
    """Look up a task (for testing / inspection)."""
    return _tasks.get(session_id, {}).get(agent_id)
