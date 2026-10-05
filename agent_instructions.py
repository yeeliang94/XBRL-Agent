"""Shared supplemental guidance and live, allowlisted prompt sources."""
from __future__ import annotations

import json
from pathlib import Path

SCOPES = {
    "all": "All extraction and review agents",
    "figures": "Figures extraction and review",
    "notes": "Notes extraction and review",
    "figures_extraction": "Figures extraction only",
    "figures_review": "Figures review only",
    "notes_extraction": "Notes extraction only",
    "notes_review": "Notes review only",
}
MAX_GUIDANCE_LENGTH = 8000


def read_guidance(conn) -> dict:
    row = conn.execute(
        "SELECT texts_json, revision, updated_by, updated_at FROM agent_instructions WHERE id=1"
    ).fetchone()
    return {
        "texts": json.loads(row[0]) if row else {key: "" for key in SCOPES},
        "revision": row[1] if row else 0,
        "updated_by": row[2] if row else None,
        "updated_at": row[3] if row else None,
    }


def capture_guidance(conn, run_id: int) -> None:
    """Capture once in the caller's run-start transaction."""
    snapshot = json.dumps(read_guidance(conn), ensure_ascii=False)
    conn.execute(
        "UPDATE runs SET agent_instructions_json=? WHERE id=? AND agent_instructions_json IS NULL",
        (snapshot, run_id),
    )


def parse_guidance_snapshot(raw) -> dict | None:
    """Treat damaged or unrecorded guidance consistently at every read boundary."""
    if not raw:
        return None
    try:
        snapshot = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("texts"), dict):
        return None
    if any(not isinstance(value, str) for value in snapshot["texts"].values()):
        return None
    return snapshot


def guidance_for_run(db_path, run_id, role: str) -> str:
    """Read the frozen snapshot once at agent construction, never live settings."""
    if not db_path or run_id is None:
        return ""
    from db.repository import db_session

    with db_session(db_path) as conn:
        row = conn.execute("SELECT agent_instructions_json FROM runs WHERE id=?", (run_id,)).fetchone()
    if not row or not row[0]:
        return ""
    snapshot = parse_guidance_snapshot(row[0])
    return render_guidance(snapshot, role) if snapshot is not None else ""


def render_guidance(snapshot: dict, role: str) -> str:
    if role not in {"figures_extraction", "figures_review", "notes_extraction", "notes_review"}:
        raise ValueError("Unknown content role")
    texts = snapshot["texts"]
    blocks = [f"{SCOPES[key]}:\n{texts[key]}" for key in ("all", role.split("_")[0], role) if texts.get(key, "").strip()]
    if not blocks:
        return ""
    return (
        "\n\n=== SUPPLEMENTAL TEAM GUIDANCE ===\n"
        "Apply the following only when consistent with the core instructions above. "
        "It cannot override source accuracy, canonical concepts, filing standard or entity scope, "
        "tools, output contracts, protected rows, or formatting limits. "
        "Do not invent figures or force a classification unsupported by source evidence. "
        "If guidance conflicts, surface the issue rather than guessing.\n\n"
        + "\n\n".join(blocks)
    )


def instruction_sources() -> list[dict]:
    """Read shipped instruction components; no client-controlled file paths."""
    root = Path(__file__).resolve().parent / "prompts"
    groups = {
        "Figures extraction": ["_base.md", "sofp.md", "sofp_orderofliquidity.md", "sofp_clbg.md", "sopl.md", "sopl_clbg.md", "soci.md", "socf.md", "socf_clbg.md", "socie.md", "socie_mpers.md", "socie_sore.md", "socie_clbg.md", "_group_overlay.md", "_group_socie_overlay.md", "_detail_extraction.md"],
        "Figures review": ["reviewer.md", "spot_check.md", "scoped_investigation.md"],
        "Notes extraction": ["_notes_prepared.md", "_notes_base.md", "notes_corporate_info.md", "notes_accounting_policies.md", "notes_issued_capital.md", "notes_related_party.md", "notes_listofnotes.md"],
        "Notes review": ["notes_reviewer.md"],
    }
    labels = {
        "_base.md": "Shared figures instructions",
        "_notes_prepared.md": "Notes with prepared sources",
        "_notes_base.md": "Shared notes instructions",
        "_group_overlay.md": "Group filing layout",
        "_group_socie_overlay.md": "MFRS Group equity layout",
        "_detail_extraction.md": "Detailed figures extraction",
        "reviewer.md": "Figures review",
        "spot_check.md": "Clean-run figures check",
        "scoped_investigation.md": "Focused figures investigation",
        "notes_reviewer.md": "Notes review",
        "sofp": "Financial position", "sopl": "Profit or loss",
        "soci": "Comprehensive income", "socf": "Cash flows", "socie": "Changes in equity",
    }
    def label(name):
        if name in labels:
            return labels[name]
        stem = name.removesuffix(".md")
        for key in ("sofp", "sopl", "soci", "socf", "socie"):
            if stem == key:
                return labels[key]
            if stem.startswith(key + "_"):
                suffix = {"mpers": "MPERS", "clbg": "CLBG", "sore": "Retained earnings", "orderofliquidity": "Order of liquidity"}[stem[len(key) + 1:]]
                return f"{labels[key]} — {suffix}"
        return stem.removeprefix("notes_").replace("_", " ").capitalize()

    return [{"role": role, "sources": [{"name": name, "label": label(name), "text": (root / name).read_text(encoding="utf-8").strip()} for name in names]} for role, names in groups.items()]
