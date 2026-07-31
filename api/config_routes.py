"""Config / settings / connection-test routes.

Endpoints: ``/api/config``, ``/api/settings`` (GET+POST), ``/api/test-connection``.
Handlers read shared state/helpers through ``server.X`` so the test
monkeypatch surface (``server.ENV_FILE``, ``server._create_proxy_model``,
``server._load_available_models`` …) keeps working.
"""
import json
import logging
import os
import time

from dotenv import load_dotenv, set_key
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

import re

import server
from auth import routes as auth_routes
from db.repository import db_session

logger = logging.getLogger("server")

router = APIRouter()


def _app_version() -> str:
    """The running build's version string. Prod build stamps are cached; on a
    dev checkout it is git-derived behind a short TTL (utils.app_version)."""
    from utils.app_version import get_app_version

    return get_app_version()

# AI-plumbing + firm-wide run defaults that only an administrator may change
# (docs/PLAN-ui-ux-plain-language-overhaul.md Phase 6). The UI renders these
# read-only for non-admins, but the server is the real boundary: a write that
# touches any of these keys from a non-admin is refused. Cosmetic/firm-default
# keys not in this set (e.g. notes_table_style) stay writable.
_ADMIN_ONLY_SETTINGS_KEYS = frozenset({
    "model",
    "api_key",
    "proxy_url",
    "default_models",
    "scout_enabled_default",
    "auto_review",
    "notes_auto_review",
    "spot_check",
    "spot_check_mode",
    "notes_coverage",
    "entity_memory",
    "tolerance_rm",
})

# Notes-table style theme validation (docs/PLAN-notes-table-theme.md). Mirrors
# the frontend `parseThemeOptions` (clipboardFormat.ts) + the sanitiser colour
# rule, so a value that survived the form is accepted and a tampered payload
# fails loudly (400) rather than landing broken CSS in .env.
_HEX_COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
_BORDER_STYLES = {"none", "single", "double"}
_LIST_MARKERS = {"disc", "dash", "decimal"}


def _valid_theme_color(value) -> bool:
    return isinstance(value, str) and (
        value.strip().lower() == "transparent"
        or bool(_HEX_COLOR_RE.match(value.strip()))
    )


