"""Provider-neutral token usage breakdown for live telemetry.

PydanticAI exposes aggregate output tokens plus provider-specific detail keys
for the reasoning subset. Keep the split in one place so live cost and token
displays do not silently report every reasoning model as zero-thinking.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class UsageMetrics:
    prompt_tokens: int
    completion_tokens: int
    thinking_tokens: int
    total_tokens: int


_THINKING_DETAIL_KEYS = (
    "reasoning_tokens",  # OpenAI / compatible providers
    "thoughts_tokens",   # Google Gemini
)


def _token_attr(usage: Any, current: str, legacy: str) -> int:
    value = getattr(usage, current, None)
    if value is None:
        value = getattr(usage, legacy, 0)
    return int(value or 0)


def derive_thinking_tokens(
    total_tokens: int,
    prompt_tokens: int,
    completion_tokens: int,
) -> int:
    """Recover reasoning from persisted totals without provider metadata.

    New rows persist visible completion separately from reasoning while total
    remains the provider aggregate. Legacy rows stored all output as
    completion, so the same calculation correctly returns zero for them.
    """
    return max(
        int(total_tokens or 0)
        - int(prompt_tokens or 0)
        - int(completion_tokens or 0),
        0,
    )


def split_usage(usage: Any) -> UsageMetrics:
    """Return prompt, visible completion, reasoning, and total token counts.

    Provider reasoning is included inside ``output_tokens``. The detail keys
    identify that subset, so subtract it from visible completion and keep the
    original aggregate total unchanged. ``max`` avoids double-counting when a
    gateway supplies both a native and compatibility alias for the same value.
    """
    prompt = _token_attr(usage, "input_tokens", "request_tokens")
    output = _token_attr(usage, "output_tokens", "response_tokens")
    details = getattr(usage, "details", {}) or {}
    if not isinstance(details, Mapping):
        details = {}
    thinking = max(
        (int(details.get(key, 0) or 0) for key in _THINKING_DETAIL_KEYS),
        default=0,
    )
    # Reasoning is a subset of output. Clamp malformed provider metadata so a
    # telemetry anomaly cannot create a negative completion count.
    thinking = min(max(thinking, 0), max(output, 0))
    total = int(getattr(usage, "total_tokens", 0) or (prompt + output))
    return UsageMetrics(
        prompt_tokens=prompt,
        completion_tokens=max(output - thinking, 0),
        thinking_tokens=thinking,
        total_tokens=total,
    )
