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
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

import server

logger = logging.getLogger("server")

router = APIRouter()


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
async def update_settings(body: dict):
    """Update .env file with new settings."""
    ENV_FILE = server.ENV_FILE
    if not ENV_FILE.exists():
        ENV_FILE.write_text("")

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
    if "tolerance_rm" in body:
        set_key(str(ENV_FILE), "XBRL_TOLERANCE_RM", str(body["tolerance_rm"]))

    load_dotenv(ENV_FILE, override=True)
    return {"status": "ok"}


@router.post("/api/test-connection")
async def test_connection(body: dict):
    """Test LLM connectivity with provided or .env settings."""
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
