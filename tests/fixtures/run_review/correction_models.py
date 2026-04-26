"""Mocked LLM models for CORRECTION agent regression tests.

Built for Phase 2 of `docs/PLAN-run-review-fixes.md` — the CORRECTION
turn-flood fixture. Each model is a `FunctionModel` that scripts a
specific sequence of tool calls so we can pin the new iteration-cap +
diff-first behaviour without burning real LLM budget.

Use these from `pytest`-style tests like::

    from tests.fixtures.run_review.correction_models import inspect_flood_model

    agent, deps = create_correction_agent(..., model=inspect_flood_model(n=30))
    # The new cap should fire long before turn 30 — assert the agent
    # exits with status="correction_exhausted".
"""

from __future__ import annotations

import json
from typing import Optional

from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel


def inspect_flood_model(
    *,
    n: int = 30,
    final_fill_args: Optional[dict] = None,
) -> FunctionModel:
    """Return a FunctionModel that emits N consecutive ``inspect_workbook``
    tool calls, then one ``fill_workbook`` and a closing text response.

    Mirrors the failure mode from RUN-REVIEW.md §3.4: the corrector
    spirals on inspect calls until pydantic-ai's silent 50-request cap
    fires. With Phase 2.1 in place, the new dynamic cap should bail out
    long before turn ``n`` is reached.
    """
    call_count = 0

    def model_function(messages, info: AgentInfo) -> ModelResponse:
        nonlocal call_count
        call_count += 1

        # 1..n: emit inspect_workbook (the failure-mode behaviour).
        # Tool signature is `inspect_workbook(ctx, query_json: str)` so
        # the tool args dict's KEY must be `query_json`, with the inner
        # JSON as its STRING value.
        if call_count <= n:
            return ModelResponse(parts=[
                ToolCallPart(
                    tool_name="inspect_workbook",
                    args={"query_json": json.dumps(
                        {"sheet": "SOFP-CuNonCu", "rows": [10, 20]}
                    )},
                    tool_call_id=f"insp-{call_count}",
                ),
            ])

        # n+1: a single planned diff (what a diff-first corrector would do
        # right after one inspect, not after 30 of them). Tool signature
        # is `fill_workbook(ctx, fields_json: str)` so wrap correspondingly.
        if call_count == n + 1:
            return ModelResponse(parts=[
                ToolCallPart(
                    tool_name="fill_workbook",
                    args=final_fill_args or {
                        "fields_json": json.dumps([
                            {"sheet": "SOFP-CuNonCu", "row": 30, "col": 2, "value": 0},
                        ]),
                    },
                    tool_call_id=f"fill-{call_count}",
                ),
            ])

        # n+2: closing message — required so the agent exits cleanly when
        # the cap is NOT yet enforced (smoke-test pre-Phase-2.1).
        return ModelResponse(parts=[TextPart(
            content="Correction complete after inspect-flood scenario.",
        )])

    return FunctionModel(model_function)


def diff_first_model(*, fill_args: Optional[dict] = None) -> FunctionModel:
    """Return a FunctionModel that does ONE inspect, ONE fill, ONE verify.

    This is the post-Phase-2.3 expected shape: minimal context-gathering,
    a single planned diff, single verification, done. Used by tests that
    pin the new prompt's behaviour against a well-behaved model so we
    don't conflate "prompt regressed" with "model is bad".
    """
    call_count = 0

    def model_function(messages, info: AgentInfo) -> ModelResponse:
        nonlocal call_count
        call_count += 1

        if call_count == 1:
            return ModelResponse(parts=[
                ToolCallPart(
                    tool_name="inspect_workbook",
                    args={"query_json": json.dumps({"sheet": "SOFP-CuNonCu", "rows": [10]})},
                    tool_call_id="insp-1",
                ),
            ])
        if call_count == 2:
            return ModelResponse(parts=[
                ToolCallPart(
                    tool_name="fill_workbook",
                    args=fill_args or {
                        "fields_json": json.dumps([
                            {"sheet": "SOFP-CuNonCu", "row": 10, "col": 2, "value": 100},
                        ]),
                    },
                    tool_call_id="fill-1",
                ),
            ])
        if call_count == 3:
            return ModelResponse(parts=[
                ToolCallPart(
                    tool_name="verify_totals",
                    args={"statement": "SOFP"},
                    tool_call_id="ver-1",
                ),
            ])
        return ModelResponse(parts=[TextPart(content="Diff applied; verified.")])

    return FunctionModel(model_function)
