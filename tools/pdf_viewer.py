from pathlib import Path
from typing import Optional, Tuple

import fitz  # PyMuPDF

# --------------------------------------------------------------------------
# Render policy (PLAN-notes-source-integrity-build Step 1.1)
#
# MEASURED, not assumed. On data/FINCO-Audited-Financial-Statement-2021.pdf
# page 31 the embedded scan is 1648 x 2336 px on an 11.0 x 15.58 in page —
# a 150 DPI source. The historic default renders at 200 DPI (2200 x 3117),
# which INTERPOLATES: no extra detail reaches the model, the PNG grows ~75%,
# and every provider then downscales a full page to a fixed budget anyway
# (roughly 768 px on the short side for OpenAI high detail, 1568 px on the
# long edge for Anthropic). So rendering above the source is pure cost.
#
# `native` fixes that by rendering at min(source DPI, cap). It is opt-in per
# call because this module is shared with scout, the notes reviewer and the
# formatter, and only the notes vision path has opted in so far.
# --------------------------------------------------------------------------
RENDER_POLICY_CAP = "cap"
RENDER_POLICY_NATIVE = "native"

DEFAULT_DPI = 200

# A raster must cover at least this fraction of the page to be treated as
# "the page's image". Guards the trap case: a digital page of vector text
# carrying a small logo. Keying on any embedded image would compute the
# logo's DPI and render the whole text page at it. Several raster TILES also
# fail this test individually, so a tiled page correctly falls back to the cap.
_MIN_DOMINANT_AREA_RATIO = 0.6

# A normalised crop rectangle in page fractions: (x0, y0, x1, y1), each 0..1.
Clip = Tuple[float, float, float, float]


def dominant_raster_dpi(
    page: "fitz.Page", *, min_area_ratio: float = _MIN_DOMINANT_AREA_RATIO
) -> Optional[float]:
    """Native DPI of a raster covering most of ``page``, or None.

    Returns None when the page has no image, only small ones, or several
    partial tiles — every case where "the page's resolution" is not a
    well-defined number and the caller should keep its cap.
    """
    page_area = abs(page.rect.width * page.rect.height)
    if page_area <= 0:
        return None

    best: Optional[float] = None
    for info in page.get_image_info():
        try:
            bbox = fitz.Rect(info["bbox"])
            px_w, px_h = int(info["width"]), int(info["height"])
        except (KeyError, TypeError, ValueError):
            continue
        if bbox.width <= 0 or bbox.height <= 0 or px_w <= 0 or px_h <= 0:
            continue
        if abs(bbox.width * bbox.height) / page_area < min_area_ratio:
            continue
        # Points -> inches is /72. Take the limiting axis: a raster stretched
        # on one axis is only as detailed as its coarser direction.
        dpi = min(px_w / (bbox.width / 72.0), px_h / (bbox.height / 72.0))
        if best is None or dpi > best:
            best = dpi
    return best


def _resolve_dpi_for_page(
    page: "fitz.Page", cap: int, policy: str
) -> tuple[int, str]:
    """Return (dpi, effective_policy). Falls back to the cap when a page has
    no dominant raster, so the reported policy always says what actually ran."""
    if policy != RENDER_POLICY_NATIVE:
        return cap, RENDER_POLICY_CAP
    native = dominant_raster_dpi(page)
    if native is None:
        return cap, RENDER_POLICY_CAP
    # Never above the source (the point of this policy) and never above the
    # cap (so an unusually high-resolution scan can't blow up the payload).
    return max(1, min(int(round(native)), cap)), RENDER_POLICY_NATIVE


def resolve_render_dpi(
    path: str,
    page_num: int,
    *,
    cap: int = DEFAULT_DPI,
    policy: str = RENDER_POLICY_CAP,
) -> tuple[int, str]:
    """Path-based wrapper around `_resolve_dpi_for_page`, 1-indexed page."""
    doc = fitz.open(path)
    try:
        if page_num < 1 or page_num > len(doc):
            raise ValueError(
                f"Invalid page {page_num}. Document has {len(doc)} pages."
            )
        return _resolve_dpi_for_page(doc[page_num - 1], cap, policy)
    finally:
        doc.close()


def _validate_clip(clip: Optional[Clip]) -> Optional[Clip]:
    """Normalise and validate a page-fraction crop, or raise ValueError."""
    if clip is None:
        return None
    try:
        x0, y0, x1, y1 = (float(v) for v in clip)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"clip must be four numbers (x0, y0, x1, y1); got {clip!r}"
        ) from exc
    for name, v in (("x0", x0), ("y0", y0), ("x1", x1), ("y1", y1)):
        if not 0.0 <= v <= 1.0:
            raise ValueError(
                f"clip.{name} must be a page fraction between 0 and 1; got {v}"
            )
    if x0 >= x1 or y0 >= y1:
        raise ValueError(
            f"clip must have x0 < x1 and y0 < y1; got {(x0, y0, x1, y1)}"
        )
    return (x0, y0, x1, y1)


def render_page_png(
    path: str,
    page_num: int,
    *,
    cap: int = DEFAULT_DPI,
    policy: str = RENDER_POLICY_CAP,
    clip: Optional[Clip] = None,
) -> bytes:
    """Render one 1-indexed page to PNG bytes, optionally cropped.

    ``clip`` is in PAGE FRACTIONS, not points, so a caller can ask for "the
    bottom half" without knowing the page size.

    Note what cropping does and does not do: at a fixed DPI a crop has the
    SAME pixel density as the full page — it simply covers less of it. The
    reason a crop can help a vision model is downstream, at the provider's
    fixed-size downscale, where fewer square inches compete for the same
    pixel budget. Whether that produces a better answer is an experiment
    (plan Step 0.1), not something this function should claim.

    Rendering is pure: caching is the caller's job, because the notes path
    coalesces concurrent renders of the same page through its own
    single-flight map.
    """
    rect = _validate_clip(clip)
    doc = fitz.open(path)
    try:
        if page_num < 1 or page_num > len(doc):
            raise ValueError(
                f"Invalid page {page_num}. Document has {len(doc)} pages."
            )
        page = doc[page_num - 1]
        dpi, _ = _resolve_dpi_for_page(page, cap, policy)
        zoom = dpi / 72
        clip_rect = None
        if rect is not None:
            r = page.rect
            clip_rect = fitz.Rect(
                r.x0 + rect[0] * r.width,
                r.y0 + rect[1] * r.height,
                r.x0 + rect[2] * r.width,
                r.y0 + rect[3] * r.height,
            )
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=clip_rect)
        return pix.tobytes("png")
    finally:
        doc.close()


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
