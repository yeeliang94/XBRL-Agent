"""PLAN §4 Phase E.1 — retry-budget enforcement for single-agent notes sheets.

The coordinator must:
  1. Retry a failed single-agent run exactly once before marking it failed.
  2. Never retry on asyncio.CancelledError (user abort must propagate as-is).
  3. Write a per-sheet ``notes_<TEMPLATE>_failures.json`` side-log when the
     retry budget is exhausted.
  4. Keep sheet-level failures isolated from other sheets (already covered
     by test_coordinator_isolates_per_template_failures).
  5. Treat HTTP 429 (rate-limit) errors as a separate, larger retry
     budget with honoured retry-after hints and backoff — a TPM throttle
     is not a real failure and must not burn the generic-error budget.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic_ai.exceptions import ModelHTTPError

from notes import coordinator as coord_mod
from notes._rate_limit import (
    RATE_LIMIT_MAX_RETRIES,
    compute_backoff_delay,
    is_rate_limit_error,
    parse_retry_after,
)
from notes.coordinator import (
    NotesRunConfig,
    _NoWriteError,
    _run_single_notes_agent,
    _SingleAgentOutcome,
    run_notes_extraction,
)
from notes_types import NotesTemplateType


def _rate_limit_error(message: str = "Please try again in 745ms.") -> ModelHTTPError:
    """Build a 429 that looks like what pydantic-ai surfaces from OpenAI."""
    return ModelHTTPError(
        status_code=429,
        model_name="gpt-5.4-mini",
        body={"message": message, "type": "tokens", "code": "rate_limit_exceeded"},
    )


def _ok(path: str) -> _SingleAgentOutcome:
    """Build a minimal success outcome for tests that don't care about
    writer diagnostics — keeps the test body focused on retry contract."""
    return _SingleAgentOutcome(filled_path=path)


@pytest.mark.asyncio
async def test_single_agent_retries_once_on_exception(tmp_path: Path):
    """An agent that raises on the first attempt must be retried exactly once.

    Using a call-counter on the inner ``_invoke_single_notes_agent_once``
    gives a crisp contract test that survives internal refactors of the
    retry loop.
    """
    calls: list[int] = []

    async def flaky_invoke(**kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("transient upstream hiccup")
        # Second attempt succeeds.
        return _ok(str(tmp_path / "NOTES_CORP_INFO_filled.xlsx"))

    with patch.object(coord_mod, "_invoke_single_notes_agent_once", side_effect=flaky_invoke):
        result = await _run_single_notes_agent(
            template_type=NotesTemplateType.CORP_INFO,
            pdf_path=str(tmp_path / "x.pdf"),
            inventory=[],
            filing_level="company",
            model="test",
            output_dir=str(tmp_path),
        )

    assert len(calls) == 2, "expected exactly one retry (2 total attempts)"
    assert result.status == "succeeded"
    assert result.workbook_path is not None


@pytest.mark.asyncio
async def test_single_agent_stops_at_max_retries(tmp_path: Path):
    """When every attempt fails, the coordinator must give up after
    ``max_retries + 1`` attempts, mark the sheet failed, and persist a
    ``notes_<TEMPLATE>_failures.json`` side-log."""
    calls: list[int] = []

    async def always_fail(**kwargs):
        calls.append(1)
        raise RuntimeError(f"failure #{len(calls)}")

    with patch.object(coord_mod, "_invoke_single_notes_agent_once", side_effect=always_fail):
        result = await _run_single_notes_agent(
            template_type=NotesTemplateType.RELATED_PARTY,
            pdf_path=str(tmp_path / "x.pdf"),
            inventory=[],
            filing_level="company",
            model="test",
            output_dir=str(tmp_path),
        )

    assert len(calls) == 2, "PLAN §4 E.1: 1 retry means 2 total attempts"
    assert result.status == "failed"
    assert "failure #2" in (result.error or "")

    # Side-log written with both attempts recorded.
    log_path = tmp_path / "notes_RELATED_PARTY_failures.json"
    assert log_path.exists()
    payload = json.loads(log_path.read_text(encoding="utf-8"))
    assert payload["template"] == "RELATED_PARTY"
    assert len(payload["attempts"]) == 2
    assert payload["attempts"][0]["error"] == "failure #1"
    assert payload["attempts"][1]["error"] == "failure #2"


@pytest.mark.asyncio
async def test_single_agent_retries_silent_no_write(tmp_path: Path):
    """The ``_NoWriteError`` (agent finished without calling write_notes)
    is a retryable condition — the model sometimes succeeds on retry."""
    calls: list[int] = []

    async def no_write_then_succeed(**kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise _NoWriteError("Notes agent finished without writing any payloads")
        return _ok(str(tmp_path / "NOTES_ACC_POLICIES_filled.xlsx"))

    with patch.object(coord_mod, "_invoke_single_notes_agent_once", side_effect=no_write_then_succeed):
        result = await _run_single_notes_agent(
            template_type=NotesTemplateType.ACC_POLICIES,
            pdf_path=str(tmp_path / "x.pdf"),
            inventory=[],
            filing_level="company",
            model="test",
            output_dir=str(tmp_path),
        )

    assert len(calls) == 2
    assert result.status == "succeeded"


@pytest.mark.asyncio
async def test_cancelled_error_never_retries(tmp_path: Path):
    """User abort must propagate as status='cancelled' on the first attempt
    — retrying a cancellation would trap the user's intent to stop."""
    calls: list[int] = []

    async def cancel_now(**kwargs):
        calls.append(1)
        raise asyncio.CancelledError()

    with patch.object(coord_mod, "_invoke_single_notes_agent_once", side_effect=cancel_now):
        result = await _run_single_notes_agent(
            template_type=NotesTemplateType.ISSUED_CAPITAL,
            pdf_path=str(tmp_path / "x.pdf"),
            inventory=[],
            filing_level="company",
            model="test",
            output_dir=str(tmp_path),
        )

    assert len(calls) == 1, "cancellation must NOT be retried"
    assert result.status == "cancelled"
    # No failure side-log — cancellation isn't a failure.
    assert not (tmp_path / "notes_ISSUED_CAPITAL_failures.json").exists()


