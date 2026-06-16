"""Item 32 (32c): the DB-rendered (process-cached) read_template summary.

These tests pin the load-bearing contract of Phase 3: the cached summary string
the agent's ``read_template`` tool serves is **byte-identical** to the legacy
per-call xlsx parse, the cache memoises by ``template_id`` (no re-parse on a
second call), the flag toggles the behaviour, and the cached string still trips
the downstream compaction matcher (``_is_template_summary``).
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from extraction import agent as agent_mod
from extraction.agent import (
    _render_template_summary,
    _summarize_template,
    _TEMPLATE_SUMMARY_CACHE,
)
from extraction.history_processors import _is_template_summary
from pydantic_ai.messages import ToolReturnPart
from tools.template_reader import read_template as _read_template_impl

_ROOT = Path(__file__).resolve().parent.parent


def _all_template_paths() -> list[Path]:
    """Every shipped Company/Group template across both filing standards."""
    paths: list[Path] = []
    for standard in ("XBRL-template-MFRS", "XBRL-template-MPERS"):
        for level in ("Company", "Group"):
            paths.extend(sorted((_ROOT / standard / level).glob("*.xlsx")))
    return paths


def _deps(template_path: str, template_id: str | None):
    """A minimal stand-in for ExtractionDeps — _render_template_summary only
    reads template_id / template_fields / template_path."""
    return SimpleNamespace(
        template_path=template_path,
        template_id=template_id,
        template_fields=[],
    )


@pytest.fixture(autouse=True)
def _clear_cache():
    """Keep the process-global cache from leaking across tests."""
    _TEMPLATE_SUMMARY_CACHE.clear()
    yield
    _TEMPLATE_SUMMARY_CACHE.clear()


@pytest.fixture
def _flag_on(monkeypatch):
    monkeypatch.setenv("XBRL_DB_READ_TEMPLATE", "1")


@pytest.fixture
def _flag_off(monkeypatch):
    monkeypatch.setenv("XBRL_DB_READ_TEMPLATE", "0")


def test_cached_summary_is_byte_identical_for_every_template(_flag_on):
    """Step 3.1 verify: the cached string equals the live _summarize_template
    output for every template family — no field-by-field reconstruction drift."""
    for path in _all_template_paths():
        expected = _summarize_template(_read_template_impl(str(path)))
        # Synthetic template_id keyed off the path — distinct per file, mirrors
        # the 1:1 file↔template_id relationship.
        template_id = f"{path.parent.parent.name}/{path.parent.name}/{path.stem}"
        cached = _render_template_summary(_deps(str(path), template_id))
        assert cached == expected, f"summary drift for {path}"


def test_second_call_does_not_reparse_the_workbook(_flag_on, monkeypatch):
    """Step 3.1 verify: memoisation — a second call for the same template_id is
    served from cache and never re-opens the xlsx."""
    path = str(_ROOT / "XBRL-template-MFRS" / "Company" / "01-SOFP-CuNonCu.xlsx")
    first = _render_template_summary(_deps(path, "mfrs-company-sofp"))

    # After the first build, any further parse attempt is a bug.
    def _boom(*_a, **_k):  # pragma: no cover - only fires on regression
        raise AssertionError("read_template re-parsed the workbook on a cache hit")

    monkeypatch.setattr(agent_mod, "_read_template_impl", _boom)
    second = _render_template_summary(_deps(path, "mfrs-company-sofp"))
    assert second == first
    # Cache is keyed by (template_id, mtime) so a regenerated file self-invalidates.
    assert any(k[0] == "mfrs-company-sofp" for k in _TEMPLATE_SUMMARY_CACHE)


def test_flag_off_uses_legacy_per_deps_parse(_flag_off):
    """Step 3.4: with the flag off, the process cache is never populated; the
    summary still renders via the legacy per-deps parse."""
    path = str(_ROOT / "XBRL-template-MFRS" / "Company" / "01-SOFP-CuNonCu.xlsx")
    expected = _summarize_template(_read_template_impl(path))
    out = _render_template_summary(_deps(path, "mfrs-company-sofp"))
    assert out == expected
    assert _TEMPLATE_SUMMARY_CACHE == {}


def test_missing_template_id_falls_through_to_legacy_path(_flag_on):
    """No template_id (some CLI paths) → graceful fall-through, no cache entry."""
    path = str(_ROOT / "XBRL-template-MFRS" / "Company" / "01-SOFP-CuNonCu.xlsx")
    expected = _summarize_template(_read_template_impl(path))
    out = _render_template_summary(_deps(path, None))
    assert out == expected
    assert _TEMPLATE_SUMMARY_CACHE == {}


def test_cached_summary_still_matches_compaction_marker(_flag_on):
    """Step 3.2: the cached string must still be recognised as a template
    summary so strip_duplicate_template keeps compacting it."""
    path = str(_ROOT / "XBRL-template-MFRS" / "Company" / "01-SOFP-CuNonCu.xlsx")
    cached = _render_template_summary(_deps(path, "mfrs-company-sofp"))
    part = ToolReturnPart(tool_name="read_template", content=cached, tool_call_id="x")
    assert _is_template_summary(part)


def test_no_new_marker_tokens_vs_legacy(_flag_on):
    """Step 3.3: parity only — the cached summary introduces no new tokens (no
    mandatory markers etc.) beyond the legacy output. Byte-equality already
    proves this; asserted explicitly as the durable fence."""
    path = str(_ROOT / "XBRL-template-MFRS" / "Company" / "01-SOFP-CuNonCu.xlsx")
    legacy = _summarize_template(_read_template_impl(path))
    cached = _render_template_summary(_deps(path, "mfrs-company-sofp"))
    assert cached == legacy