def _validate_notes_table_style(raw) -> dict:
    """Validate + clean an incoming theme object. Returns the cleaned dict (only
    known, in-range keys) or raises HTTPException(400) on a malformed field.
    Unknown keys are dropped so the persisted shape stays auditable."""
    if not isinstance(raw, dict):
        raise HTTPException(
            status_code=400, detail="notes_table_style must be an object."
        )
    cleaned: dict = {}
    if "borderStyle" in raw:
        if raw["borderStyle"] not in _BORDER_STYLES:
            raise HTTPException(
                status_code=400,
                detail=f"notes_table_style.borderStyle must be one of {sorted(_BORDER_STYLES)}.",
            )
        cleaned["borderStyle"] = raw["borderStyle"]
    for key, lo, hi in (
        ("fontSizePt", 6, 24),
        ("paragraphSpacingPx", 0, 48),
        # Prose theme fields (notes house style, item 1). Optional — absent
        # keeps each surface's historic default; validated only when present.
        ("headingSizePt", 6, 24),
        ("headingWeight", 400, 800),
    ):
        if key in raw:
            v = raw[key]
            if not isinstance(v, (int, float)) or isinstance(v, bool) or not (lo <= v <= hi):
                raise HTTPException(
                    status_code=400,
                    detail=f"notes_table_style.{key} must be a number in [{lo}, {hi}].",
                )
            cleaned[key] = v
    if "cellPaddingPx" in raw:
        pad = raw["cellPaddingPx"]
        if (
            not isinstance(pad, list)
            or len(pad) != 2
            or any(
                not isinstance(x, (int, float)) or isinstance(x, bool) or not (0 <= x <= 32)
                for x in pad
            )
        ):
            raise HTTPException(
                status_code=400,
                detail="notes_table_style.cellPaddingPx must be [vertical, horizontal] in [0, 32].",
            )
        cleaned["cellPaddingPx"] = pad
    for color_key in ("borderColor", "headerFill"):
        if color_key in raw and raw[color_key] is not None:
            if not _valid_theme_color(raw[color_key]):
                raise HTTPException(
                    status_code=400,
                    detail=f"notes_table_style.{color_key} must be a hex colour or 'transparent'.",
                )
            cleaned[color_key] = raw[color_key].strip().lower()
    if "headerBold" in raw:
        if not isinstance(raw["headerBold"], bool):
            raise HTTPException(
                status_code=400,
                detail="notes_table_style.headerBold must be a boolean.",
            )
        cleaned["headerBold"] = raw["headerBold"]
    if "headerRule" in raw:
        if not isinstance(raw["headerRule"], bool):
            raise HTTPException(
                status_code=400,
                detail="notes_table_style.headerRule must be a boolean.",
            )
        cleaned["headerRule"] = raw["headerRule"]
    if "listMarker" in raw and raw["listMarker"] is not None:
        if raw["listMarker"] not in _LIST_MARKERS:
            raise HTTPException(
                status_code=400,
                detail=f"notes_table_style.listMarker must be one of {sorted(_LIST_MARKERS)}.",
            )
        cleaned["listMarker"] = raw["listMarker"]
    if "totalsDoubleUnderline" in raw:
        if not isinstance(raw["totalsDoubleUnderline"], bool):
            raise HTTPException(
                status_code=400,
                detail="notes_table_style.totalsDoubleUnderline must be a boolean.",
            )
        cleaned["totalsDoubleUnderline"] = raw["totalsDoubleUnderline"]
    return cleaned


@router.get("/api/config")
async def get_config():
    """Lightweight feature-flag surface for the frontend. Lets the SPA hide
    canonical-mode UI (the Concepts tab + per-run links) when the backend
    isn't running in canonical mode (peer-review finding 5)."""
    return {
        "canonical_mode": server._canonical_mode_enabled(),
        # False only when canonical mode is on but the startup tree bootstrap
        # failed — the UI can warn instead of showing an empty Concepts page.
        "canonical_ready": server._CANONICAL_BOOTSTRAP_OK is not False,
        # Whether the reviewer pass auto-runs after extraction (Settings
        # toggle). Surfaced here so the SPA can label the run accordingly.
        "auto_review": server._auto_review_enabled(),
        # Clean-run spot-check (issue 1): whether a run with no failing checks
        # still gets a grounded sanity pass, and at what depth (light/full).
        "spot_check": server._spot_check_enabled(),
        "spot_check_mode": server._spot_check_mode(),
        # Notes coverage checklist (docs/PLAN-notes-coverage-and-routing.md). Default on.
        "notes_coverage": server._notes_coverage_enabled(),
        # Item 28 — per-entity advisory memory (prior-year prompt hints). Default on.
        "entity_memory": server._entity_memory_enabled(),
        # Firm-wide notes-table style theme (docs/PLAN-notes-table-theme.md).
        # Surfaced here so the Notes tab + clipboard read the firm default at
        # render time without a separate /api/settings round-trip.
        "notes_table_style": server._notes_table_style(),
        # v30 evals workspace: the build that would stamp a run launched now, so
        # the UI can show "you are on version X" (docs/PLAN-evals-workspace.md).
        "app_version": _app_version(),
    }


@router.get("/api/settings")
async def get_settings():
    load_dotenv(server.ENV_FILE, override=True)
    api_key = server._resolve_api_key()
    masked = api_key[:4] + "..." + api_key[-2:] if len(api_key) > 8 else ""

    extended = server._load_extended_settings()
    return {
        # Backward-compatible fields
        "model": os.environ.get("TEST_MODEL", "openai.gpt-5.4"),
        "proxy_url": os.environ.get("LLM_PROXY_URL", ""),
        "api_key_set": bool(api_key),
        "api_key_preview": masked,
        # Extended fields (Phase 8)
        "available_models": server._load_available_models(),
        **extended,
    }


