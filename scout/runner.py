"""Scout runner — backward-compatible entry point.

Delegates to the PydanticAI scout agent in scout/agent.py.
This module exists so that existing imports (server.py, run.py, tests)
continue to work without changes:

    from scout.runner import run_scout
"""
from __future__ import annotations

# Re-export the agent-based run_scout as the public API
from scout.agent import run_scout  # noqa: F401

__all__ = ["run_scout"]
