"""CodeMode spike seam (docs/PLAN-codemode-spike.md) — default OFF.

One place owns every CodeMode decision so the experiment stays deletable in a
single commit: the env toggle, the statement allowlist, the sandboxed-tool
subset, the sequential (stateful) trio, and the prompt addendum. The harness
package (`pydantic-ai-harness[code-mode]`, an exactly-pinned optional
dependency from requirements-codemode.txt) is imported ONLY inside
`build_code_mode_capability()` — with the flag off it never enters the
process, which is what keeps flag-off runs byte-identical to today
(pinned by tests/test_code_mode_seam.py).
"""
from __future__ import annotations

import functools
import inspect
import os
import time
from typing import Any, Callable, Optional

# Tools that move INSIDE the run_code sandbox when the flag is on. Text-only
# by design: view_pdf_pages returns page IMAGES the model must see with its
# own eyes, and read_template's value is provider prompt-cache reuse — both
# stay ordinary tool calls. submit_face_coverage is conditionally registered
# (scout refs present); the CodeMode selector treats a non-registered name as
# simply not matching, so listing it unconditionally is harmless.
SANDBOX_TOOL_NAMES: list[str] = [
    "calculator",
    "lookup_definitions",
    "search_pdf_text",
    "write_facts",
    "verify_totals",
    "save_result",
    "submit_face_coverage",
]

# The stateful trio: they mutate shared state (workbook file,
# deps.last_verify_result, the save gate) and must never overlap. Registered
# with sequential=True when the flag is on — the harness renders sequential
# tools as SYNC functions inside the sandbox, so a script cannot even attempt
# to asyncio.gather them (verified live, plan Step 2 findings).
SEQUENTIAL_TOOL_NAMES: frozenset[str] = frozenset(
    {"write_facts", "verify_totals", "save_result"}
)

# Marker string doubles as the test hook for "addendum present/absent".
CODE_MODE_PROMPT_MARKER = "=== CODE MODE (run_code) ==="

CODE_MODE_PROMPT_ADDENDUM = f"""

{CODE_MODE_PROMPT_MARKER}
Several tools are available ONLY as Python functions inside the `run_code`
sandbox (their signatures are listed in the run_code function catalog).
Rules for using it well:

- Calls are KEYWORD-ONLY: `write_facts(facts=[...])`, never
  `write_facts([...])`. The sandbox type-checks your script before running
  it and rejects positional calls.
- `write_facts`, `verify_totals` and `save_result` are synchronous — call
  them in order, do NOT await or gather them. Batchable read tools
  (`calculator`, `lookup_definitions`, `search_pdf_text`) are async and may
  be gathered.
- Combine the finishing chain in ONE script: write_facts, then
  verify_totals, then — only if verification reports balanced and no
  mandatory rows unfilled — save_result. If verification fails, print the
  verify output and END the script so you can re-examine the PDF pages
  before deciding on corrections. Never loop blindly re-writing values to
  force a balance (the no-plug rule applies inside scripts too).
- `view_pdf_pages` and `read_template` remain normal tools OUTSIDE the
  sandbox — you must look at pages yourself; a script cannot read images.
- If a script fails partway, any write_facts that already succeeded HAS
  been applied — the stdout shown with the error tells you how far it got.
  Re-check state (verify_totals) instead of assuming a clean slate.
"""

_TRUTHY = ("1", "true", "yes", "on")


def code_mode_enabled(statement_value: str) -> bool:
    """Whether the CodeMode spike is on for this statement.

    Read at call time (repo kill-switch convention) so tests can toggle via
    the environment. ``statement_value`` is the StatementType.value string,
    matched case-insensitively against the comma-separated
    ``XBRL_CODE_MODE_STATEMENTS`` allowlist (default: SOFP only).
    """
    if os.environ.get("XBRL_CODE_MODE", "0").strip().lower() not in _TRUTHY:
        return False
    allow = os.environ.get("XBRL_CODE_MODE_STATEMENTS", "SOFP")
    allowed = {s.strip().upper() for s in allow.split(",") if s.strip()}
    return statement_value.upper() in allowed


def build_code_mode_capability():
    """Lazy-import the harness and build the CodeMode capability.

    Import stays inside this function so the optional dependency is never
    loaded flag-off. A missing package is a friendly config error, not an
    ImportError traceback mid-run.
    """
    try:
        from pydantic_ai_harness import CodeMode
    except ImportError as exc:  # pragma: no cover - environment-dependent
        raise RuntimeError(
            "XBRL_CODE_MODE is on but the optional CodeMode dependency is "
            "not installed. Install it with:\n"
            "  ./venv/bin/pip install -r requirements-codemode.txt\n"
            "or turn the flag off (unset XBRL_CODE_MODE)."
        ) from exc
    return CodeMode(tools=list(SANDBOX_TOOL_NAMES))


# ---------------------------------------------------------------------------
# Host-side call ledger (plan Phase 3) — the debug-telemetry replacement.
#
# Under CodeMode the per-tool SSE stream and run_agent_turns rows collapse to
# one `run_code` blob per script; this ledger is the agreed substitute. Each
# sandbox-callable tool is wrapped with `ledgered`, which appends one entry
# per invocation — in a `finally`, so FAILED calls land too. The harness's
# own ToolReturnPart.metadata records nested call structure for SUCCESSFUL
# scripts; the ledger's job is exactly what that lacks: durations and
# failure records (Step 2 findings).
# ---------------------------------------------------------------------------