@router.post("/api/settings")
async def update_settings(body: dict, request: Request):
    """Update .env file with new settings.

    AI-plumbing + firm-wide run-default keys are admin-only (the settings form
    hides them from non-admins, but the server enforces it). A non-admin write
    that touches any such key is refused with 403; a write touching only the
    cosmetic keys (e.g. notes_table_style) is allowed for everyone.
    """
    if _ADMIN_ONLY_SETTINGS_KEYS.intersection(body):
        with db_session(server.AUDIT_DB_PATH) as conn:
            denied = auth_routes._require_admin(conn, request)
        if denied is not None:
            return denied

    ENV_FILE = server.ENV_FILE
    if not ENV_FILE.exists():
        # encoding pinned per the Windows UTF-8 invariant (gotcha #1).
        ENV_FILE.write_text("", encoding="utf-8")

    # Legacy fields
    if "model" in body:
        set_key(str(ENV_FILE), "TEST_MODEL", body["model"])
    if "api_key" in body and body["api_key"]:
        set_key(str(ENV_FILE), "GOOGLE_API_KEY", body["api_key"])
    if "proxy_url" in body and body["proxy_url"]:
        set_key(str(ENV_FILE), "LLM_PROXY_URL", body["proxy_url"])

    # Extended fields (Phase 8)
    if "default_models" in body:
        # Validate the submitted dict BEFORE merging anything. The peer
        # review flagged that an unvalidated payload could land arbitrary
        # data in .env (e.g. {"x": {"nested": [...]}} would be json-dumped
        # verbatim). Constrain keys to the known agent roles + notes
        # templates, and values to short strings matching an id in
        # config/models.json. Reject everything else with 400 so a
        # misconfigured client fails loudly instead of polluting the env
        # file the whole run pipeline reads from.
        raw_models = body["default_models"]
        if not isinstance(raw_models, dict):
            raise HTTPException(
                status_code=400,
                detail="default_models must be an object keyed by agent role.",
            )
        from notes_types import NotesTemplateType as _NT
        allowed_keys = set(server._AGENT_ROLES) | {nt.value for nt in _NT}
        known_model_ids = {m["id"] for m in server._load_available_models() if "id" in m}
        for key, value in raw_models.items():
            if key not in allowed_keys:
                raise HTTPException(
                    status_code=400,
                    detail=f"Unknown default_models key: {key!r}. Allowed: {sorted(allowed_keys)}.",
                )
            if not isinstance(value, str) or not value:
                raise HTTPException(
                    status_code=400,
                    detail=f"default_models[{key!r}] must be a non-empty string model id.",
                )
            if len(value) > 128:
                raise HTTPException(
                    status_code=400,
                    detail=f"default_models[{key!r}] value too long (max 128 chars).",
                )
            # An unknown model id is a soft warning, not an error — the
            # config file may have been edited without a server restart,
            # or a new model may be in the registry file but not yet
            # loaded. The guard above already capped length + type.
            if known_model_ids and value not in known_model_ids:
                logger.warning(
                    "default_models[%s]=%s not in config/models.json", key, value,
                )

        # Merge incoming (now-validated) overrides with existing defaults
        load_dotenv(ENV_FILE, override=True)
        existing = server._load_extended_settings()["default_models"]
        existing.update(raw_models)
        set_key(str(ENV_FILE), "XBRL_DEFAULT_MODELS", json.dumps(existing))
    if "scout_enabled_default" in body:
        set_key(str(ENV_FILE), "XBRL_SCOUT_ENABLED_DEFAULT",
                "true" if body["scout_enabled_default"] else "false")
    if "auto_review" in body:
        set_key(str(ENV_FILE), "XBRL_AUTO_REVIEW",
                "true" if body["auto_review"] else "false")
    if "notes_auto_review" in body:
        set_key(str(ENV_FILE), "XBRL_NOTES_AUTO_REVIEW",
                "true" if body["notes_auto_review"] else "false")
    # Clean-run spot-check (issue 1): enable toggle + depth (light/full).
    if "spot_check" in body:
        set_key(str(ENV_FILE), "XBRL_SPOT_CHECK",
                "true" if body["spot_check"] else "false")
    if "spot_check_mode" in body:
        mode = str(body["spot_check_mode"]).strip().lower()
        if mode not in ("light", "full"):
            raise HTTPException(
                status_code=400,
                detail="spot_check_mode must be 'light' or 'full'.",
            )
        set_key(str(ENV_FILE), "XBRL_SPOT_CHECK_MODE", mode)
    # Notes coverage checklist (docs/PLAN-notes-coverage-and-routing.md). Default on.
    if "notes_coverage" in body:
        set_key(str(ENV_FILE), "XBRL_NOTES_COVERAGE",
                "true" if body["notes_coverage"] else "false")
    # Item 28 — per-entity advisory memory toggle (prior-year prompt hints).
    if "entity_memory" in body:
        set_key(str(ENV_FILE), "XBRL_ENTITY_MEMORY",
                "true" if body["entity_memory"] else "false")
    if "tolerance_rm" in body:
        set_key(str(ENV_FILE), "XBRL_TOLERANCE_RM", str(body["tolerance_rm"]))

    # Firm-wide notes-table style theme (docs/PLAN-notes-table-theme.md). Stored
    # as a JSON object, like XBRL_DEFAULT_MODELS. Validated/cleaned first so a
    # tampered payload can't land broken CSS in .env.
    if "notes_table_style" in body:
        cleaned = _validate_notes_table_style(body["notes_table_style"])
        set_key(str(ENV_FILE), "XBRL_NOTES_TABLE_STYLE", json.dumps(cleaned))

    load_dotenv(ENV_FILE, override=True)
    return {"status": "ok"}