@pytest.mark.asyncio
async def test_sheet_failure_does_not_block_other_sheets(tmp_path: Path):
    """Cross-sheet isolation — one sheet exhausting its retries must not
    affect a sibling sheet that's running in parallel."""
    pdf_path = tmp_path / "uploaded.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")
    config = NotesRunConfig(
        pdf_path=str(pdf_path),
        output_dir=str(tmp_path),
        model="test",
        notes_to_run={NotesTemplateType.CORP_INFO, NotesTemplateType.ACC_POLICIES},
        filing_level="company",
    )

    # Patch the whole per-agent runner so we skip the retry machinery entirely
    # here; the previous tests already verify the retry contract. This one
    # only checks the outer coordinator's isolation after retries settle.
    async def fake_run(**kwargs):
        from notes.coordinator import NotesAgentResult

        if kwargs["template_type"] == NotesTemplateType.CORP_INFO:
            return NotesAgentResult(
                template_type=NotesTemplateType.CORP_INFO,
                status="failed",
                error="retries exhausted",
            )
        return NotesAgentResult(
            template_type=NotesTemplateType.ACC_POLICIES,
            status="succeeded",
            workbook_path=str(tmp_path / "NOTES_ACC_POLICIES_filled.xlsx"),
        )

    with patch.object(coord_mod, "_run_single_notes_agent", side_effect=fake_run):
        result = await run_notes_extraction(config, infopack=None)

    by_tpl = {r.template_type: r for r in result.agent_results}
    assert by_tpl[NotesTemplateType.CORP_INFO].status == "failed"
    assert by_tpl[NotesTemplateType.ACC_POLICIES].status == "succeeded"
    assert not result.all_succeeded


