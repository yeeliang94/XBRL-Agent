"""Generate the committed concept unit-class index from the SSM taxonomy.

Step 6 of docs/PLAN-mtool-fill-pipeline.md (peer-review finding 2). The mTool
exporter used to carry ONE filing-wide ``scale`` multiplier. That is wrong the
moment linear coverage reaches the numeric notes: MFRS sheet 13
(``Notes-IssuedCapital``) puts "Number of shares issued and fully paid" three
rows above "Amount of shares issued and fully paid", so a blanket thousands
conversion would multiply a share COUNT by 1,000 and file it.

To scale only what is a money amount, a fact needs a **unit class**. Neither
``run_concept_facts`` nor ``concept_nodes`` carries one — the concept-model
parser mints UUIDs from ``(template_id, sheet, row, label)`` and drops the XBRL
concept id. So we take it from the authority instead of guessing from the
label: every XBRL element declaration in ``SSMxT_2022v1.0/`` states its item
type (``xbrli:monetaryItemType``, ``xbrli:sharesItemType``, …). We read those,
join them to the taxonomy's own label text (the same join
``scripts/generate_concept_definitions.py`` uses), and emit a label-keyed index
the runtime can load without re-parsing XML.

**Ambiguity is recorded, never resolved by guessing.** If one normalised label
maps to two different unit classes, the entry is written as ``"ambiguous"`` —
the exporter treats that exactly like "unknown" and refuses to scale it.

Run: ``python3 scripts/generate_concept_units.py`` — writes
``concept_model/concept_units_{mfrs,mpers}.json``. Re-run after any SSM
taxonomy upgrade.
"""
from __future__ import annotations

import json
import re
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(_REPO_ROOT / "scripts"))
import generate_mpers_templates as _gen  # noqa: E402
from generate_mpers_templates import load_label_map  # noqa: E402

sys.path.insert(0, str(_REPO_ROOT))
from notes.labels import normalize_label  # noqa: E402

_TAXONOMY_ROOT = _REPO_ROOT / "SSMxT_2022v1.0"
_OUTPUT_DIR = _REPO_ROOT / "concept_model"

# XBRL item type -> the unit class the translation manifest reasons about.
#
# * monetary  — a money amount; the ONLY class a denomination scale may touch.
# * shares    — a count of shares; scaling it is the finding-2 bug.
# * per_share — a money amount PER share (EPS, DPS); already per-unit, and mTool
#               expects it unscaled, so it is its own class rather than
#               "monetary" with an asterisk.
# * pure      — percentages, ratios, plain counts; dimensionless.
# * non_numeric — text/date/domain; never carries a figure the exporter writes.
_TYPE_TO_UNIT_CLASS: dict[str, str] = {
    "monetaryItemType": "monetary",
    "sharesItemType": "shares",
    "perShareItemType": "per_share",
    "pureItemType": "pure",
    "percentItemType": "pure",
    "decimalItemType": "pure",
    "integerItemType": "pure",
    "nonNegativeIntegerItemType": "pure",
    "positiveIntegerItemType": "pure",
    "stringItemType": "non_numeric",
    "textBlockItemType": "non_numeric",
    "domainItemType": "non_numeric",
    "dateItemType": "non_numeric",
    "durationItemType": "non_numeric",
    "gYearItemType": "non_numeric",
    "enumerationItemType": "non_numeric",
}

# Which concept-id prefixes belong to each filing standard. `ssmt` (the shared
# SSM core) and the DEI prefixes are common to both.
_STANDARD_PREFIXES: dict[str, tuple[str, ...]] = {
    "mfrs": ("ssmt-mfrs", "ssmt", "ifrs-full", "ssmt-dei", "ssmt-ee",
             "ssmt-dei-ee-mfrs"),
    "mpers": ("ssmt-mpers", "ssmt", "ifrs-smes", "ssmt-dei", "ssmt-ee",
              "ssmt-dei-ee-mpers"),
}

_ELEMENT_RE = re.compile(r"<xs:element\b[^>]*>")
_ID_RE = re.compile(r'\bid="([^"]+)"')
_TYPE_RE = re.compile(r'\btype="([^"]+)"')

AMBIGUOUS = "ambiguous"


def _unit_class_for_type(xsd_type: str) -> str | None:
    """Map ``xbrli:monetaryItemType`` -> ``monetary``. Unknown types return
    ``None`` so they are simply absent from the index rather than mislabelled."""
    local = xsd_type.split(":", 1)[-1]
    return _TYPE_TO_UNIT_CLASS.get(local)


def scan_element_types(root: Path = _TAXONOMY_ROOT) -> dict[str, str]:
    """``{concept_id -> xsd item type}`` for every element in the taxonomy.

    Regex rather than a full XML parse: the declarations are one self-closing
    element per line across ~190 schema files, and we want only two attributes.
    A malformed file degrades to "fewer entries", never a crash — a concept we
    can't type simply has no unit class, which the exporter treats loudly.
    """
    types: dict[str, str] = {}
    for path in sorted(root.rglob("*.xsd")):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for match in _ELEMENT_RE.finditer(text):
            tag = match.group(0)
            cid, xtype = _ID_RE.search(tag), _TYPE_RE.search(tag)
            if cid and xtype:
                types.setdefault(cid.group(1), xtype.group(1))
    return types


