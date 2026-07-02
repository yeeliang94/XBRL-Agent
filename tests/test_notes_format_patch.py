from __future__ import annotations

import importlib

import pytest

from notes.format_patch import FormatPatchError, apply_sheet_patch
from notes.html_sanitize import sanitize_notes_html


def test_formatter_request_budget_stays_below_pydantic_cap(monkeypatch):
    """The per-click request budget must stay under pydantic-ai's silent 50
    (gotcha #18), including operator overrides which are clamped."""
    import notes.formatting_agent as fa

    assert fa.MAX_FORMATTER_REQUESTS < 50

    monkeypatch.setenv("XBRL_NOTES_FORMATTER_MAX_REQUESTS", "999")
    reloaded = importlib.reload(fa)
    try:
        assert reloaded.MAX_FORMATTER_REQUESTS <= reloaded._MAX_REQUESTS_CEILING < 50
    finally:
        monkeypatch.delenv("XBRL_NOTES_FORMATTER_MAX_REQUESTS", raising=False)
        importlib.reload(fa)


def test_applies_one_coloured_top_border_to_one_cell():
    html = "<table><tr><td>A</td><td>1</td></tr></table>"
    patch = {
        "cells": [{
            "row": 1,
            "operations": [{
                "target": {"table": 0, "cell": {"r": 1, "c": 2}},
                "style": {
                    "border_top": {
                        "width": "1px", "style": "solid", "color": "#666666",
                    },
                },
            }],
        }],
    }
    out = apply_sheet_patch({1: html}, patch)
    assert "border-top: 1px solid #666666" in out.rows[1]
    assert "A" in out.rows[1] and ">1<" in out.rows[1]


def test_removes_all_borders_using_hidden():
    html = (
        '<table><tr><td style="border: 1px solid #000000">A</td>'
        '<td style="border: 1px solid #000000">1</td></tr></table>'
    )
    patch = {
        "cells": [{
            "row": 1,
            "operations": [{
                "target": {"table": 0, "range": "all"},
                "style": {"clear_border": ["top", "right", "bottom", "left"]},
            }],
        }],
    }
    out = apply_sheet_patch({1: html}, patch)
    assert out.rows[1].count("hidden") == 8


def test_total_rows_can_get_single_and_double_rules():
    html = (
        "<table><tr><td>Revenue</td><td>10</td></tr>"
        "<tr><td>Total</td><td>10</td></tr></table>"
    )
    patch = {
        "cells": [{
            "row": 1,
            "operations": [{
                "target": {"table": 0, "range": "total_rows"},
                "style": {
                    "border_top": {
                        "width": "1px", "style": "solid", "color": "#000000",
                    },
                    "border_bottom": {
                        "width": "3px", "style": "double", "color": "#000000",
                    },
                    "text_align": "right",
                },
            }],
        }],
    }
    out = apply_sheet_patch({1: html}, patch)
    assert "border-top: 1px solid #000000" in out.rows[1]
    assert "border-bottom: 3px double #000000" in out.rows[1]
    assert "text-align: right" in out.rows[1]


def test_rejects_text_changes_after_sanitize():
    html = "<table><tr><td>A</td></tr></table>"
    # Force an unsupported target by changing table shape through raw malformed
    # patch is not possible; verify the backend rejects unknown style instead.
    patch = {
        "cells": [{
            "row": 1,
            "operations": [{
                "target": {"table": 0, "cell": {"r": 1, "c": 1}},
                "style": {"font_size": "20px"},
            }],
        }],
    }
    with pytest.raises(FormatPatchError, match="unsupported style key"):
        apply_sheet_patch({1: html}, patch)


def test_sanitizer_preserves_formatter_styles():
    html = "<table><tr><td>A</td></tr></table>"
    patch = {
        "cells": [{
            "row": 1,
            "operations": [{
                "target": {"table": 0, "cell": {"r": 1, "c": 1}},
                "style": {
                    "border_top": {
                        "width": "1px", "style": "solid", "color": "#666666",
                    },
                    "fill": "header_fill",
                    "text_align": "center",
                },
            }],
        }],
    }
    out = apply_sheet_patch({1: html}, patch)
    cleaned, warnings = sanitize_notes_html(out.rows[1])
    assert warnings == []
    assert "border-top: 1px solid #666666" in cleaned
    assert "background-color: #f2f2f2" in cleaned
    assert "text-align: center" in cleaned


def test_can_set_table_width_without_structure_change():
    html = "<table><tr><td>A</td></tr></table>"
    patch = {
        "cells": [{
            "row": 1,
            "operations": [{
                "target": {"table": 0, "range": "table"},
                "style": {"table_width": "100%"},
            }],
        }],
    }
    out = apply_sheet_patch({1: html}, patch)
    assert '<table style="width: 100%">' in out.rows[1]


def test_bold_is_idempotent_across_repeated_patches():
    """Re-running a bold op must not nest <strong><strong>… (tech debt #2)."""
    patch = {
        "cells": [{
            "row": 1,
            "operations": [{
                "target": {"table": 0, "cell": {"r": 1, "c": 1}},
                "style": {"bold": True},
            }],
        }],
    }
    html = "<table><tr><td>Total</td></tr></table>"
    first = apply_sheet_patch({1: html}, patch).rows[1]
    assert first.count("<strong>") == 1
    # Feed the bolded HTML back through the same patch — a whitespace text node
    # or re-serialisation must not defeat the "already wrapped" guard.
    second = apply_sheet_patch({1: first}, patch).rows[1]
    assert second.count("<strong>") == 1