@pytest.mark.asyncio
async def test_single_agent_surfaces_writer_diagnostics_as_warnings(tmp_path: Path):
    """Peer-review [HIGH]: single-sheet success paths were dropping writer
    skip-errors and borderline fuzzy matches. They now ride through to
    ``NotesAgentResult.warnings`` so history/UI can flag partial successes."""
    async def succeed_with_warnings(**kwargs):
        return _SingleAgentOutcome(
            filled_path=str(tmp_path / "NOTES_CORP_INFO_filled.xlsx"),
            write_errors=[
                "No matching row for label 'Bogus label' in sheet 'Notes-CI'",
            ],
            fuzzy_matches=[
                ("Going concrn", "Going concern", 0.81),   # borderline
                ("Issued capital", "Issued capital", 1.00),  # exact — not a warning
            ],
        )

    with patch.object(coord_mod, "_invoke_single_notes_agent_once", side_effect=succeed_with_warnings):
        result = await _run_single_notes_agent(
            template_type=NotesTemplateType.CORP_INFO,
            pdf_path=str(tmp_path / "x.pdf"),
            inventory=[],
            filing_level="company",
            model="test",
            output_dir=str(tmp_path),
        )

    assert result.status == "succeeded"
    # Writer skip error + borderline fuzzy match surface as distinct warnings.
    assert any("bogus label" in w.lower() for w in result.warnings)
    assert any("borderline fuzzy match" in w.lower() for w in result.warnings)
    # Perfect (score=1.0) matches are NOT warnings — avoid log spam.
    assert not any("1.00" in w for w in result.warnings)


@pytest.mark.asyncio
async def test_max_retries_zero_runs_exactly_once(tmp_path: Path):
    """When max_retries=0 is passed (future override point for ops who want
    faster failures), the coordinator must run exactly once."""
    calls: list[int] = []

    async def always_fail(**kwargs):
        calls.append(1)
        raise RuntimeError("nope")

    with patch.object(coord_mod, "_invoke_single_notes_agent_once", side_effect=always_fail):
        result = await _run_single_notes_agent(
            template_type=NotesTemplateType.CORP_INFO,
            pdf_path=str(tmp_path / "x.pdf"),
            inventory=[],
            filing_level="company",
            model="test",
            output_dir=str(tmp_path),
            max_retries=0,
        )

    assert len(calls) == 1
    assert result.status == "failed"


@pytest.mark.asyncio
async def test_cancellation_emit_is_safe_against_queue_teardown(tmp_path: Path):
    """Peer-review #3: a cancelled agent used to await ``_emit`` inside the
    ``except CancelledError`` block. If the surrounding task is being torn
    down hard, that put can itself be cancelled and the terminal return
    never happens. The ``_safe_emit`` wrapper must swallow the inner
    failure so the outer coordinator always gets a cancelled result.
    """
    async def cancel_now(**kwargs):
        raise asyncio.CancelledError()

    # A queue that raises on put — simulates a teardown where the event
    # machinery is already gone by the time the cancellation handler runs.
    class _BrokenQueue:
        async def put(self, _item):
            raise RuntimeError("queue closed during teardown")

    with patch.object(coord_mod, "_invoke_single_notes_agent_once", side_effect=cancel_now):
        result = await _run_single_notes_agent(
            template_type=NotesTemplateType.CORP_INFO,
            pdf_path=str(tmp_path / "x.pdf"),
            inventory=[],
            filing_level="company",
            model="test",
            output_dir=str(tmp_path),
            event_queue=_BrokenQueue(),
        )

    # Without _safe_emit the broken queue would have bubbled up and the
    # caller would never see a structured cancelled result.
    assert result.status == "cancelled"


# ---------------------------------------------------------------------------
# Rate-limit (429) retry contract — the bug observed in production was that
# 5 parallel notes agents hit OpenAI's TPM bucket simultaneously, retried
# with zero backoff, and blew past the 1-retry generic budget within a
# single second. These tests pin the new contract: 429s have their own
# budget, honour the retry-after hint, and back off before retrying.
# ---------------------------------------------------------------------------


class TestParseRetryAfter:
    """The regex must pick up both ``ms`` and ``s`` units and tolerate the
    surrounding prose OpenAI emits ("Please try again in Xs. Visit …")."""

    def test_milliseconds(self):
        exc = _rate_limit_error("Please try again in 745ms. Visit https://...")
        assert parse_retry_after(exc) == pytest.approx(0.745)

    def test_seconds_with_decimals(self):
        exc = _rate_limit_error("Please try again in 1.498s. Visit https://...")
        assert parse_retry_after(exc) == pytest.approx(1.498)

    def test_integer_seconds(self):
        exc = _rate_limit_error("Please try again in 30s. Visit https://...")
        assert parse_retry_after(exc) == pytest.approx(30.0)

    def test_missing_hint_returns_none(self):
        """A 429 without a parseable hint (some proxies strip the prose)
        must fall back to exponential schedule — ``None`` signals that."""
        exc = _rate_limit_error("Rate limit exceeded.")
        assert parse_retry_after(exc) is None

    def test_non_rate_limit_returns_none(self):
        """500s and other non-429 errors are not rate limits, even if the
        body coincidentally says "try again in 5s"."""
        exc = ModelHTTPError(
            status_code=500,
            model_name="gpt-5.4-mini",
            body={"message": "Please try again in 5s."},
        )
        assert parse_retry_after(exc) is None

    def test_litellm_nested_body_shape(self):
        """LiteLLM sometimes wraps the OpenAI body under an ``error`` key —
        we must dig one level deeper to find the message."""
        exc = ModelHTTPError(
            status_code=429,
            model_name="gpt-5.4-mini",
            body={"error": {"message": "Try again in 500ms."}},
        )
        assert parse_retry_after(exc) == pytest.approx(0.5)

    def test_string_body(self):
        """If the upstream returns a raw string instead of a dict, parse
        the string directly — seen from some proxies."""
        exc = ModelHTTPError(
            status_code=429,
            model_name="gpt-5.4-mini",
            body="Try again in 2s",
        )
        assert parse_retry_after(exc) == pytest.approx(2.0)


