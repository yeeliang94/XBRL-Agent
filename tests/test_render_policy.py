"""Render-policy tests — PLAN-notes-source-integrity-build Phase 1, Steps 1.1/1.2.

Two invariants live here:

1. **Never upsample past the source.** A scanned page carries a raster at a
   fixed native resolution. Rendering above it invents pixels: the PNG grows,
   the model sees nothing new (providers downscale a full page to a fixed
   budget regardless). Measured on the FINCO fixture the scan is 150 DPI while
   the pipeline default renders at 200.

2. **A page with no dominant raster keeps the cap.** ``render_pages_to_png_bytes``
   is shared with scout, the notes reviewer and the formatter. "The page's
   embedded image" is undefined for a vector page carrying a small logo, so
   native detection must require one raster covering most of the page and fall
   back to the cap otherwise. Getting this wrong would render a text page at a
   logo's effective DPI.
"""
import fitz
import pytest
from pathlib import Path

from tools import page_cache
from tools.pdf_viewer import (
    RENDER_POLICY_CAP,
    RENDER_POLICY_NATIVE,
    dominant_raster_dpi,
    render_page_png,
    render_pages_to_png_bytes,
    resolve_render_dpi,
)

_SAMPLE_PDF = Path("data/FINCO-Audited-Financial-Statement-2021.pdf")
_needs_sample = pytest.mark.skipif(
    not _SAMPLE_PDF.exists(),
    reason="Sample PDF not in repo — run with local data only",
)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _vector_page_with_logo(tmp_path: Path) -> Path:
    """A text page carrying one small raster — the trap case for native DPI.

    If detection keyed on "the page's embedded image" it would compute the
    logo's DPI and render the whole text page at it.
    """
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)  # A4 points
    page.insert_text((72, 100), "Notes to the financial statements", fontsize=12)
    # 20x20 px raster drawn into a 40x40 pt box => ~36 DPI if mistaken for
    # the dominant image, well below the 200 cap.
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 20, 20))
    pix.clear_with(128)
    page.insert_image(fitz.Rect(500, 40, 540, 80), pixmap=pix)
    out = tmp_path / "vector_with_logo.pdf"
    doc.save(str(out))
    doc.close()
    return out


# --------------------------------------------------------------------------
# Step 1.1 — dominant raster detection
# --------------------------------------------------------------------------

@_needs_sample
def test_dominant_raster_dpi_reads_the_scan_native_resolution():
    doc = fitz.open(str(_SAMPLE_PDF))
    try:
        dpi = dominant_raster_dpi(doc[30])
    finally:
        doc.close()
    # 1648 px across an 11.0 in page.
    assert dpi is not None
    assert 145 <= dpi <= 155, f"expected ~150 DPI, got {dpi}"


def test_dominant_raster_dpi_ignores_a_small_logo(tmp_path):
    path = _vector_page_with_logo(tmp_path)
    doc = fitz.open(str(path))
    try:
        assert dominant_raster_dpi(doc[0]) is None
    finally:
        doc.close()


def test_dominant_raster_dpi_is_none_for_a_blank_page(tmp_path):
    doc = fitz.open()
    doc.new_page(width=595, height=842)
    out = tmp_path / "blank.pdf"
    doc.save(str(out))
    doc.close()
    doc2 = fitz.open(str(out))
    try:
        assert dominant_raster_dpi(doc2[0]) is None
    finally:
        doc2.close()


# --------------------------------------------------------------------------
# Step 1.1 — resolved DPI never exceeds the source or the cap
# --------------------------------------------------------------------------

@_needs_sample
def test_native_policy_drops_to_the_scan_resolution():
    dpi, policy = resolve_render_dpi(
        str(_SAMPLE_PDF), 31, cap=200, policy=RENDER_POLICY_NATIVE
    )
    assert 145 <= dpi <= 155
    assert policy == RENDER_POLICY_NATIVE


@_needs_sample
def test_native_policy_never_exceeds_the_cap():
    """A 150 DPI scan under a 72 DPI cap must render at 72, not 150."""
    dpi, _ = resolve_render_dpi(
        str(_SAMPLE_PDF), 31, cap=72, policy=RENDER_POLICY_NATIVE
    )
    assert dpi == 72


@_needs_sample
def test_cap_policy_is_unchanged_by_this_work():
    dpi, policy = resolve_render_dpi(
        str(_SAMPLE_PDF), 31, cap=200, policy=RENDER_POLICY_CAP
    )
    assert dpi == 200
    assert policy == RENDER_POLICY_CAP


def test_vector_page_keeps_the_cap_under_native_policy(tmp_path):
    path = _vector_page_with_logo(tmp_path)
    dpi, policy = resolve_render_dpi(str(path), 1, cap=200, policy=RENDER_POLICY_NATIVE)
    assert dpi == 200
    # Reported policy degrades to `cap` so telemetry shows what actually ran.
    assert policy == RENDER_POLICY_CAP


@_needs_sample
def test_native_render_matches_the_scan_pixel_width():
    """~1648 px, not the historic 2200.

    Not exactly 1648: the scan's bbox is 791.0 pt on a 792.0 pt page, so
    rendering the whole PAGE at the scan's 150 DPI yields 1650 px. The two
    extra pixels are page margin outside the image. What matters is that the
    render tracks the source resolution instead of interpolating up to 2200.
    """
    png = render_page_png(str(_SAMPLE_PDF), 31, cap=200, policy=RENDER_POLICY_NATIVE)
    pix = fitz.Pixmap(png)
    assert pix.width == pytest.approx(1648, abs=4), (
        f"expected ~1648 px (the scan's own resolution), got {pix.width}"
    )
    assert pix.width < 2200, "must not upsample to the historic 200 DPI render"


