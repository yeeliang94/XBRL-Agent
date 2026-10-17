"""The two supported report roles, derived from the committed SSM taxonomy."""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
REPORTS = {
    "DIRECTORS_REPORT": ("Directors' report", "120000", "Report-Directors"),
    "AUDITORS_REPORT": ("Auditors' report", "130000", "Report-Auditors"),
}


def template_path(report: str, standard: str, level: str) -> Path:
    if report not in REPORTS or standard not in {"mfrs", "mpers"} or level not in {"company", "group"}:
        raise ValueError("Unsupported report or filing family")
    return ROOT / f"XBRL-template-{standard.upper()}" / level.capitalize() / f"{REPORTS[report][2]}.xlsx"


@dataclass(frozen=True)
class ReportField:
    concept: str
    label: str
    row: int
    kind: str
    choices: tuple[str, ...] = ()


@lru_cache(maxsize=1)
def _types() -> dict[str, tuple[str, ...]]:
    xs = "{http://www.w3.org/2001/XMLSchema}"
    root = ET.parse(ROOT / "SSMxT_2022v1.0/def/fdn/ssmt-fdn_2022-12-31.xsd")
    return {t.get("name"): tuple(e.get("value") for e in t.iter(xs + "enumeration"))
            for t in root.iter(xs + "complexType")}


@lru_cache(maxsize=4)
def fields(report: str, standard: str = "mfrs") -> tuple[ReportField, ...]:
    from concept_model.taxonomy_semantics import taxonomy_registry
    from scripts.generate_mpers_templates import walk_role_for_taxonomy

    if report not in REPORTS or standard not in {"mfrs", "mpers"}:
        raise ValueError("Unsupported report or filing standard")
    rows = walk_role_for_taxonomy(
        ROOT / f"SSMxT_2022v1.0/rep/ssm/ca-2016/fs/{standard}", standard, REPORTS[report][1])
    registry = taxonomy_registry()
    result = []
    for row, (_, concept_id, label, _) in enumerate(rows, 3):
        c = registry[concept_id]
        if not c.reportable:
            continue
        dtype = (c.data_type or "").split(":")[-1]
        choices = _types().get(dtype, ())
        kind = "html" if dtype == "textBlockItemType" else "date" if dtype == "dateItemType" else "choice" if choices else "text"
        result.append(ReportField(concept_id, label, row, kind, choices))
    return tuple(result)


def value_kind(concept_id: str, standard: str) -> str:
    return next(f.kind for report in REPORTS for f in fields(report, standard) if f.concept == concept_id)


def import_templates(db_path) -> None:
    from concept_model.notes_parser import parse_notes_template
    from concept_model.notes_importer import import_notes_template
    from concept_model.filing_targets import persist_template_manifest

    for standard in ("mfrs", "mpers"):
        for level in ("company", "group"):
            for report, (_, _, sheet) in REPORTS.items():
                path = template_path(report, standard, level)
                template_id, nodes = parse_notes_template(str(path), sheet)
                html_ids = {f.concept for f in fields(report, standard) if f.kind == "html"}
                import_notes_template(db_path, template_id, [n for n in nodes if n.taxonomy_element_id in html_ids])
                persist_template_manifest(db_path, path)