class TestIsRateLimitError:
    def test_429_model_http_error(self):
        assert is_rate_limit_error(_rate_limit_error())

    def test_500_not_rate_limit(self):
        exc = ModelHTTPError(status_code=500, model_name="x", body={})
        assert not is_rate_limit_error(exc)

    def test_generic_runtime_error(self):
        assert not is_rate_limit_error(RuntimeError("boom"))


class TestBackoffDelay:
    def test_honours_hint_with_floor(self):
        """A 745ms hint must sleep at least 2s + jitter — OpenAI's hint is
        calibrated for tiny follow-ups, not full PDF-page retries."""
        exc = _rate_limit_error("Please try again in 745ms.")
        delay = compute_backoff_delay(exc, attempt=0)
        assert delay >= 2.0  # floor
        assert delay < 4.0   # floor + max jitter

    def test_honours_hint_above_floor(self):
        """A 30s hint must be honoured (not clamped to the 2s floor)."""
        exc = _rate_limit_error("Please try again in 30s.")
        delay = compute_backoff_delay(exc, attempt=0)
        assert delay >= 30.0
        assert delay < 32.0  # hint + max jitter

    def test_exponential_fallback_when_no_hint(self):
        """Missing hint → fall back to exponential schedule. Jitter makes
        the bounds loose but the retry-1 sleep must clearly exceed
        retry-0 sleep without jitter collision."""
        exc = _rate_limit_error("No hint in this body.")
        # attempt=0 → 2^1 = 2s + jitter; attempt=1 → 2^2 = 4s + jitter.
        # Even with max jitter on the first and min jitter on the second,
        # the gap still separates them cleanly.
        d0 = compute_backoff_delay(exc, attempt=0)
        d1 = compute_backoff_delay(exc, attempt=1)
        assert 2.0 <= d0 < 4.0
        assert 4.0 <= d1 < 6.0

    def test_absurd_hint_is_capped(self):
        """Peer-review I-1: a pathological provider hint like ``"try again
        in 9999999s"`` must not pin a sub-agent for days while holding a
        task_registry slot. Both the raw parse and the final computed
        delay stay at the ceiling."""
        exc = _rate_limit_error("Please try again in 9999999s.")
        # Raw hint is capped so any downstream consumer of
        # parse_retry_after also sees the sane value.
        assert parse_retry_after(exc) == pytest.approx(120.0)
        # Computed delay is at the ceiling (jitter doesn't push it over).
        delay = compute_backoff_delay(exc, attempt=0)
        assert delay == pytest.approx(120.0)

    def test_exponential_schedule_is_capped(self):
        """A high ``attempt`` must not blow past the ceiling either — the
        coordinator currently caps retries at 3 but a future budget bump
        shouldn't silently produce 256s sleeps."""
        exc = _rate_limit_error("No hint in this body.")
        # attempt=10 → 2**11 = 2048s without a cap. Must clamp to 120.
        delay = compute_backoff_delay(exc, attempt=10)
        assert delay == pytest.approx(120.0)