_SUMMARY_CAP = 300


def _truncate(value: Any, cap: int = _SUMMARY_CAP) -> str:
    text = str(value)
    return text if len(text) <= cap else text[: cap - 1] + "…"


def ledgered(fn: Callable) -> Callable:
    """Wrap a sandbox-callable tool so every invocation lands in
    ``ctx.deps.code_mode_ledger``.

    No-op when the ledger is None (flag off — the deps field is only
    initialised on CodeMode runs), so the flag-off path gains nothing but a
    cheap attribute check. ``functools.wraps`` preserves the signature, so
    pydantic-ai's schema generation sees the original function unchanged.
    """
    if inspect.iscoroutinefunction(fn):  # pragma: no cover - all current
        # sandbox tools are sync; kept so a future async tool isn't
        # silently unledgered.
        @functools.wraps(fn)
        async def awrapper(ctx, *args, **kwargs):
            ledger = getattr(ctx.deps, "code_mode_ledger", None)
            if ledger is None:
                return await fn(ctx, *args, **kwargs)
            start = time.monotonic()
            outcome, error, result = "ok", None, None
            try:
                result = await fn(ctx, *args, **kwargs)
                return result
            except BaseException as exc:
                outcome, error = "error", f"{type(exc).__name__}: {exc}"
                raise
            finally:
                _append_entry(ledger, ctx, fn, args, kwargs, start,
                              outcome, error, result)
        return awrapper

    @functools.wraps(fn)
    def wrapper(ctx, *args, **kwargs):
        ledger = getattr(ctx.deps, "code_mode_ledger", None)
        if ledger is None:
            return fn(ctx, *args, **kwargs)
        start = time.monotonic()
        outcome, error, result = "ok", None, None
        try:
            result = fn(ctx, *args, **kwargs)
            return result
        except BaseException as exc:
            outcome, error = "error", f"{type(exc).__name__}: {exc}"
            raise
        finally:
            _append_entry(ledger, ctx, fn, args, kwargs, start,
                          outcome, error, result)
    return wrapper


def _append_entry(ledger, ctx, fn, args, kwargs, start,
                  outcome, error, result) -> None:
    # Runs inside the wrappers' `finally`: an exception here would convert a
    # successful tool call into a failure (or REPLACE the tool's real
    # exception), so recording is strictly best-effort — a failing
    # `__str__`/`__repr__` on an arg or result must cost only the ledger
    # entry, never the call.
    try:
        arg_bits = [_truncate(a) for a in args]
        arg_bits += [f"{k}={_truncate(v)}" for k, v in kwargs.items()]
        ledger.append({
            "tool": fn.__name__,
            # The harness mints nested call ids shaped `pyd_ai_<hex>__<seq>`
            # with a fresh hex per run_code invocation — prefix identifies the
            # script, suffix the call order (group_code_mode_ledger parses it).
            "tool_call_id": getattr(ctx, "tool_call_id", None),
            "args_summary": _truncate(", ".join(arg_bits)),
            "duration_ms": int((time.monotonic() - start) * 1000),
            "outcome": outcome,
            "error": error,
            "result_summary": _truncate(result) if outcome == "ok" else None,
        })
    except Exception:  # noqa: BLE001 — recording is advisory
        import logging
        logging.getLogger(__name__).debug(
            "code-mode ledger entry skipped for %s", fn.__name__
        )


def group_code_mode_ledger(entries: Optional[list]) -> list[dict]:
    """Derive per-script grouping for the trace file.

    Adds ``run_code_call_id`` (the shared nested-id prefix), ``seq`` (call
    order inside the script) and ``script_turn_index`` (1-based, in
    first-seen order — a retry's script gets its own index). Entries whose
    id doesn't parse keep positional order under a None group.
    """
    grouped: list[dict] = []
    first_seen: dict[str, int] = {}
    for pos, entry in enumerate(entries or [], start=1):
        call_id = entry.get("tool_call_id") or ""
        prefix, sep, suffix = call_id.rpartition("__")
        if sep and suffix.isdigit():
            group_id, seq = prefix, int(suffix)
        else:
            group_id, seq = None, pos
        key = group_id or "?"
        if key not in first_seen:
            first_seen[key] = len(first_seen) + 1
        grouped.append({
            **entry,
            "run_code_call_id": group_id,
            "seq": seq,
            "script_turn_index": first_seen[key],
        })
    return grouped


def ledger_echo(new_entries: list) -> str:
    """One-line live summary of a script's tool activity, appended to the
    `run_code` tool_result SSE event so the UI's single blob still says
    what happened inside."""
    if not new_entries:
        return ""
    bits = []
    for e in new_entries:
        mark = "ok" if e.get("outcome") == "ok" else f"FAILED({e.get('error')})"
        bits.append(f"{e.get('tool')}:{mark}")
    return " | script calls: " + ", ".join(bits)
