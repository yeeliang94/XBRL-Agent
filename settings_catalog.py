"""Advanced operator settings managed from the Settings page.

Each entry names an environment variable that a pipeline module already reads.
The Settings page saves overrides to the local runtime settings file
(`runtime_settings.py`), which is applied to `os.environ` at startup and before
every run, so the readers need no change. `.env` and the machine environment
remain the fallback when a key has not been saved from the page.

Not listed here, by design:

- Secrets and sign-in: provider API keys, `LLM_PROXY_API_KEY`,
  `SESSION_SECRET`, `AUTH_*`, `BOOTSTRAP_ADMIN_*`.
- Values needed before the settings file can be found or that describe this
  machine: `PORT`, `XBRL_OUTPUT_DIR`, `XBRL_SETTINGS_FILE`, `XBRL_APP_LOG_*`,
  `XBRL_APP_VERSION`, `XBRL_SOFFICE_PATH`, `XBRL_DOCX_CONVERTER`.
- Test fixtures (`MPERS_TEST_PDF`, `FINCO_PDF_PATH`) and the legacy
  `SCOUT_MODEL` fallback, which the per-role model setting replaces.

`restart=True` marks a value read once at process start (a module constant or
startup hook). It is saved immediately but takes effect after a restart.
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import Any, Optional

from runtime_settings import deployment_value


_TRUE = ("1", "true", "yes", "on")
_FALSE = ("0", "false", "no", "off")


@dataclass(frozen=True)
class AdvancedSetting:
    key: str
    label: str
    help: str
    group: str
    kind: str  # "bool" | "int" | "float" | "choice"
    default: Any
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    choices: tuple[str, ...] = ()
    restart: bool = False

    def parse_env(self, raw: Optional[str]) -> Any:
        """The value the pipeline reader would act on, for display."""
        if raw is None or raw.strip() == "":
            return self.default
        text = raw.strip()
        if self.kind == "bool":
            lowered = text.lower()
            if lowered in _TRUE:
                return True
            if lowered in _FALSE:
                return False
            return self.default
        if self.kind == "choice":
            match = next((c for c in self.choices if c.lower() == text.lower()), None)
            return match if match is not None else self.default
        try:
            number = float(text)
        except ValueError:
            return self.default
        # "nan"/"inf" parse as floats but are not valid JSON; a hand-edited
        # .env must not break GET /api/settings.
        if not math.isfinite(number):
            return self.default
        if self.kind == "int":
            return int(number) if number.is_integer() else self.default
        return number

    def to_env(self, value: Any) -> str:
        """Validate a submitted value and return the stored string.

        Raises ValueError with an operator-readable message.
        """
        if self.kind == "bool":
            if not isinstance(value, bool):
                raise ValueError(f"{self.label} must be on or off.")
            return "1" if value else "0"
        if self.kind == "choice":
            text = str(value).strip()
            match = next((c for c in self.choices if c.lower() == text.lower()), None)
            if match is None:
                raise ValueError(
                    f"{self.label} must be one of {', '.join(self.choices)}."
                )
            return match
        if isinstance(value, bool):
            raise ValueError(f"{self.label} must be a number.")
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"{self.label} must be a number.") from None
        if not math.isfinite(number):
            raise ValueError(f"{self.label} must be a number.")
        if self.kind == "int" and not number.is_integer():
            raise ValueError(f"{self.label} must be a whole number.")
        if self.minimum is not None and number < self.minimum:
            raise ValueError(f"{self.label} must be at least {_fmt(self.minimum)}.")
        if self.maximum is not None and number > self.maximum:
            raise ValueError(f"{self.label} must be at most {_fmt(self.maximum)}.")
        return str(int(number)) if self.kind == "int" else str(number)


def _fmt(number: float) -> str:
    return str(int(number)) if float(number).is_integer() else str(number)


_LIMITS = "Time and turn limits"
_CHECKS = "Checks and verification"
_REVIEW = "Reviewer and notes formatter"
_EFFICIENCY = "Context and efficiency"
_PROVIDER = "AI provider compatibility"
_HOUSEKEEPING = "Diagnostics and housekeeping"

ADVANCED_SETTINGS: tuple[AdvancedSetting, ...] = (
    # --- Time and turn limits -------------------------------------------
    AdvancedSetting(
        "XBRL_MAX_AGENT_ITERATIONS", "Maximum turns per extraction agent",
        "Model responses one statement agent may use before it stops. Capped "
        "at 45 so the app's own limit fires before the framework's hidden 50.",
        _LIMITS, "int", 40, minimum=1, maximum=45, restart=True,
    ),
    AdvancedSetting(
        "XBRL_MAX_TOKENS_PER_AGENT", "Token budget per agent",
        "Total tokens one agent may use before it is stopped. 0 means no budget.",
        _LIMITS, "int", 0, minimum=0,
    ),
    AdvancedSetting(
        "XBRL_FACE_WALLCLOCK_S", "Statement agent time limit (seconds)",
        "Longest one statement agent may run. 0 means no limit.",
        _LIMITS, "float", 1800.0, minimum=0, restart=True,
    ),
    AdvancedSetting(
        "XBRL_CORRECTION_WALLCLOCK_S", "Reviewer time limit (seconds)",
        "Longest the reviewer pass may run. 0 means no limit.",
        _LIMITS, "float", 300.0, minimum=0, restart=True,
    ),
    AdvancedSetting(
        "XBRL_NOTES_VALIDATOR_WALLCLOCK_S", "Notes reviewer time limit (seconds)",
        "Longest the notes reviewer may run. 0 means no limit.",
        _LIMITS, "float", 300.0, minimum=0, restart=True,
    ),
    AdvancedSetting(
        "XBRL_NOTES_FORMATTER_WALLCLOCK_S", "Notes formatter time limit (seconds)",
        "Longest the notes formatter may run. 0 means no limit.",
        _LIMITS, "float", 300.0, minimum=0, restart=True,
    ),
    AdvancedSetting(
        "XBRL_CROSS_CHECK_TIMEOUT_S", "Cross-check time limit (seconds)",
        "Longest one cross-check pass may run. 0 means no limit.",
        _LIMITS, "float", 120.0, minimum=0, restart=True,
    ),
    AdvancedSetting(
        "XBRL_NOTES12_TURN_TIMEOUT_S", "List-of-notes turn time limit (seconds)",
        "Longest one model turn may take while writing the list of notes.",
        _LIMITS, "float", 180.0, minimum=1, restart=True,
    ),
    AdvancedSetting(
        "XBRL_NOTES12_FANOUT_TIMEOUT_S", "List-of-notes worker time limit (seconds)",
        "Longest one list-of-notes worker may run per attempt.",
        _LIMITS, "float", 420.0, minimum=1, restart=True,
    ),
    AdvancedSetting(
        "XBRL_MAX_CONCURRENT_AGENTS", "Agents running at once",
        "How many agents may call the AI service at the same time. 0 means no "
        "limit. Lower it if the AI service rate-limits runs.",
        _LIMITS, "int", 0, minimum=0,
    ),
    AdvancedSetting(
        "XBRL_NOTES_FORMATTER_MAX_REQUESTS", "Maximum notes formatter turns",
        "Model responses the notes formatter may use per sheet. Capped at 45.",
        _LIMITS, "int", 16, minimum=1, maximum=45,
    ),
    # --- Checks and verification ----------------------------------------
    AdvancedSetting(
        "XBRL_FACT_BASED_CHECKS", "Cross-check from extracted facts",
        "Runs cross-checks on the saved facts. Turn off only to fall back to "
        "reading the workbook.",
        _CHECKS, "bool", True,
    ),
    AdvancedSetting(
        "XBRL_FACT_BASED_VERIFY", "Verify totals from extracted facts",
        "Verifies statement totals on the saved facts. Turn off only to fall "
        "back to workbook formulas.",
        _CHECKS, "bool", True,
    ),
    AdvancedSetting(
        "XBRL_DB_READ_TEMPLATE", "Reuse cached template summaries",
        "Agents read a stored template summary instead of re-reading the "
        "workbook on every call.",
        _CHECKS, "bool", True,
    ),
    AdvancedSetting(
        "XBRL_WRITE_FRESHNESS", "Warn on overwritten note rows",
        "advisory flags a note row that is written twice with different "
        "content; off silences it.",
        _CHECKS, "choice", "advisory", choices=("advisory", "off"),
    ),
    AdvancedSetting(
        "XBRL_LIMIT_WARNINGS", "Warn agents near their limits",
        "Tells an agent when it is close to its turn or time limit.",
        _CHECKS, "bool", True,
    ),
    # --- Reviewer and notes formatter -----------------------------------
    AdvancedSetting(
        "XBRL_REVIEWER_COMPACT_CONTEXT", "Compact reviewer history",
        "Removes old page images from the reviewer's history to reduce usage.",
        _REVIEW, "bool", False,
    ),
    AdvancedSetting(
        "XBRL_REVIEWER_INVESTIGATION_BUNDLE", "Reviewer batch investigation tool",
        "Lets the reviewer run several read-only checks in one call.",
        _REVIEW, "bool", False,
    ),
    AdvancedSetting(
        "XBRL_NOTES_FORMATTER_STRUCTURED", "Structured notes formatter output",
        "The formatter returns a declared data shape instead of free text. "
        "Turn off if formatting quality drops.",
        _REVIEW, "bool", True,
    ),
    # --- Context and efficiency ------------------------------------------
    AdvancedSetting(
        "XBRL_TEMPLATE_IN_PROMPT", "Put the template in the agent's instructions",
        "Statement agents receive the template up front instead of reading it "
        "with a tool call.",
        _EFFICIENCY, "bool", False,
    ),
    AdvancedSetting(
        "XBRL_TEMPLATE_SUMMARY_COMPACT", "Compact template summaries",
        "Shows agents one line per template row instead of one per cell.",
        _EFFICIENCY, "bool", False,
    ),
    AdvancedSetting(
        "XBRL_SOFT_COMPACT_TOKENS", "History trimming threshold (tokens)",
        "After this many tokens, older tool results are trimmed more "
        "aggressively. 0 turns this off.",
        _EFFICIENCY, "int", 60000, minimum=0,
    ),
    AdvancedSetting(
        "XBRL_SOFT_COMPACT_OUTBOUND_CHARS", "History trimming by request size (characters)",
        "Also trims when a single request's text exceeds this size. 0 turns "
        "this off.",
        _EFFICIENCY, "int", 0, minimum=0,
    ),
    AdvancedSetting(
        "XBRL_COMPACT_MIN_RECLAIM_CHARS", "Minimum saving to trim history (characters)",
        "Skips a trim that would save less than this. 0 turns this off.",
        _EFFICIENCY, "int", 0, minimum=0,
    ),
    AdvancedSetting(
        "XBRL_CLAMP_MAX_PART_CHARS", "Cap on one oversized message (characters)",
        "Shortens any single message part larger than this. 0 turns this off.",
        _EFFICIENCY, "int", 40000, minimum=0,
    ),
    AdvancedSetting(
        "XBRL_PAGE_CACHE_MAX", "Rendered page cache size",
        "How many rendered PDF pages are kept in memory.",
        _EFFICIENCY, "int", 64, minimum=1, maximum=4096, restart=True,
    ),
    AdvancedSetting(
        "XBRL_PDF_SIDECAR_PAGE_CAP", "PDF transcription page limit",
        "Most pages one PDF transcription pass may read. Each page is a paid "
        "call.",
        _EFFICIENCY, "int", 80, minimum=1,
    ),
    # --- AI provider compatibility ---------------------------------------
    AdvancedSetting(
        "XBRL_OPENAI_CACHE_OPTIONS", "Newer GPT-5.6 cache request format",
        "Uses OpenAI's newer prompt-cache request field. Turn on only after a "
        "live run confirms it.",
        _PROVIDER, "bool", False,
    ),
    AdvancedSetting(
        "XBRL_ALLOW_GEMINI_PROXY", "Allow Gemini 3 through the proxy",
        "Gemini 3 usually fails on its second tool call through this proxy. "
        "Turn on only after the proxy is confirmed to handle it.",
        _PROVIDER, "bool", False,
    ),
    # --- Diagnostics and housekeeping ------------------------------------
    AdvancedSetting(
        "XBRL_LOG_LEVEL", "Log detail",
        "How much the server writes to its log.",
        _HOUSEKEEPING, "choice", "INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"), restart=True,
    ),
    AdvancedSetting(
        "XBRL_CACHE_PROBE", "Log prompt-cache details",
        "Writes prompt-cache measurements at normal log detail.",
        _HOUSEKEEPING, "bool", False,
    ),
    AdvancedSetting(
        "XBRL_TRACE_RETENTION_DAYS", "Keep run traces for (days)",
        "Traces contain document and model content. 0 keeps them forever; use "
        "it only when another system deletes them.",
        _HOUSEKEEPING, "int", 90, minimum=0, restart=True,
    ),
    AdvancedSetting(
        "XBRL_SSE_KEEPALIVE_S", "Live-progress keep-alive (seconds)",
        "How often an idle progress stream sends a heartbeat so network "
        "proxies do not close it.",
        _HOUSEKEEPING, "float", 25.0, minimum=1,
    ),
    AdvancedSetting(
        "XBRL_STAGE_RESUME", "Resume failed runs from the command line",
        "Lets the command-line resume tool relaunch a run from its last "
        "completed stage. When off it only previews.",
        _HOUSEKEEPING, "bool", False,
    ),
)

ADVANCED_BY_KEY: dict[str, AdvancedSetting] = {s.key: s for s in ADVANCED_SETTINGS}


def describe(locally_saved: dict[str, str]) -> list[dict[str, Any]]:
    """Every advanced setting with its current effective value."""
    return [
        {
            "key": s.key,
            "label": s.label,
            "help": s.help,
            "group": s.group,
            "kind": s.kind,
            "default": s.default,
            "min": s.minimum,
            "max": s.maximum,
            "choices": list(s.choices),
            "restart": s.restart,
            "value": s.parse_env(os.environ.get(s.key)),
            # What "Use default" restores: the .env value, else the default.
            "fallback": s.parse_env(deployment_value(s.key)),
            "saved_here": s.key in locally_saved,
        }
        for s in ADVANCED_SETTINGS
    ]


def validate_updates(raw: Any) -> dict[str, Optional[str]]:
    """Turn `{KEY: value | null}` into settings-file updates.

    `null` removes the saved value so `.env` or the built-in default applies.
    Raises ValueError on an unknown key or an invalid value.
    """
    if not isinstance(raw, dict):
        raise ValueError("advanced_settings must be an object keyed by setting name.")
    updates: dict[str, Optional[str]] = {}
    for key, value in raw.items():
        setting = ADVANCED_BY_KEY.get(key)
        if setting is None:
            raise ValueError(f"Unknown advanced setting: {key!r}.")
        updates[key] = None if value is None else setting.to_env(value)
    return updates
