"""Generate the committed concept-definition index from the SSM doc linkbases.

Plan B (docs/PLAN-extraction-judgement-improvements.md) gives extraction /
reviewer / notes agents a tool to look up the *official* SSM definition of a
concept when they are torn between similar template rows (e.g. "Other current
payables" vs "Other current non-trade payables"). The definitions already
ship inside the SSM taxonomy as XBRL "ReportingDocumentation" label
linkbases — this script extracts them into a small JSON index the runtime can
load without re-parsing XML on every run.

Why label-keyed, not concept_id-keyed: the concept-model parser discards the
XBRL concept_id at parse time (it mints UUIDs from
``(template_id, sheet, row, label)``), so a template row cannot be joined back
to a concept_id at runtime. We therefore index by human label (the same text
the agent sees on the row and types into a query) and carry the concept_id
alongside as metadata.

The documentation linkbase has the identical locator/arc/label shape as the
label linkbase that ``scripts/generate_mpers_templates.py::load_label_map``
already parses, so we reuse that label map for the concept_id -> label join
and apply the same parsing pattern here for concept_id -> definition.

Run: ``python3 scripts/generate_concept_definitions.py`` — writes
``concept_model/concept_definitions_{mfrs,mpers}.json``. Re-run after any SSM
taxonomy upgrade.
"""
from __future__ import annotations

import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

# Repo-root anchor so the script works regardless of caller cwd — same pattern
# as the other scripts/ generators.
_REPO_ROOT = Path(__file__).resolve().parent.parent

# Reuse the existing, tested concept_id -> display-label map. It scans every
# ``lab_en*.xml`` across the taxonomy tree, so it already covers BOTH standards
# (ssmt-mfrs_* and ssmt-mpers_*). Importing the generator module is safe — it
# only executes work under its ``if __name__ == "__main__"`` guard.
sys.path.insert(0, str(_REPO_ROOT / "scripts"))
from generate_mpers_templates import load_label_map  # noqa: E402

# normalize_label is the single source of truth for "what counts as the same
# label" across the notes pipeline; reusing it keeps the index's search key
# consistent with how labels are compared everywhere else.
sys.path.insert(0, str(_REPO_ROOT))
from notes.labels import normalize_label  # noqa: E402

# XBRL linkbase namespaces (same as the MPERS generator's _NS).
_NS = {
    "link": "http://www.xbrl.org/2003/linkbase",
    "xlink": "http://www.w3.org/1999/xlink",
}

# The documentation text lives on <link:label> resources carrying this role.
_DOCUMENTATION_ROLE = re.compile(r"/ReportingDocumentation$")

# Per-standard documentation linkbases, in priority order (first file wins on
# a concept_id collision). The rep-level ``doc_en-ssmt-fs-{standard}`` file is
# the PRIMARY source: it is the filing taxonomy's documentation, covering the
# same concepts the templates are generated from (templates come from
# ``rep/ssm/ca-2016/fs/{standard}``), so it documents the headline line items
# the agent actually fills (e.g. OtherCurrentNontradePayables). The def-level
# "cor" files only document standard-specific / shared extensions and add a
# small amount on top; we merge them for the most complete SSM coverage.
# (The full_ifrs / ifrs_for_smes docs were measured to add nothing here — their
# concept_ids don't normalise-match the template labels — so they are omitted.)
# Concepts with no SSM documentation simply won't appear; the tool returns an
# explicit no-match for them.
_DOC_LINKBASES: dict[str, list[Path]] = {
    "mfrs": [
        _REPO_ROOT
        / "SSMxT_2022v1.0/rep/ssm/ca-2016/fs/mfrs/doc_en-ssmt-fs-mfrs_2022-12-31.xml",
        _REPO_ROOT
        / "SSMxT_2022v1.0/def/ic/cor-ca2016/ssmt-mfrs-cor/doc_ssmt-mfrs-cor_2022-12-31.xml",
        _REPO_ROOT
        / "SSMxT_2022v1.0/def/ic/cor-ca2016/ssmt-cor/doc_ssmt-cor_2022-12-31.xml",
    ],
    "mpers": [
        _REPO_ROOT
        / "SSMxT_2022v1.0/rep/ssm/ca-2016/fs/mpers/doc_en-ssmt-fs-mpers_2022-12-31.xml",
        _REPO_ROOT
        / "SSMxT_2022v1.0/def/ic/cor-ca2016/ssmt-mpers-cor/doc_ssmt-mpers-cor_2022-12-31.xml",
        _REPO_ROOT
        / "SSMxT_2022v1.0/def/ic/cor-ca2016/ssmt-cor/doc_ssmt-cor_2022-12-31.xml",
    ],
}

# Output lands inside the concept_model package so the runtime loader can
# resolve it relative to its own __file__ without a separate data dir.
_OUTPUT_DIR = _REPO_ROOT / "concept_model"


