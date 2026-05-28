"""Monolith face-statement extraction (experiment).

A single-agent path that fills all 5 MFRS Company face statements in one
PydanticAI run, gated behind the `orchestration="monolith"` flag (PRD:
`docs/PRD-monolith-face-experiment.md`). The split-pipeline coordinator
(`coordinator.py`) is unchanged and remains the default.

Public surface:
  - `monolith.state.build_state_snapshot` — per-turn dashboard for the agent.
  - `monolith.tools.create_monolith_tools` — get_state / write_cells / done.
  - `monolith.coordinator.run_monolith` — orchestrator parallel to
    `coordinator.run_extraction`.
  - `monolith.config.MAX_AGENT_ITERATIONS_MONOLITH` — separate iteration cap.
"""
