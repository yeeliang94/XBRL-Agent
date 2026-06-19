"""Standalone "scanned PDF → readable document" conversion.

This subsystem is deliberately independent of the XBRL extraction pipeline
(coordinator, concept model, templates). It takes a PDF and produces readable
HTML using Docling, fully offline. See docs/PRD-scanned-pdf-to-doc.md.
"""

from .converter import convert_pdf_to_html, DocConvertError

__all__ = ["convert_pdf_to_html", "DocConvertError"]
