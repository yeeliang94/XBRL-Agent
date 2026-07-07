"""Build a minimal, valid .docx in-process for tests — no python-docx dependency.

A .docx is just a zip of OOXML parts. We hand-write the smallest package
mammoth will parse: content types, package rels, a styles part (so Heading
paragraphs map to <h1>), and a document body with a few note headings, prose,
and a table. Used by the word-input ingest tests (docs/PLAN-word-input.md).
"""
from __future__ import annotations

import zipfile
from pathlib import Path

_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
</Types>"""

_ROOT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"""

_DOC_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>"""

_STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/></w:style>
</w:styles>"""

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _para(text: str, heading: bool = False) -> str:
    ppr = '<w:pPr><w:pStyle w:val="Heading1"/></w:pPr>' if heading else ""
    return f"<w:p>{ppr}<w:r><w:t>{text}</w:t></w:r></w:p>"


def _cell(text: str) -> str:
    return f"<w:tc><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:tc>"


def _row(*cells: str) -> str:
    return "<w:tr>" + "".join(_cell(c) for c in cells) + "</w:tr>"


def _document() -> str:
    body = "".join([
        _para("3. SIGNIFICANT ACCOUNTING POLICIES", heading=True),
        _para("Basis of preparation for note three."),
        _para("4. PROPERTY, PLANT AND EQUIPMENT", heading=True),
        _para("The following table shows the movement in property, plant and equipment."),
        "<w:tbl>" + _row("Cost", "Amount") + _row("Buildings", "1,595") + "</w:tbl>",
        _para("5. REVENUE", heading=True),
        _para("Revenue recognised during the year for note five."),
    ])
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{_W}"><w:body>{body}</w:body></w:document>'
    )


def build_minimal_docx(path: Path) -> Path:
    """Write a minimal valid .docx to ``path`` and return it.

    Contains note headings 3, 4, 5; note 4 carries a 2x2 table (the case the
    notes source-formatting side-channel cares about).
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _CONTENT_TYPES)
        z.writestr("_rels/.rels", _ROOT_RELS)
        z.writestr("word/_rels/document.xml.rels", _DOC_RELS)
        z.writestr("word/styles.xml", _STYLES)
        z.writestr("word/document.xml", _document())
    return path


# --- styled variant (Step 5 injection tests) --------------------------------
def _styled_cell(text: str, *, borders: str = "", jc: str = "") -> str:
    ppr = f"<w:pPr>{jc}</w:pPr>" if jc else ""
    tc_pr = f"<w:tcPr>{borders}</w:tcPr>" if borders else ""
    return f"<w:tc>{tc_pr}<w:p>{ppr}<w:r><w:t>{text}</w:t></w:r></w:p></w:tc>"


def _styled_document() -> str:
    right = '<w:jc w:val="right"/>'
    top_single = ('<w:tcBorders>'
                  '<w:top w:val="single" w:color="000000" w:sz="6"/>'
                  '</w:tcBorders>')
    bottom_double = ('<w:tcBorders>'
                     '<w:top w:val="single" w:color="000000" w:sz="6"/>'
                     '<w:bottom w:val="double" w:color="000000" w:sz="6"/>'
                     '</w:tcBorders>')
    table = (
        "<w:tbl>"
        # header row
        "<w:tr>"
        + _styled_cell("Cost")
        + _styled_cell("Amount", jc=right)
        + "</w:tr>"
        # data row: amount right-aligned, top single rule
        "<w:tr>"
        + _styled_cell("Buildings")
        + _styled_cell("1,595", borders=top_single, jc=right)
        + "</w:tr>"
        # total row: double bottom rule under the amount
        "<w:tr>"
        + _styled_cell("Total")
        + _styled_cell("3,190", borders=bottom_double, jc=right)
        + "</w:tr>"
        "</w:tbl>"
    )
    # A standalone (non-table) paragraph carrying real block formatting:
    # right-aligned, with before/after spacing and a left indent. Exercises the
    # paragraph-injection pass (Codex/HIGH follow-up).
    spaced_para = (
        "<w:p><w:pPr>"
        '<w:jc w:val="right"/>'
        '<w:ind w:left="360"/>'
        '<w:spacing w:before="120" w:after="240"/>'
        "</w:pPr><w:r><w:t>Approved by the board.</w:t></w:r></w:p>"
    )
    body = "".join([
        _para("4. PROPERTY, PLANT AND EQUIPMENT", heading=True),
        _para("The movement in property, plant and equipment."),
        table,
        spaced_para,
    ])
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{_W}"><w:body>{body}</w:body></w:document>'
    )


def build_styled_docx(path: Path) -> Path:
    """A valid .docx whose note-4 table carries real visual styling: a
    right-aligned amount column, a single top rule, and a double bottom rule on
    the total row. Used to prove Step-5 style injection carries those into
    source.html."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _CONTENT_TYPES)
        z.writestr("_rels/.rels", _ROOT_RELS)
        z.writestr("word/_rels/document.xml.rels", _DOC_RELS)
        z.writestr("word/styles.xml", _STYLES)
        z.writestr("word/document.xml", _styled_document())
    return path
