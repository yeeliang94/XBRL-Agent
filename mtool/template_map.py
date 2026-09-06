"""Resolve canonical filing facts onto an uploaded workbook.

This is the semantic seam between the application and mTool.  Taxonomy
concept identifiers and dimensions are the primary address.  Visible labels
and positional columns remain a compatibility fallback for workbooks that do
not expose those identifiers.

The module is deliberately read-only with respect to workbooks.  It produces
ordinary ``offline_fill`` instructions; the standard-library patcher remains
the only code that writes an mTool file.
"""
from __future__ import annotations

from collections import defaultdict
import hashlib
import json
import re
from typing import Any

from mtool.column_detect import (
    describe_template,
    detect_column_map,
    fingerprint_workbook,
)
from mtool.exporter import apply_column_map
from mtool.offline_fill import (
    get_shared_strings,
    get_sheet_paths,
    load_workbook_entries,
    read_sheet_cells,
)


# mTool stores filing identities as XSD hrefs, sometimes several in one cell
# (``table::axis::member``) and sometimes followed by an ``@label-role``.
# Index ONLY that closed syntax.  Splitting arbitrary text on ``#`` would turn
# labels or internal keys into filing identities and weaken the exact-address
# gate this module exists to enforce.
_TAXONOMY_HREF_RE = re.compile(
    r"[A-Za-z0-9_.-]+\.xsd#([A-Za-z_][A-Za-z0-9_.-]*)"
)


def _taxonomy_identifiers(text: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(_TAXONOMY_HREF_RE.findall(text)))


def index_workbook(data: dict) -> tuple[dict, dict]:
    """Return exact text/taxonomy occurrences and readable worksheet cells.

    Full stripped cell values remain indexed for compatibility.  Valid XSD
    href fragments are indexed additionally because canonical semantic
    addresses use the taxonomy element id, not mTool's serialised href.
    """
    paths = get_sheet_paths(data)
    shared = get_shared_strings(data)
    occurrences: dict[str, list[tuple[str, int, str]]] = defaultdict(list)
    by_sheet: dict[str, dict] = {}
    for sheet, entry in paths.items():
        cells = read_sheet_cells(data[entry], shared)
        by_sheet[sheet] = cells
        for row, row_cells in cells.items():
            for col, (_kind, raw) in row_cells.items():
                text = (raw or "").strip()
                if text:
                    occurrences[text].append((sheet, int(row), col))
                    for identifier in _taxonomy_identifiers(text):
                        occurrences[identifier].append(
                            (sheet, int(row), col))
    return occurrences, by_sheet


def inspect_template(
    template_path: str,
    doc: dict[str, Any],
    *,
    data: dict | None = None,
    workbook_index: tuple[dict, dict] | None = None,
) -> dict[str, Any]:
    """Inspect one workbook and report its semantic filing capability."""
    if data is None:
        _, data, _ = load_workbook_entries(template_path)
    fingerprint = fingerprint_workbook(data)
    descriptor = describe_template(fingerprint)
    occurrences, cells_by_sheet = workbook_index or index_workbook(data)
    requested = len(doc.get("writes", []))
    addressable = 0
    missing_address = 0
    for write in doc.get("writes", []):
        address = write.get("semantic_address") or {}
        primary = address.get("primary_concept")
        if primary and occurrences.get(primary):
            addressable += 1
        elif primary:
            missing_address += 1

    meta = doc.get("meta", {})
    expected_standard = str(meta.get("filing_standard") or "").lower()
    expected_level = str(meta.get("filing_level") or "").lower()
    family_match = not descriptor or (
        (not expected_standard or expected_standard in descriptor.get("filing_standards", []))
        and (not expected_level or expected_level in descriptor.get("filing_levels", []))
    )
    if descriptor and descriptor.get("source") == "generated":
        expected_statements = {
            str(write.get("template_id") or "").split("-")[2].upper()
            for write in doc.get("writes", [])
            if len(str(write.get("template_id") or "").split("-")) >= 3
        }
        descriptor_tokens = {
            token for token in
            str(descriptor.get("name") or "").upper().split("-")
            if token
        }
        family_match = family_match and all(
            statement in descriptor_tokens for statement in expected_statements)
    generated = bool(
        descriptor and descriptor.get("source") == "generated" and family_match)
    semantic_source = "generated-targets" if generated else (
        "taxonomy-identifiers" if addressable else "legacy-labels")
    compatibility = "verified-generated" if generated else (
        "candidate-2.2" if addressable else "unverified")
    return {
        "fingerprint": fingerprint,
        "template": descriptor,
        "semantic_source": semantic_source,
        "mtool_compatibility": compatibility,
        "supported_mtool_version": "2.2",
        "filing_family_match": family_match,
        "requested": requested,
        "identifier_addressable": addressable,
        "identifier_missing": missing_address,
        "column_map": detect_column_map(
            template_path, doc, data=data, cells_by_sheet=cells_by_sheet
        ),
    }