def _concept_id_from_href(href: str) -> str:
    """Extract the concept id from an XSD href like ``…cor.xsd#ssmt-mfrs_Foo``."""
    return href.split("#", 1)[-1]


def _humanise_concept_id(concept_id: str) -> str:
    """Fallback label when the label map has no entry for a documented concept.

    Strips the ``ssmt-mfrs_`` / ``ssmt-mpers_`` prefix and splits CamelCase so
    the concept is still searchable by something readable. Only used when the
    real taxonomy label is genuinely missing — rare, but we never want a
    documented concept to be unsearchable.
    """
    local = concept_id.split("_", 1)[-1]
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", local)
    return spaced.strip()


def parse_doc_linkbase(path: Path) -> dict[str, str]:
    """Parse one documentation linkbase into ``{concept_id -> definition}``.

    Mirrors the loc/labelArc/label resolution in ``load_label_map`` but keeps
    only the ReportingDocumentation-role resources. Raises if the file is
    missing or unparseable — a silent skip would ship an empty index that
    looks fine until an agent gets no results.
    """
    if not path.exists():
        raise FileNotFoundError(
            f"Documentation linkbase not found: {path}. The SSM taxonomy under "
            f"SSMxT_2022v1.0/ must be present to (re)generate the definitions index."
        )
    try:
        tree = ET.parse(path)
    except ET.ParseError as exc:
        raise ValueError(f"Could not parse documentation linkbase {path}: {exc}") from exc

    root = tree.getroot()

    # xlink:label -> concept_id (via link:loc)
    loc_to_concept: dict[str, str] = {}
    # xlink:label -> (role, text) for <link:label> resources
    label_resources: dict[str, tuple[str, str]] = {}
    # (from_label, to_label) arcs bind loc -> label
    arcs: list[tuple[str, str]] = []

    for elem in root.iter():
        tag = elem.tag.split("}", 1)[-1]
        if tag == "loc":
            key = elem.get(f"{{{_NS['xlink']}}}label")
            href = elem.get(f"{{{_NS['xlink']}}}href")
            if key and href:
                loc_to_concept[key] = _concept_id_from_href(href)
        elif tag == "label":
            key = elem.get(f"{{{_NS['xlink']}}}label")
            role = elem.get(f"{{{_NS['xlink']}}}role") or ""
            text = (elem.text or "").strip()
            if key and text:
                label_resources[key] = (role, text)
        elif tag == "labelArc":
            frm = elem.get(f"{{{_NS['xlink']}}}from")
            to = elem.get(f"{{{_NS['xlink']}}}to")
            if frm and to:
                arcs.append((frm, to))

    definitions: dict[str, str] = {}
    for frm, to in arcs:
        concept_id = loc_to_concept.get(frm)
        if not concept_id:
            continue
        label_info = label_resources.get(to)
        if not label_info:
            continue
        role, text = label_info
        if not _DOCUMENTATION_ROLE.search(role):
            continue
        # First documentation arc wins; the linkbase carries one per concept.
        definitions.setdefault(concept_id, text)

    return definitions


def build_index(standard: str) -> list[dict[str, str]]:
    """Build the per-standard definition index: a list of concept entries.

    Each entry: ``{concept_id, label, label_normalized, definition}``. The
    label comes from the shared label map (the same text agents see on rows);
    a humanised concept_id is the fallback so every documented concept stays
    searchable.
    """
    if standard not in _DOC_LINKBASES:
        raise ValueError(f"Unknown standard {standard!r}; expected one of {list(_DOC_LINKBASES)}")

    # Merge the per-standard linkbases in priority order — the first file to
    # document a concept_id wins (rep-level filing doc before def-level cor).
    definitions: dict[str, str] = {}
    for path in _DOC_LINKBASES[standard]:
        for concept_id, text in parse_doc_linkbase(path).items():
            definitions.setdefault(concept_id, text)

    label_map = load_label_map()

    entries: list[dict[str, str]] = []
    for concept_id, definition in sorted(definitions.items()):
        label = label_map.get(concept_id) or _humanise_concept_id(concept_id)
        entries.append(
            {
                "concept_id": concept_id,
                "label": label,
                "label_normalized": normalize_label(label),
                "definition": definition,
            }
        )
    return entries


def main() -> None:
    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for standard in _DOC_LINKBASES:
        entries = build_index(standard)
        if not entries:
            # A standard with zero definitions means the linkbase path or role
            # filter is wrong — fail loud rather than commit an empty index.
            raise RuntimeError(
                f"No definitions extracted for {standard}; refusing to write an empty index."
            )
        out_path = _OUTPUT_DIR / f"concept_definitions_{standard}.json"
        out_path.write_text(
            json.dumps(entries, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(f"{standard}: wrote {len(entries)} definitions -> {out_path.relative_to(_REPO_ROOT)}")


if __name__ == "__main__":
    main()
