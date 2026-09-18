"""Stable fact identity for source-supported taxonomy dimensions.

Empty dimensions retain the historical identity. Dimensions already encoded
in a concept (SOCIE components) are removed from the instance key after their
agreement is checked, so the same fact cannot acquire two identities.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
import xml.etree.ElementTree as ET


@lru_cache(maxsize=4)
def numeric_category_catalog(standard: str, *, roots_only: bool = False) -> dict[str, list[str]]:
    """Read valid category members from SSM's definition linkbases."""
    if standard not in {"mfrs", "mpers"}:
        raise ValueError("Unknown filing standard")
    root = Path(__file__).resolve().parents[1] / "SSMxT_2022v1.0/rep/ssm/ca-2016/fs" / standard
    xlink = "{http://www.w3.org/1999/xlink}"
    catalog = {}
    for role in ("740000", "750000"):
        path, = root.glob(f"def_*role-{role}.xml")
        tree = ET.parse(path)
        for link in tree.getroot():
            locators = {e.get(xlink + "label"): e.get(xlink + "href", "").split("#")[-1]
                        for e in link if e.tag.endswith("}loc")}
            edges = {}
            axes = {}
            for arc in link:
                kind = arc.get(xlink + "arcrole", "").rsplit("/", 1)[-1]
                source, target = (locators.get(arc.get(xlink + k)) for k in ("from", "to"))
                if source and target and kind in {"dimension-domain", "domain-member"}:
                    edges.setdefault(source, []).append(target)
                    if kind == "dimension-domain" and "ConsolidatedAndSeparate" not in source:
                        axes[source] = target
            for axis in axes:
                members, pending = set(), list(edges.get(axis, []))
                while pending:
                    member = pending.pop()
                    if member in members:
                        continue
                    members.add(member)
                    pending.extend(edges.get(member, []))
                catalog[axis] = [axes[axis]] if roots_only else sorted(members)
    return catalog


def diagnostic_value(instances: list[tuple[str, float | None]]) -> float | None:
    """Project categories into the legacy total-only diagnostic grid.

    A source-disclosed total wins. Otherwise sum disjoint members of one
    taxonomy axis, preserving the components in canonical storage. This
    projection is never used to choose native filing destinations.
    """
    by_key = dict(instances)
    if "" in by_key:
        return by_key[""]
    if len(by_key) == 1:
        return next(iter(by_key.values()))
    parsed = [(json.loads(key), value) for key, value in by_key.items()]
    axes = {axis for dims, _ in parsed for axis in dims}
    if len(axes) != 1 or any(len(dims) != 1 for dims, _ in parsed):
        raise ValueError("Diagnostic total requires one common category axis")
    axis = next(iter(axes))
    standard = "mfrs" if axis.startswith(("ifrs-full_", "ssmt-mfrs_")) else "mpers"
    catalog = numeric_category_catalog(standard)
    roots = numeric_category_catalog(standard, roots_only=True)
    for dims, value in parsed:
        if dims[axis] not in catalog.get(axis, []):
            raise ValueError("Cannot aggregate an unrecognised numeric category")
        if dims[axis] in roots.get(axis, []):
            return value
    values = [value for _, value in parsed if value is not None]
    return sum(values) if values else None


def diagnostic_rows(rows) -> list[dict]:
    """Collapse only the internal grid projection, never the stored facts."""
    groups = {}
    for row in rows:
        item = dict(row)
        groups.setdefault((item["concept_uuid"], item["period"], item["entity_scope"]), []).append(item)
    out = []
    for group in groups.values():
        # Keep the status of the source total when present; otherwise a blank
        # first category must not suppress populated siblings in the grid.
        representative = next((r for r in group if not r["dimension_key"]), None)
        if representative is None:
            roots = {member for standard in ("mfrs", "mpers")
                     for members in numeric_category_catalog(standard, roots_only=True).values()
                     for member in members}
            representative = next((r for r in group
                if any(member in roots for member in json.loads(r["dimension_key"]).values())), None)
        item = dict(representative or next((r for r in group if r["value"] is not None), group[0]))
        item["value"] = diagnostic_value([(r["dimension_key"], r["value"]) for r in group])
        if any(r["dimension_key"] for r in group):
            item["evidence"] = json.dumps([{k: r.get(k) for k in ("dimension_key", "value", "evidence")}
                                          for r in group], ensure_ascii=False)
        out.append(item)
    return out


def dimension_key(dimensions: dict[str, str] | None = None) -> str:
    if not dimensions:
        return ""
    clean = {}
    for axis, member in dimensions.items():
        if not isinstance(axis, str) or not isinstance(member, str):
            raise ValueError("Dimensions must map taxonomy axis IDs to member IDs")
        axis, member = axis.strip(), member.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*", axis) or not re.fullmatch(
            r"[A-Za-z_][A-Za-z0-9_.-]*", member
        ):
            raise ValueError("Dimensions require exact taxonomy identifiers")
        if axis in clean and clean[axis] != member:
            raise ValueError("A dimension axis cannot have two members")
        clean[axis] = member
    return json.dumps(clean, sort_keys=True, separators=(",", ":"))


def instance_key(conn, concept_uuid: str, dimensions: dict[str, str]) -> str:
    supplied = json.loads(dimension_key(dimensions) or "{}")
    row = conn.execute(
        "SELECT dimensions_json FROM concept_semantic_addresses WHERE concept_uuid = ?",
        (concept_uuid,),
    ).fetchone()
    fixed = json.loads(row[0] or "{}") if row else {}
    for axis, member in fixed.items():
        if axis in supplied and supplied.pop(axis) != member:
            raise ValueError("Dimension conflicts with the concept's fixed category")
    node = conn.execute(
        "SELECT template_id, render_sheet FROM concept_nodes WHERE concept_uuid = ?",
        (concept_uuid,),
    ).fetchone()
    if supplied:
        if not node or node[1] not in {"Notes-Issuedcapital", "Notes-RelatedPartytran"}:
            raise ValueError("This concept does not accept an additional category axis")
        catalog = numeric_category_catalog(node[0].split("-")[0])
        suffix = "ClassesOfShareCapitalAxis" if node[1] == "Notes-Issuedcapital" else "CategoriesOfRelatedPartiesAxis"
        for axis, member in supplied.items():
            if not axis.endswith(suffix) or member not in catalog.get(axis, []):
                raise ValueError("Category must belong to this note's taxonomy axis")
    return dimension_key(supplied)