def _marker_rows(cells: dict, marker: str) -> list[int]:
    return sorted(
        row
        for row, row_cells in cells.items()
        if any((raw or "").strip() == marker
               for _kind, raw in row_cells.values())
    )


def _parse_marker_date(text: str) -> tuple[int, int, int] | None:
    match = re.fullmatch(r"(\d{2})/(\d{2})/(\d{4})", (text or "").strip())
    if not match:
        return None
    day, month, year = (int(part) for part in match.groups())
    return year, month, day


def _dimensional_period_blocks(cells: dict) -> list[dict[str, Any]]:
    """Identify dimensional row blocks and classify them as CY/PY by date.

    A genuine mTool SOCIE sheet repeats the same primary rows for current and
    prior periods.  ``#DOM#`` starts each category block and the following
    ``#ENDT#`` row declares that block's reporting date.  Comparing those
    dates is the same evidence-backed rule used by column detection.
    """
    dom_rows = _marker_rows(cells, "#DOM#")
    end_rows = _marker_rows(cells, "#ENDT#")
    raw_blocks: list[dict[str, Any]] = []
    for index, dom_row in enumerate(dom_rows):
        next_dom = dom_rows[index + 1] if index + 1 < len(dom_rows) else None
        relevant_end_rows = [
            row for row in end_rows
            if row >= dom_row and (next_dom is None or row < next_dom)
        ]
        column_dates: dict[str, set] = defaultdict(set)
        for row in relevant_end_rows:
            for col, (_kind, raw) in cells.get(row, {}).items():
                if (parsed := _parse_marker_date(raw)) is not None:
                    column_dates[col].add(parsed)
        dates = {date for values in column_dates.values() for date in values}
        raw_blocks.append({
            "dom_row": dom_row,
            "previous_dom_row": dom_rows[index - 1] if index else None,
            "next_dom_row": next_dom,
            "end_date": max(dates) if dates else None,
            "column_dates": dict(column_dates),
        })

    ordered_dates = sorted(
        {date for block in raw_blocks for dates in block["column_dates"].values() for date in dates},
        reverse=True,
    )
    for block in raw_blocks:
        block["column_periods"] = {
            col: ("current_year" if next(iter(dates)) == ordered_dates[0]
                  else "prior_year" if len(ordered_dates) > 1 and next(iter(dates)) == ordered_dates[1]
                  else None)
            for col, dates in block["column_dates"].items() if len(dates) == 1
        }
        block["period_roles"] = set(block["column_periods"].values()) - {None}
        date = block["end_date"]
        block["period_role"] = (
            "current_year" if ordered_dates and date == ordered_dates[0]
            else "prior_year" if len(ordered_dates) > 1 and date == ordered_dates[1]
            else None
        )
    return raw_blocks


def _block_for_primary_row(
    blocks: list[dict[str, Any]], row: int,
) -> dict[str, Any] | None:
    candidates = [block for block in blocks if block["dom_row"] <= row]
    if not candidates:
        return None
    block = candidates[-1]
    next_dom = block["next_dom_row"]
    return block if next_dom is None or row < next_dom else None