@router.post("/api/test-connection")
async def test_connection(body: dict, request: Request):
    """Test LLM connectivity with provided or .env settings.

    Admin-only: this exercises the shared AI plumbing with a supplied model /
    proxy / key, so it's gated the same way as writing those settings (the
    hidden button in the settings form is only a UI guard).
    """
    with db_session(server.AUDIT_DB_PATH) as conn:
        denied = auth_routes._require_admin(conn, request)
    if denied is not None:
        return denied

    load_dotenv(server.ENV_FILE, override=True)

    model_name = body.get("model") or os.environ.get("TEST_MODEL", "openai.gpt-5.4")
    api_key = body.get("api_key") or os.environ.get("GOOGLE_API_KEY", "")
    proxy_url = body.get("proxy_url") or os.environ.get("LLM_PROXY_URL", "")

    if not api_key:
        return JSONResponse(status_code=400, content={"status": "error", "message": "API key is required."})

    start = time.time()
    try:
        from pydantic_ai import Agent

        model = server._create_proxy_model(model_name, proxy_url, api_key)
        test_agent = Agent(model)
        result = await test_agent.run("Say OK")
        latency_ms = int((time.time() - start) * 1000)
        return {"status": "ok", "model": model_name, "latency_ms": latency_ms}
    except Exception:
        # LLM SDK exceptions frequently embed the Authorization header or
        # bearer token in str(e). Log the full trace server-side only; the
        # HTTP response stays generic so we never leak credentials to callers.
        logger.exception("Connection test failed", extra={"model": model_name})
        return JSONResponse(
            status_code=502,
            content={
                "status": "error",
                "message": "Connection test failed. See server logs for details.",
            },
        )
