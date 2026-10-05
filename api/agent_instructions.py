"""Read shared guidance and live prompt sources; admin-only guidance updates."""
from datetime import datetime, timezone
import json

from fastapi import APIRouter, HTTPException, Request

import server
from agent_instructions import MAX_GUIDANCE_LENGTH, SCOPES, instruction_sources, read_guidance
from auth import config, middleware, routes as auth_routes
from db.repository import db_session

router = APIRouter()


@router.get("/api/agent-instructions")
async def get_agent_instructions():
    with db_session(server.AUDIT_DB_PATH) as conn:
        return {**read_guidance(conn), "scopes": SCOPES, "max_length": MAX_GUIDANCE_LENGTH}


@router.put("/api/agent-instructions")
async def put_agent_instructions(body: dict, request: Request):
    with db_session(server.AUDIT_DB_PATH) as conn:
        denied = auth_routes._require_admin(conn, request)
        if denied is not None:
            return denied
        texts = body.get("texts")
        revision = body.get("revision")
        if type(revision) is not int or revision < 0 or not isinstance(texts, dict) or set(texts) != set(SCOPES):
            raise HTTPException(400, "Provide all guidance scopes and the saved revision.")
        if any(not isinstance(value, str) or len(value) > MAX_GUIDANCE_LENGTH or "\x00" in value for value in texts.values()):
            raise HTTPException(400, f"Each instruction must be plain text, up to {MAX_GUIDANCE_LENGTH} characters.")
        if config.dev_bypass_active():
            editor = "Dev"
        else:
            session, _ = middleware.resolve_session(conn, request.cookies.get(config.cookie_name()))
            editor = session.email
        conn.commit()
        conn.execute("BEGIN IMMEDIATE")
        if read_guidance(conn)["revision"] != revision:
            raise HTTPException(409, "Someone else saved guidance. Reload the saved version before saving your changes.")
        conn.execute(
            "INSERT INTO agent_instructions(id,texts_json,revision,updated_by,updated_at) VALUES(1,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET texts_json=excluded.texts_json,revision=excluded.revision,"
            "updated_by=excluded.updated_by,updated_at=excluded.updated_at",
            (json.dumps(texts, ensure_ascii=False), revision + 1, editor, datetime.now(timezone.utc).isoformat()),
        )
        return {**read_guidance(conn), "scopes": SCOPES, "max_length": MAX_GUIDANCE_LENGTH}


@router.get("/api/agent-instructions/sources")
async def get_instruction_sources():
    return instruction_sources()