def _write_period_role(write: dict[str, Any]) -> str | None:
    role = str(write.get("column_role") or "")
    if role.endswith("current_year"):
        return "current_year"
    if role.endswith("prior_year"):
        return "prior_year"
    period = str(write.get("period") or "").upper()
    if period == "CY":
        return "current_year"
    if period == "PY":
        return "prior_year"
    return None


def _dimension_column(
    occurrences: dict[str, list[tuple[str, int, str]]],
    dimensions: dict[str, str],
    *,
    sheet: str,
    block: dict[str, Any] | None = None,
    period: str | None = None,
) -> str | None:
    if not dimensions:
        return None
    members = list(dimensions.values())
    columns: set[str] | None = None
    for member in members:
        found = set()
        for found_sheet, row, col in occurrences.get(member, []):
            if found_sheet != sheet:
                continue
            if block is not None:
                if period and block.get("column_periods", {}).get(col) != period:
                    continue
                # Dimension-member headers precede their ``#DOM#`` marker.
                # Assign an occurrence to the next category block, bounded by
                # the previous marker so a later block cannot leak backward.
                if row > block["dom_row"]:
                    continue
                previous_dom = block["previous_dom_row"]
                if previous_dom is not None and row <= previous_dom:
                    continue
            found.add(col)
        columns = found if columns is None else columns & found
    if columns and len(columns) == 1:
        return next(iter(columns))
    return None


def _coverage_issue(
    write: dict[str, Any],
    primary: str,
    reason_code: str,
    detail: str,
    *,
    candidates: list[str] | None = None,
) -> dict[str, Any]:
    issue = {
        "concept_uuid": write.get("concept_uuid"),
        "primary_concept": primary,
        "sheet": write.get("sheet"),
        "label": write.get("label"),
        "reason_code": reason_code,
        "detail": detail,
        "period": write.get("period"),
        "entity_scope": write.get("entity_scope"),
        "value": write.get("value"),
        "dimensions": (write.get("semantic_address") or {}).get("dimensions") or {},
    }
    if candidates is not None:
        issue["candidates"] = candidates
    return issue