@_needs_sample
def test_native_render_is_smaller_than_the_upsampled_one():
    native = render_page_png(str(_SAMPLE_PDF), 31, cap=200, policy=RENDER_POLICY_NATIVE)
    capped = render_page_png(str(_SAMPLE_PDF), 31, cap=200, policy=RENDER_POLICY_CAP)
    assert len(native) < len(capped)


# --------------------------------------------------------------------------
# Step 1.2 — cropped render mechanics
#
# Only the MECHANICS are pinned here. Whether a crop makes the model read a
# table better is the Phase 0.1 experiment, and Phase 0 has to stay able to
# disprove it — so no test asserts that.
# --------------------------------------------------------------------------

@_needs_sample
def test_clip_renders_only_the_requested_region():
    full = render_page_png(str(_SAMPLE_PDF), 31, cap=200, policy=RENDER_POLICY_CAP)
    half = render_page_png(
        str(_SAMPLE_PDF), 31, cap=200, policy=RENDER_POLICY_CAP,
        clip=(0.0, 0.0, 1.0, 0.5),
    )
    f, h = fitz.Pixmap(full), fitz.Pixmap(half)
    assert h.width == pytest.approx(f.width, abs=2)
    assert h.height == pytest.approx(f.height / 2, abs=2)


@_needs_sample
def test_clip_preserves_pixel_density():
    """Cropping does not change DPI — it changes how much page the pixels cover.

    Pinning this stops the rev-1 error (a crop described as "double the pixels
    per inch") from coming back.
    """
    full = fitz.Pixmap(render_page_png(str(_SAMPLE_PDF), 31, cap=200,
                                       policy=RENDER_POLICY_CAP))
    quarter = fitz.Pixmap(render_page_png(str(_SAMPLE_PDF), 31, cap=200,
                                          policy=RENDER_POLICY_CAP,
                                          clip=(0.0, 0.0, 0.5, 0.5)))
    assert quarter.width == pytest.approx(full.width / 2, abs=2)
    assert quarter.height == pytest.approx(full.height / 2, abs=2)


@_needs_sample
def test_clip_is_validated():
    for bad in [(-0.1, 0, 1, 1), (0, 0, 1.5, 1), (0.6, 0, 0.4, 1), (0, 0, 1, 0)]:
        with pytest.raises(ValueError):
            render_page_png(str(_SAMPLE_PDF), 31, clip=bad)


# --------------------------------------------------------------------------
# Step 1.1/1.2 — cache keys carry policy and clip
# --------------------------------------------------------------------------

@_needs_sample
def test_two_clips_do_not_share_a_cache_entry():
    page_cache.reset()
    a = render_page_png(str(_SAMPLE_PDF), 31, clip=(0.0, 0.0, 1.0, 0.5))
    b = render_page_png(str(_SAMPLE_PDF), 31, clip=(0.0, 0.5, 1.0, 1.0))
    assert a != b, "top and bottom halves must not collide in the cache"


def test_cache_key_separates_policy_and_clip():
    page_cache.reset()
    page_cache.put("/x.pdf", 1, 200, b"capped", policy=RENDER_POLICY_CAP)
    page_cache.put("/x.pdf", 1, 200, b"native", policy=RENDER_POLICY_NATIVE)
    page_cache.put("/x.pdf", 1, 200, b"clipped", policy=RENDER_POLICY_CAP,
                   clip=(0.0, 0.0, 1.0, 0.5))

    assert page_cache.get("/x.pdf", 1, 200, policy=RENDER_POLICY_CAP) == b"capped"
    assert page_cache.get("/x.pdf", 1, 200, policy=RENDER_POLICY_NATIVE) == b"native"
    assert page_cache.get(
        "/x.pdf", 1, 200, policy=RENDER_POLICY_CAP, clip=(0.0, 0.0, 1.0, 0.5)
    ) == b"clipped"


def test_cache_defaults_stay_backwards_compatible():
    """Existing three-arg callers keep working and land on the `cap` entry."""
    page_cache.reset()
    page_cache.put("/y.pdf", 2, 200, b"legacy")
    assert page_cache.get("/y.pdf", 2, 200) == b"legacy"
    assert page_cache.get("/y.pdf", 2, 200, policy=RENDER_POLICY_CAP) == b"legacy"


# --------------------------------------------------------------------------
# Shared renderer is untouched for the paths that did not opt in
# --------------------------------------------------------------------------

def test_cache_default_policy_matches_renderer():
    """page_cache duplicates the policy literal to stay import-light. If the
    two drift, a three-argument caller and a policy-passing caller stop
    sharing an entry and every page renders twice."""
    assert page_cache._DEFAULT_POLICY == RENDER_POLICY_CAP


@_needs_sample
def test_shared_renderer_default_is_unchanged():
    """scout / reviewer / formatter call this and must not shift behaviour."""
    pages = render_pages_to_png_bytes(str(_SAMPLE_PDF), start=31, end=31)
    assert fitz.Pixmap(pages[0]).width == 2200  # the historic 200 DPI render
