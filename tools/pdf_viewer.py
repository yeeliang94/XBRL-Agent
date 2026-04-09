from pathlib import Path
from typing import Optional

import fitz  # PyMuPDF


def count_pdf_pages(path: str) -> int:
    doc = fitz.open(path)
    try:
        return len(doc)
    finally:
        doc.close()


def render_pages_to_images(
    path: str,
    start: int = 1,
    end: Optional[int] = None,
    output_dir: Optional[str] = None,
    dpi: int = 200,
) -> list[Path]:
    doc = fitz.open(path)
    try:
        total_pages = len(doc)

        if end is None:
            end = total_pages

        if start < 1 or end > total_pages or start > end:
            raise ValueError(
                f"Invalid page range: {start}-{end}. Document has {total_pages} pages."
            )

        out = Path(output_dir) if output_dir else Path("output/images")
        out.mkdir(parents=True, exist_ok=True)

        images: list[Path] = []
        for page_num in range(start, end + 1):
            page = doc[page_num - 1]
            zoom = dpi / 72
            mat = fitz.Matrix(zoom, zoom)
            pix = page.get_pixmap(matrix=mat)
            img_path = out / f"page_{page_num:03d}.png"
            pix.save(str(img_path))
            images.append(img_path)

        return images
    finally:
        doc.close()


def render_pages_to_png_bytes(
    path: str,
    start: int = 1,
    end: Optional[int] = None,
    dpi: int = 200,
) -> list[bytes]:
    """Render PDF pages directly to PNG bytes.

    This avoids writing temporary page images to disk, which is safer on
    Windows when multiple agents render previews concurrently.
    """
    doc = fitz.open(path)
    try:
        total_pages = len(doc)

        if end is None:
            end = total_pages

        if start < 1 or end > total_pages or start > end:
            raise ValueError(
                f"Invalid page range: {start}-{end}. Document has {total_pages} pages."
            )

        images: list[bytes] = []
        zoom = dpi / 72
        mat = fitz.Matrix(zoom, zoom)
        for page_num in range(start, end + 1):
            page = doc[page_num - 1]
            pix = page.get_pixmap(matrix=mat)
            images.append(pix.tobytes("png"))

        return images
    finally:
        doc.close()