class TestSingleAgentRateLimitRetry:
    """The coordinator must (a) recognise 429s, (b) sleep before retrying,
    (c) grant a larger budget than the generic 1-retry cap, and (d) leave
    the generic budget untouched when failures are 429s."""

    @pytest.mark.asyncio
    async def test_429_then_success_backs_off_and_retries(self, tmp_path: Path):
        calls: list[int] = []

        async def flaky_invoke(**kwargs):
            calls.append(1)
            if len(calls) == 1:
                raise _rate_limit_error("Please try again in 745ms.")
            return _ok(str(tmp_path / "NOTES_ACC_POLICIES_filled.xlsx"))

        sleeps: list[float] = []

        async def fake_sleep(delay):
            sleeps.append(delay)

        with patch.object(coord_mod, "_invoke_single_notes_agent_once", side_effect=flaky_invoke), \
             patch.object(coord_mod.asyncio, "sleep", side_effect=fake_sleep):
            result = await _run_single_notes_agent(
                template_type=NotesTemplateType.ACC_POLICIES,
                pdf_path=str(tmp_path / "x.pdf"),
                inventory=[],
                filing_level="company",
                model="test",
                output_dir=str(tmp_path),
            )

        assert len(calls) == 2, "expected 1 retry after 429"
        assert result.status == "succeeded"
        # The backoff sleep must be at least the 2s floor — proves we did
        # NOT retry immediately.
        assert sleeps, "coordinator must sleep before retrying a 429"
        assert sleeps[0] >= 2.0

    @pytest.mark.asyncio
    async def test_429_budget_exceeds_generic_budget(self, tmp_path: Path):
        """Three consecutive 429s then success proves the RL budget is >= 3
        — the generic budget would have given up after 1 retry."""
        calls: list[int] = []

        async def always_429_then_ok(**kwargs):
            calls.append(1)
            if len(calls) <= 3:  # first 3 attempts fail with 429
                raise _rate_limit_error(f"Try again in {len(calls) * 100}ms.")
            return _ok(str(tmp_path / "NOTES_CORP_INFO_filled.xlsx"))

        with patch.object(coord_mod, "_invoke_single_notes_agent_once", side_effect=always_429_then_ok), \
             patch.object(coord_mod.asyncio, "sleep", new=_no_sleep):
            result = await _run_single_notes_agent(
                template_type=NotesTemplateType.CORP_INFO,
                pdf_path=str(tmp_path / "x.pdf"),
                inventory=[],
                filing_level="company",
                model="test",
                output_dir=str(tmp_path),
            )

        assert result.status == "succeeded"
        assert len(calls) == 4, "RL budget should permit 3 retries (4 total)"

    @pytest.mark.asyncio
    async def test_429s_exhaust_rate_limit_budget_and_fail(self, tmp_path: Path):
        """When every attempt is a 429, the sheet fails after
        ``RATE_LIMIT_MAX_RETRIES`` retries (4 total attempts) with the
        failures side-log noting the rate-limited flag."""
        calls: list[int] = []

        async def always_429(**kwargs):
            calls.append(1)
            raise _rate_limit_error(f"Try again in {len(calls) * 100}ms.")

        with patch.object(coord_mod, "_invoke_single_notes_agent_once", side_effect=always_429), \
             patch.object(coord_mod.asyncio, "sleep", new=_no_sleep):
            result = await _run_single_notes_agent(
                template_type=NotesTemplateType.RELATED_PARTY,
                pdf_path=str(tmp_path / "x.pdf"),
                inventory=[],
                filing_level="company",
                model="test",
                output_dir=str(tmp_path),
            )

        assert result.status == "failed"
        assert len(calls) == 1 + RATE_LIMIT_MAX_RETRIES

        log_path = tmp_path / "notes_RELATED_PARTY_failures.json"
        payload = json.loads(log_path.read_text(encoding="utf-8"))
        # Every attempt in the log should be flagged rate_limited=True so
        # operators can distinguish throttling from real failures.
        assert all(a.get("rate_limited") for a in payload["attempts"])

    @pytest.mark.asyncio
    async def test_generic_budget_preserved_when_429s_mixed_in(self, tmp_path: Path):
        """A 429 followed by a real error must NOT double-count: the 429
        shouldn't consume the generic budget, but the real error then
        gets exactly 1 retry before giving up."""
        calls: list[int] = []

        async def mixed_failures(**kwargs):
            calls.append(1)
            if len(calls) == 1:
                raise _rate_limit_error("Try again in 100ms.")
            if len(calls) == 2:
                raise RuntimeError("real error — first generic failure")
            if len(calls) == 3:
                raise RuntimeError("real error — second generic failure (budget exhausted)")
            return _ok(str(tmp_path / "ok.xlsx"))  # unreachable

        with patch.object(coord_mod, "_invoke_single_notes_agent_once", side_effect=mixed_failures), \
             patch.object(coord_mod.asyncio, "sleep", new=_no_sleep):
            result = await _run_single_notes_agent(
                template_type=NotesTemplateType.CORP_INFO,
                pdf_path=str(tmp_path / "x.pdf"),
                inventory=[],
                filing_level="company",
                model="test",
                output_dir=str(tmp_path),
            )

        assert result.status == "failed"
        # 1 (429) + 1 (generic) + 1 (generic, exhausted) = 3 attempts.
        assert len(calls) == 3
        assert "second generic failure" in (result.error or "")

    @pytest.mark.asyncio
    async def test_cancellation_during_backoff_propagates(self, tmp_path: Path):
        """If the user aborts while we're sleeping before a 429 retry, the
        CancelledError must bubble through to a cancelled result — the
        sleep itself is a cancellation point."""
        calls: list[int] = []

        async def always_429(**kwargs):
            calls.append(1)
            raise _rate_limit_error("Try again in 10s.")

        async def cancelling_sleep(delay):
            raise asyncio.CancelledError()

        with patch.object(coord_mod, "_invoke_single_notes_agent_once", side_effect=always_429), \
             patch.object(coord_mod.asyncio, "sleep", side_effect=cancelling_sleep):
            result = await _run_single_notes_agent(
                template_type=NotesTemplateType.CORP_INFO,
                pdf_path=str(tmp_path / "x.pdf"),
                inventory=[],
                filing_level="company",
                model="test",
                output_dir=str(tmp_path),
            )

        assert result.status == "cancelled"
        assert len(calls) == 1, "must not retry past a cancellation"


