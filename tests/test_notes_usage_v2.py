"""V2-review regression pins (2026-07-12): notes telemetry with REAL RunUsage.

The review's probe showed `_backfill_token_report` producing 0/0 under V2
because the caller passes ``agent_run.usage`` — which V2 makes a property
VALUE, not a callable — and the resulting TypeError was swallowed by the
advisory try/except. These tests use a genuine ``pydantic_ai.usage.RunUsage``
(not stubs, not legacy field names) against both accepted shapes.
"""

from pydantic_ai.usage import RunUsage

from notes.coordinator import _backfill_token_report
from token_tracker import TokenReport


def _report() -> TokenReport:
    return TokenReport(model="test-model")


def test_backfill_accepts_usage_value_directly():
    """The V2 idiom: the property VALUE is passed, no callable involved."""
    report = _report()
    usage = RunUsage(input_tokens=123, output_tokens=45)
    _backfill_token_report(report, usage, "NOTES_TEST")
    assert report.total_prompt_tokens == 123
    assert report.total_completion_tokens == 45


def test_backfill_still_accepts_zero_arg_callable():
    """Legacy/test shape stays supported."""
    report = _report()
    _backfill_token_report(
        report, lambda: RunUsage(input_tokens=7, output_tokens=3), "NOTES_TEST"
    )
    assert report.total_prompt_tokens == 7
    assert report.total_completion_tokens == 3


def test_no_silent_zero_on_real_v2_usage():
    """The exact reviewer probe: RunUsage(123, 45) must never read 0/0."""
    report = _report()
    _backfill_token_report(report, RunUsage(input_tokens=123, output_tokens=45), "X")
    assert (report.total_prompt_tokens, report.total_completion_tokens) != (0, 0)


def test_backfill_separates_provider_reasoning_tokens():
    report = _report()
    usage = RunUsage(
        input_tokens=123,
        output_tokens=45,
        details={"reasoning_tokens": 20},
    )

    _backfill_token_report(report, usage, "NOTES_TEST")

    assert report.total_prompt_tokens == 123
    assert report.total_completion_tokens == 25
    assert report.total_thinking_tokens == 20
    assert report.grand_total == 168