def build_index(standard: str, element_types: dict[str, str],
                label_map: dict[str, dict[str, str]]) -> dict[str, str]:
    """``{normalised label -> unit_class}`` for one filing standard.

    Every label spelling a concept carries (SSM ReportingLabel, standard label,
    terse label, …) is indexed, because templates render whichever role SSM
    designated — see the MPERS label convention. A label two concepts share
    with DIFFERENT unit classes is written as ``ambiguous``.
    """
    prefixes = _STANDARD_PREFIXES[standard]
    by_label: dict[str, set[str]] = defaultdict(set)
    for concept_id, roles in label_map.items():
        if concept_id.split("_", 1)[0] not in prefixes:
            continue
        xsd_type = element_types.get(concept_id)
        if not xsd_type:
            continue
        unit_class = _unit_class_for_type(xsd_type)
        if unit_class is None:
            continue
        for label in roles.values():
            key = normalize_label(label)
            if key:
                by_label[key].add(unit_class)

    return {label: _resolve(classes) for label, classes in sorted(by_label.items())}


def _resolve(classes: set[str]) -> str:
    """Collapse the unit classes one label maps to into a single verdict.

    A label is routinely shared by a numeric concept and a text/domain one —
    "Biological assets" is both a monetary line item and a dimension member.
    That is not a real ambiguity for our purpose: the exporter only ever asks
    about a fact that already holds a NUMBER, and a text block or domain member
    can't be where that number came from. So text-only candidates are dropped
    whenever a numeric one exists — narrowing to the only interpretation that
    can apply, not guessing between two live options.

    Two different NUMERIC classes (monetary vs shares) is a genuine ambiguity
    and stays ``ambiguous``, which the exporter refuses to scale.
    """
    numeric = classes - {"non_numeric"}
    if not numeric:
        return "non_numeric"
    if len(numeric) == 1:
        return next(iter(numeric))
    return AMBIGUOUS


def _parse_label_linkbase(path: Path) -> dict[str, dict[str, str]]:
    """One English label linkbase -> ``{concept_id -> {role -> text}}``.

    Same loc / labelArc / label resolution as
    ``generate_mpers_templates.load_label_map``; kept here rather than widening
    that function's file globs because the MPERS template generator's output is
    pinned (gotcha #3) and must not shift as a side effect of this index.
    """
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError):
        return {}

    xlink = _gen._NS["xlink"]
    loc_to_concept: dict[str, str] = {}
    resources: dict[str, tuple[str, str]] = {}
    arcs: list[tuple[str, str]] = []
    for elem in root.iter():
        tag = elem.tag.split("}", 1)[-1]
        if tag == "loc":
            key, href = elem.get(f"{{{xlink}}}label"), elem.get(f"{{{xlink}}}href")
            if key and href:
                loc_to_concept[key] = href.split("#", 1)[-1]
        elif tag == "label":
            key = elem.get(f"{{{xlink}}}label")
            text = (elem.text or "").strip()
            if key and text:
                resources[key] = (elem.get(f"{{{xlink}}}role") or "", text)
        elif tag == "labelArc":
            frm, to = elem.get(f"{{{xlink}}}from"), elem.get(f"{{{xlink}}}to")
            if frm and to:
                arcs.append((frm, to))

    out: dict[str, dict[str, str]] = defaultdict(dict)
    for frm, to in arcs:
        concept_id = loc_to_concept.get(frm)
        info = resources.get(to)
        if concept_id and info:
            role, text = info
            out[concept_id].setdefault(_gen._normalise_label_role(role), text)
    return out


def load_role_labels() -> dict[str, dict[str, str]]:
    """``{concept_id -> {role -> label}}`` — EVERY English label spelling.

    Two reasons this doesn't just call ``load_label_map``: that function returns
    only the single preferred spelling per concept, and its file globs skip
    ``lab_full_ifrs-en*.xml`` — which holds the labels for the ``ifrs-full_*``
    concepts the MFRS face templates are built from (without it, "Property,
    plant and equipment" has no unit class at all).
    """
    merged: dict[str, dict[str, str]] = defaultdict(dict)
    for path in sorted(_TAXONOMY_ROOT.rglob("lab_*.xml")):
        # English only — a Bahasa Malaysia spelling never appears on a template.
        name = path.name
        if "-en" not in name and "_en" not in name:
            continue
        for concept_id, roles in _parse_label_linkbase(path).items():
            for role, text in roles.items():
                merged[concept_id].setdefault(role, text)
    return {k: dict(v) for k, v in merged.items()}


def main() -> int:
    element_types = scan_element_types()
    label_map = load_role_labels()
    print(f"scanned {len(element_types)} element declarations, "
          f"{len(label_map)} labelled concepts")

    for standard in ("mfrs", "mpers"):
        index = build_index(standard, element_types, label_map)
        counts: dict[str, int] = defaultdict(int)
        for unit_class in index.values():
            counts[unit_class] += 1
        out = _OUTPUT_DIR / f"concept_units_{standard}.json"
        out.write_text(
            json.dumps(index, indent=1, sort_keys=True, ensure_ascii=False)
            + "\n",
            encoding="utf-8")
        print(f"{standard}: {len(index)} labels -> {out.name} "
              + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
