"""Repeat-group consistency routes (docs/PLAN-evals-workspace.md).

The gold-benchmark routes that used to live here were removed with the
benchmark flow (docs/human-mtool-file-comparison-plan.md, Step 11); a run is
now checked against a human-filled mTool file (``api/human_file.py``).

Endpoints:
  GET    /api/repeat-groups/{id}            — a group + its consistency
  POST   /api/repeat-groups/{id}/recompute  — recompute its consistency
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

import server

router = APIRouter()


def resolve_slot_labels(conn, rows: list[dict]) -> None:
    """Attach human line-item names to slot rows IN PLACE (Step 11/12).

    A slot key is (concept_uuid, period, entity_scope) — meaningless to a
    reviewer. Resolve each uuid to its sheet + label via concept_nodes; a
    uuid that no longer resolves keeps only the raw key (the UI falls back
    to rendering it)."""
    uuids = {r["key"][0] for r in rows if r.get("key")}
    if not uuids:
        return
    placeholders = ",".join("?" for _ in uuids)
    labels = {
        u: (sheet, label)
        for u, sheet, label in conn.execute(
            f"SELECT concept_uuid, render_sheet, canonical_label "
            f"FROM concept_nodes WHERE concept_uuid IN ({placeholders})",
            tuple(uuids),
        ).fetchall()
    }
    for r in rows:
        hit = labels.get((r.get("key") or [None])[0])
        if hit is not None:
            r["sheet"], r["label"] = hit


@router.get("/api/repeat-groups/{group_id}")
async def get_repeat_group_endpoint(group_id: int):
    """A repeat group + its computed consistency result (v30). Feeds the
    consistency panel on a grouped run's page (docs/PLAN-evals-workspace.md).
    Disagreement slots carry resolved sheet/label names; each child run
    carries its own accuracy (Step 11)."""
    from db import repository as repo

    conn = server._open_audit_conn()
    try:
        group = repo.fetch_repeat_group(conn, group_id)
        if group is not None and group.get("consistency"):
            c = group["consistency"]
            resolve_slot_labels(
                conn,
                list(c.get("presence_disagreements") or [])
                + list(c.get("value_disagreements") or []),
            )
    finally:
        conn.close()
    if group is None:
        raise HTTPException(status_code=404, detail="Repeat group not found")
    return group


@router.post("/api/repeat-groups/{group_id}/recompute")
async def recompute_repeat_group_endpoint(group_id: int):
    """Recompute + persist a group's consistency from its finished repeats. Used
    after a repeat finishes, or manually from the panel."""
    from db import repository as repo
    from eval.consistency import finalize_repeat_group

    conn = server._open_audit_conn()
    try:
        if repo.fetch_repeat_group(conn, group_id) is None:
            raise HTTPException(status_code=404, detail="Repeat group not found")
        result = finalize_repeat_group(conn, group_id)
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "group_id": group_id, "consistency": result.to_dict()}
