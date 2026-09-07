"""Request-scoped sheet selection shared by mTool detection, preview and fill."""
from __future__ import annotations

from copy import deepcopy
import json


def select_sheets(raw: str | None, doc: dict, note_sheets: set[str]) -> tuple[dict, dict | None]:
    """Omission preserves full-run filling; an explicit selection must be valid."""
    if raw is None:
        return doc, None
    selected = json.loads(raw)
    available = set(doc.get("sheets", {})) | note_sheets
    if (not isinstance(selected, list) or not selected
            or any(not isinstance(sheet, str) or sheet not in available for sheet in selected)):
        raise ValueError("selected_sheets must be a non-empty list of this run's sheet names")
    selected = sorted(set(selected))
    scoped = deepcopy(doc)
    scoped["writes"] = [w for w in doc.get("writes", []) if w["sheet"] in selected]
    scoped["sheets"] = {s: cfg for s, cfg in doc.get("sheets", {}).items() if s in selected}
    meta = scoped.setdefault("meta", {})
    meta["sheets_covered"] = sorted(scoped["sheets"])
    meta.setdefault("counts", {})["writes"] = len(scoped["writes"])
    meta["unit_class_unknown"] = [w for w in meta.get("unit_class_unknown", []) if w.get("sheet") in selected]
    # Keep the full source snapshot and run-wide conflict evidence. The report
    # records this explicit subset; exclusion never changes run readiness.
    selection = {
        "selected_sheets": selected,
        "excluded_sheets": sorted(available - set(selected)),
        "excluded_figures": len(doc.get("writes", [])) - len(scoped["writes"]),
        "excluded_notes": None,
    }
    return scoped, selection


def scope_notes(doc: dict, selection: dict | None) -> dict:
    """Filter notes, record exclusions, and keep receipt revision rows aligned."""
    if selection is None:
        return doc
    scoped = deepcopy(doc)
    indices = [i for i, n in enumerate(doc["footnotes"])
               if n.get("source_sheet") in selection["selected_sheets"]]
    selection["excluded_notes"] = len(doc["footnotes"]) - len(indices)
    scoped["footnotes"] = [scoped["footnotes"][i] for i in indices]
    meta = scoped.setdefault("meta", {})
    revisions = meta.get("notes_revision", [])
    meta["notes_revision"] = [revisions[i] for i in indices] if revisions else []
    counts = meta.setdefault("counts", {})
    counts["notes"] = len(indices)
    for name, tier in (("formatting_compacted", "compact"), ("formatting_reduced", "lite"), ("formatting_dropped", "flat")):
        counts[name] = sum(n.get("format_tier") == tier for n in scoped["footnotes"])
    for name in ("source_styling_dropped", "white_grid_dropped"):
        counts[name] = sum(bool(n.get(name)) for n in scoped["footnotes"])
    return scoped


def validate_note_destinations(doc: dict, selection: dict | None, defined_names: dict) -> None:
    """An explicit key/cell may override a label, never the selected sheet set."""
    if selection is None:
        return
    selected = set(selection["selected_sheets"])
    for note in doc["footnotes"]:
        sheet = (defined_names.get(note["key"], {}).get("sheet") if note.get("key")
                 else note.get("sheet") or note.get("source_sheet"))
        if not isinstance(sheet, str) or sheet not in selected:
            raise ValueError("Note destination must belong to the selected sheets; recheck note placements")