def _resolution_options(
    write: dict[str, Any],
    issue: dict[str, Any],
    occurrences: dict[str, list[tuple[str, int, str]]],
    cells_by_sheet: dict[str, dict],
    period_blocks: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Offer only taxonomy-addressed cells; never arbitrary operator coordinates.

    Missing categories are decisions about the source figure, not defaults we
    can infer from a workbook. Expose the explicit axis/member headers for
    confirmation, restricted to the fact's sheet and reporting-period block.
    """
    from concept_model.taxonomy_semantics import taxonomy_concept

    sheet = write.get("sheet")
    cells = cells_by_sheet.get(sheet, {})
    options = {}
    if issue["reason_code"] == "missing_category_dimensions":
        primary = (write.get("semantic_address") or {}).get("primary_concept")
        candidates = [c for c in occurrences.get(primary, []) if c[0] == sheet]
        blocks = period_blocks.get(sheet, [])
        candidates, period_issue = _filter_candidates_for_period(candidates, write, blocks)
        if period_issue:
            return []
        for _, row, _ in candidates:
            block = _block_for_primary_row(blocks, row)
            if block is None:
                continue
            for header_row, header_cells in cells.items():
                if header_row > block["dom_row"] or (
                    block["previous_dom_row"] is not None
                    and header_row <= block["previous_dom_row"]
                ):
                    continue
                for col, (_, raw) in header_cells.items():
                    ids = _taxonomy_identifiers(raw or "")
                    axes = [i for i in ids if (c := taxonomy_concept(i))
                            and (c.substitution_group or "").endswith("dimensionItem")]
                    members = [i for i in ids if (c := taxonomy_concept(i))
                               and c.local_name.endswith("Member")]
                    # Composite headers encode one table::axis::member tuple.
                    # More complex headers stay blocked rather than pairing by order.
                    if len(axes) != 1 or len(members) != 1:
                        continue
                    if cells.get(row, {}).get(col, (None,))[0] == "F":
                        continue
                    if block.get("column_periods", {}).get(col) != _write_period_role(write):
                        continue
                    cell = f"{sheet}!{col}{row}"
                    dims = {axes[0]: members[0]}
                    member_label = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ",
                                          members[0].split("_", 1)[-1].removesuffix("Member"))
                    options[cell] = {"cell": cell, "dimensions": dims,
                                     "label": f"{member_label} · {cell}"}
    else:
        for cell in issue.get("candidates", []):
            candidate_sheet, ref = cell.rsplit("!", 1)
            match = re.fullmatch(r"([A-Z]+)([0-9]+)", ref)
            if candidate_sheet != sheet or not match:
                continue
            col, row = match.group(1), int(match.group(2))
            if cells.get(row, {}).get(col, (None,))[0] == "F":
                continue
            # Include the actual row text and label-role URI so opening and
            # closing balances are distinguishable without inventing labels.
            row_text = " · ".join(raw for kind, raw in cells.get(row, {}).values()
                                  if kind == "S" and raw and not _taxonomy_identifiers(raw))
            options[cell] = {"cell": cell, "dimensions": issue["dimensions"],
                             "label": f"{cell} · {row_text}"}
    return list(options.values())


def _filter_candidates_for_period(
    candidates: list[tuple[str, int, str]],
    write: dict[str, Any],
    blocks: list[dict[str, Any]],
) -> tuple[list[tuple[str, int, str]], dict[str, Any] | None]:
    desired_period = _write_period_role(write)
    if not desired_period or not blocks:
        return candidates, None

    period_name = (
        "current-year" if desired_period == "current_year" else "prior-year"
    )
    available_periods = {period for block in blocks for period in block["period_roles"]}
    if desired_period not in available_periods:
        if available_periods:
            detail = (
                f"This template sheet has no {period_name} section. "
                "The figure was not written."
            )
            reason_code = "template_period_section_missing"
        else:
            detail = (
                "The template's dimensional period markers do not identify "
                f"a {period_name} section. The figure was not written."
            )
            reason_code = "template_period_markers_unresolved"
        return [], _coverage_issue(
            write,
            str((write.get("semantic_address") or {}).get("primary_concept") or ""),
            reason_code,
            detail,
        )

    filtered = [
        item for item in candidates
        if (
            (block := _block_for_primary_row(blocks, item[1])) is not None
            and desired_period in block["period_roles"]
        )
    ]
    if candidates and not filtered:
        return [], _coverage_issue(
            write,
            str((write.get("semantic_address") or {}).get("primary_concept") or ""),
            "taxonomy_identifier_missing_for_period",
            (
                "The figure's taxonomy identifier was not found in the "
                f"template's {period_name} section. The figure was not written."
            ),
        )
    return filtered, None


def _resolve_taxonomy_target(
    write: dict[str, Any],
    primary: str,
    occurrences: dict[str, list[tuple[str, int, str]]],
    detected: dict[str, dict[str, Any]],
    cmap: dict[str, dict[str, Any]],
    period_blocks: dict[str, list[dict[str, Any]]],
) -> tuple[
    tuple[str, int, str] | None,
    dict[str, Any] | None,
    dict[str, Any] | None,
]:
    all_candidates = list(occurrences.get(primary, []))
    expected_sheet = write.get("sheet")
    dimensions = (write.get("semantic_address") or {}).get("dimensions") or {}
    dimensional = bool(detected.get(expected_sheet, {}).get("dimensional"))
    if dimensional and not dimensions:
        return None, _coverage_issue(
            write,
            primary,
            "missing_category_dimensions",
            (
                "This category-based sheet requires a taxonomy category "
                "dimension (such as share class or related-party category), "
                "but this run figure has none. The figure was not written."
            ),
        ), None

    candidates = (
        [item for item in all_candidates if item[0] == expected_sheet]
        if expected_sheet else all_candidates
    )
    if expected_sheet and all_candidates and not candidates:
        return None, _coverage_issue(
            write,
            primary,
            "taxonomy_identifier_missing_on_sheet",
            (
                "The figure's taxonomy identifier was not found on the "
                "expected template sheet. The figure was not written."
            ),
        ), None

    if dimensional and candidates:
        candidates, period_issue = _filter_candidates_for_period(
            candidates, write, period_blocks.get(expected_sheet, []),
        )
        if period_issue:
            return None, period_issue, None

    if dimensions:
        narrowed = []
        for candidate_sheet, row, _concept_col in candidates:
            block = _block_for_primary_row(
                period_blocks.get(candidate_sheet, []), row,
            )
            col = _dimension_column(
                occurrences, dimensions, sheet=candidate_sheet, block=block,
                period=_write_period_role(write),
            )
            if col:
                narrowed.append((candidate_sheet, row, col))
        candidates = narrowed
    else:
        role = write.get("column_role")
        candidates = [
            (
                candidate_sheet,
                row,
                cmap.get(candidate_sheet, {}).get("columns", {}).get(role),
            )
            for candidate_sheet, row, _concept_col in candidates
        ]
        candidates = [item for item in candidates if item[2]]

    unique = list(dict.fromkeys(candidates))
    if len(unique) == 1:
        return unique[0], None, None
    if len(unique) > 1:
        return None, None, _coverage_issue(
            write,
            primary,
            "ambiguous_taxonomy_target",
            "More than one taxonomy cell matched this figure; nothing was written.",
            candidates=[f"{sheet}!{col}{row}" for sheet, row, col in unique],
        )
    return None, None, None


def resolve_filing_doc(
    template_path: str,
    doc: dict[str, Any],
    *,
    data: dict | None = None,
    column_map: dict[str, dict[str, Any]] | None = None,
    workbook_index: tuple[dict, dict] | None = None,
    filing_targets: dict[str, str] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Resolve writes and return ``(offline_doc, coverage_report)``.

    Generated repository templates use their exact canonical target hints.
    An SSM workbook is resolved by its embedded taxonomy identifiers; SOCIE
    adds the equity-component dimension to select the column.  Anything else
    goes through the existing label/column adapter and is explicitly reported
    as degraded.
    """
    if data is None:
        _, data, _ = load_workbook_entries(template_path)
    workbook_index = workbook_index or index_workbook(data)
    inspection = inspect_template(
        template_path, doc, data=data, workbook_index=workbook_index
    )
    occurrences, cells_by_sheet = workbook_index
    filing_targets = {} if filing_targets is None else filing_targets
    if not isinstance(filing_targets, dict) or any(
        not isinstance(k, str) or not isinstance(v, str)
        for k, v in filing_targets.items()
    ):
        raise ValueError("filing_targets must map resolution keys to destinations")
    workbook_revision = hashlib.sha256(
        json.dumps(cells_by_sheet, sort_keys=True).encode()
    ).hexdigest()
    used_selections = set()
    operator_resolutions = []
    descriptor = inspection.get("template") or {}
    generated = descriptor.get("source") == "generated"
    if not inspection["filing_family_match"]:
        requested = len(doc.get("writes", []))
        report = {
            "status": "blocked", "requested": requested, "mapped": 0,
            "unmapped": requested, "ambiguous": 0, "legacy_label_writes": 0,
            "coverage_percent": 0.0 if requested else 100.0,
            "unresolved_writes": [{
                "reason_code": "template_filing_family_mismatch",
                "detail": "template filing family or statement does not match the run; "
                          "expected sheets: " + ", ".join(sorted(doc.get("sheets", {})))
            }],
            "ambiguous_writes": [], "inspection": inspection,
        }
        return {**doc, "writes": []}, report
    detected = inspection["column_map"]
    period_blocks = {
        sheet: _dimensional_period_blocks(cells_by_sheet.get(sheet, {}))
        for sheet, cfg in detected.items()
        if cfg.get("dimensional")
    }
    cmap = column_map or {
        sheet: {
            "label_column": cfg.get("label_column"),
            "columns": cfg.get("columns", {}),
        }
        for sheet, cfg in detected.items()
    }

    resolved: list[dict[str, Any]] = []
    resolved_sources: list[dict[str, Any]] = []
    resolution_contexts: dict[int, dict[str, Any]] = {}
    unresolved: list[dict[str, Any]] = []
    ambiguous: list[dict[str, Any]] = []
    legacy: list[dict[str, Any]] = []
    for write in doc.get("writes", []):
        hint = write.get("target_hint") or {}
        address = write.get("semantic_address") or {}
        primary = address.get("primary_concept")
        target: tuple[str, int, str] | None = None

        if generated and hint.get("sheet") and hint.get("row") and hint.get("col"):
            target = (hint["sheet"], int(hint["row"]), hint["col"])
        elif primary:
            target, unresolved_issue, ambiguous_issue = _resolve_taxonomy_target(
                write, primary, occurrences, detected, cmap, period_blocks,
            )
            issue = unresolved_issue or ambiguous_issue
            if issue:
                options = _resolution_options(
                    write, issue, occurrences, cells_by_sheet, period_blocks,
                )
                # Taxonomy identity, dimensions and period have already
                # constrained this set. An exact visible label can distinguish
                # repeated opening/closing rows; ties still require review.
                exact = []
                if ambiguous_issue and write.get("label"):
                    label_col = detected.get(write.get("sheet"), {}).get("label_column")
                    expected_label = str(write["label"]).strip().lstrip("*").strip()
                    for option in options:
                        sheet, ref = option["cell"].rsplit("!", 1)
                        row = int(re.search(r"\d+$", ref).group())
                        raw = cells_by_sheet[sheet].get(row, {}).get(label_col, (None, ""))[1]
                        if (raw or "").strip().lstrip("*").strip() == expected_label:
                            exact.append(option)
                if len(exact) == 1:
                    sheet, ref = exact[0]["cell"].rsplit("!", 1)
                    match = re.fullmatch(r"([A-Z]+)([0-9]+)", ref)
                    target = (sheet, int(match.group(2)), match.group(1))
                    # Continue through the shared write/duplicate checks below.
                    issue = None
                key = hashlib.sha256(json.dumps(
                    [workbook_revision, write], sort_keys=True,
                ).encode()).hexdigest()
                if issue is not None:
                    issue.update(resolution_key=key, resolution_options=options)
                    resolution_contexts[id(write)] = dict(issue)
                selected = filing_targets.get(key)
                if issue is not None and selected is not None:
                    option = next((o for o in options if o["cell"] == selected), None)
                    if option is None:
                        raise ValueError("Selected filing destination is not a verified candidate")
                    selected_sheet, ref = selected.rsplit("!", 1)
                    match = re.fullmatch(r"([A-Z]+)([0-9]+)", ref)
                    target = (selected_sheet, int(match.group(2)), match.group(1))
                    used_selections.add(key)
                    operator_resolutions.append({
                        **_coverage_issue(write, primary, "operator_confirmed_destination",
                                          "Operator confirmed the taxonomy destination."),
                        "cell": option["cell"], "dimensions": option["dimensions"],
                        "destination_label": option["label"], "resolution_key": key,
                    })
                elif issue is not None:
                    (unresolved if unresolved_issue else ambiguous).append(issue)
                    continue

        if target:
            sheet, row, col = target
            item = {"sheet": sheet, "cell": f"{col}{row}",
                    "value": write["value"]}
            # Reverse ingest uses these non-patcher metadata fields to join a
            # resolved cell back to its canonical fact slot.  offline_fill
            # intentionally ignores unknown keys.
            for key in ("concept_uuid", "period", "entity_scope", "template_id"):
                if key in write:
                    item[key] = write[key]
            resolved.append(item)
            resolved_sources.append(write)
        elif not primary or inspection["semantic_source"] == "legacy-labels":
            sheet = write.get("sheet")
            if detected.get(sheet, {}).get("dimensional"):
                unresolved.append({
                    "concept_uuid": write.get("concept_uuid"),
                    "primary_concept": primary,
                    "sheet": sheet,
                    "label": write.get("label"),
                    "reason_code": (
                        "missing_taxonomy_address" if not primary
                        else "template_taxonomy_identifiers_missing"
                    ),
                    "detail": (
                        "This category-based sheet needs an exact taxonomy "
                        "address to choose the correct category column. The "
                        "figure was not written; use a template that exposes "
                        "the figure's taxonomy identifiers."
                    ),
                })
            else:
                legacy.append(write)
        else:
            unresolved.append({
                "concept_uuid": write.get("concept_uuid"),
                "primary_concept": primary,
                "sheet": write.get("sheet"),
                "label": write.get("label"),
                "reason_code": "taxonomy_target_unresolved",
                "detail": (
                    "The figure's taxonomy address did not resolve to one "
                    "unique template cell."
                ),
            })

    legacy_ready: dict[str, Any] | None = None
    if legacy:
        legacy_doc = dict(doc)
        legacy_doc["writes"] = legacy
        legacy_sheets = {write.get("sheet") for write in legacy}
        legacy_doc["sheets"] = {
            sheet: cfg for sheet, cfg in doc.get("sheets", {}).items()
            if sheet in legacy_sheets
        }
        legacy_ready = apply_column_map(legacy_doc, cmap)
        resolved.extend(legacy_ready["writes"])
        resolved_sources.extend(legacy)

    if set(filing_targets) - used_selections:
        raise ValueError("Filing selections are stale; recheck the current figures and template")
    occupied: dict[tuple[str, str], list[int]] = defaultdict(list)
    for index, item in enumerate(resolved):
        if "cell" not in item:
            continue
        destination = (item["sheet"], item["cell"])
        occupied[destination].append(index)
    collisions = set()
    for (sheet, cell), indices in occupied.items():
        if len(indices) < 2:
            continue
        for index in indices:
            write = resolved_sources[index]
            issue = _coverage_issue(
                write, (write.get("semantic_address") or {}).get("primary_concept"),
                "destination_collision",
                f"{len(indices)} figures target {sheet}!{cell}. Choose distinct destinations or correct the source figures; none of these figures was written.",
                candidates=[f"{sheet}!{cell}"],
            )
            context = resolution_contexts.get(id(write), {})
            for key in ("resolution_key", "resolution_options"):
                if key in context:
                    issue[key] = context[key]
            unresolved.append(issue)
            collisions.add(index)
    resolved = [item for index, item in enumerate(resolved) if index not in collisions]

    out = dict(doc)
    out["writes"] = resolved
    out["sheets"] = legacy_ready["sheets"] if legacy_ready else {}
    out_meta = dict(doc.get("meta", {}))
    out_meta["columns_unresolved"] = False
    out["meta"] = out_meta

    requested = len(doc.get("writes", []))
    mapped = len(resolved)
    status = "ready"
    if unresolved or ambiguous:
        status = "blocked"
    elif legacy or operator_resolutions or inspection["mtool_compatibility"] == "candidate-2.2":
        status = "attention"
    report = {
        "status": status,
        "requested": requested,
        "mapped": mapped,
        "unmapped": len(unresolved),
        "ambiguous": len(ambiguous),
        "legacy_label_writes": len(legacy),
        "coverage_percent": round(mapped * 100 / requested, 1) if requested else 100.0,
        "unresolved_writes": unresolved,
        "ambiguous_writes": ambiguous,
        "inspection": inspection,
        "operator_resolutions": operator_resolutions,
    }
    return out, report


__all__ = ["index_workbook", "inspect_template", "resolve_filing_doc"]