class TestLaunchStagger:
    """The coordinator staggers parallel notes-agent launches by
    ``NOTES_LAUNCH_STAGGER_SECS`` per index. Verify that a non-zero
    ``launch_delay`` is honoured at the top of the runner — concrete
    behaviour that the top-level stagger relies on."""

    @pytest.mark.asyncio
    async def test_launch_delay_sleeps_before_first_attempt(self, tmp_path: Path):
        sleeps: list[float] = []

        async def fake_sleep(delay):
            sleeps.append(delay)

        async def ok_invoke(**kwargs):
            # Assert the sleep ran BEFORE we got here — otherwise the
            # stagger is cosmetic and doesn't actually delay the request.
            assert sleeps == [1.6], f"expected stagger before first call, got {sleeps}"
            return _ok(str(tmp_path / "ok.xlsx"))

        with patch.object(coord_mod, "_invoke_single_notes_agent_once", side_effect=ok_invoke), \
             patch.object(coord_mod.asyncio, "sleep", side_effect=fake_sleep):
            result = await _run_single_notes_agent(
                template_type=NotesTemplateType.CORP_INFO,
                pdf_path=str(tmp_path / "x.pdf"),
                inventory=[],
                filing_level="company",
                model="test",
                output_dir=str(tmp_path),
                launch_delay=1.6,
            )

        assert result.status == "succeeded"

    @pytest.mark.asyncio
    async def test_zero_launch_delay_does_not_sleep(self, tmp_path: Path):
        """``launch_delay=0`` is the single-agent default — must not fire
        any spurious sleep that would surprise callers."""
        sleeps: list[float] = []

        async def fake_sleep(delay):
            sleeps.append(delay)

        async def ok_invoke(**kwargs):
            return _ok(str(tmp_path / "ok.xlsx"))

        with patch.object(coord_mod, "_invoke_single_notes_agent_once", side_effect=ok_invoke), \
             patch.object(coord_mod.asyncio, "sleep", side_effect=fake_sleep):
            await _run_single_notes_agent(
                template_type=NotesTemplateType.CORP_INFO,
                pdf_path=str(tmp_path / "x.pdf"),
                inventory=[],
                filing_level="company",
                model="test",
                output_dir=str(tmp_path),
                launch_delay=0.0,
            )

        assert sleeps == [], "no sleep expected when launch_delay=0"


async def _no_sleep(_delay):
    """Drop-in replacement for ``asyncio.sleep`` used in retry tests so
    we don't actually pause for backoff math during unit runs."""
    return None
