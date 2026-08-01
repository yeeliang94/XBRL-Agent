"""Zoom-region tool — PLAN-notes-source-integrity-build Phase 1, Step 1.3.

Why a named-region vocabulary rather than float coordinates: the model picks
from a closed list it cannot get subtly wrong. A hallucinated `(0.13, 0.9,
0.11, 0.2)` would either raise or silently render the wrong strip; a
hallucinated region name is caught and reported back with the valid list.

What is NOT asserted here: that zooming makes the model read a table better.
That is the Phase 0.1 experiment, and Phase 0 has to stay able to disprove it.
"""
import fitz
import pytest
from pathlib import Path

from notes.agent import ZOOM_REGIONS, resolve_zoom_region
from tools.pdf_viewer import RENDER_POLICY_NATIVE, _validate_clip, render_page_png

_SAMPLE_PDF = Path("data/FINCO-Audited-Financial-Statement-2021.pdf")
_needs_sample = pytest.mark.skipif(
    not _SAMPLE_PDF.exists(),
    reason="Sample PDF not in repo — run with local data only",
)


def test_every_region_is_a_valid_clip():
    assert ZOOM_REGIONS, "the vocabulary must not be empty"
    for name, clip in ZOOM_REGIONS.items():
        # Raises if out of range or inverted.
        assert _validate_clip(clip) == clip, name


def test_regions_cover_the_page_between_them():
    """Halves and thirds must reach both edges, or content at the very top or
    bottom of a page is unreachable by zooming."""
    tops = min(c[1] for c in ZOOM_REGIONS.values())
    bottoms = max(c[3] for c in ZOOM_REGIONS.values())
    lefts = min(c[0] for c in ZOOM_REGIONS.values())
    rights = max(c[2] for c in ZOOM_REGIONS.values())
    assert (tops, lefts) == (0.0, 0.0)
    assert (bottoms, rights) == (1.0, 1.0)


def test_thirds_overlap_so_a_straddling_table_is_never_cut():
    top = ZOOM_REGIONS["top-third"]
    middle = ZOOM_REGIONS["middle-third"]
    bottom = ZOOM_REGIONS["bottom-third"]
    assert middle[1] < top[3], "middle third must start before the top third ends"
    assert bottom[1] < middle[3], "bottom third must start before the middle ends"


def test_resolve_accepts_the_documented_names():
    for name in ZOOM_REGIONS:
        assert resolve_zoom_region(name) == ZOOM_REGIONS[name]


def test_resolve_is_forgiving_about_case_and_spacing():
    assert resolve_zoom_region("Top Half") == resolve_zoom_region("top-half")
    assert resolve_zoom_region("  BOTTOM_THIRD ") == resolve_zoom_region("bottom-third")


def test_resolve_rejects_an_unknown_region():
    with pytest.raises(ValueError) as exc:
        resolve_zoom_region("somewhere in the middle-ish")
    # The message must list the valid names — that is what lets the model
    # recover on its next turn instead of guessing again.
    assert "top-third" in str(exc.value)


def test_full_page_region_is_available():
    """The model needs a way back out to the whole page."""
    assert resolve_zoom_region("full") is None


@_needs_sample
def test_zoom_renders_the_declared_fraction_of_the_page():
    """A quadrant is ~52% per axis, not 50% — the regions overlap on purpose
    (see test_thirds_overlap_...). Assert against the declared clip so the
    overlap can be tuned without this test lying about what it checks."""
    clip = resolve_zoom_region("top-left")
    full = render_page_png(str(_SAMPLE_PDF), 31, policy=RENDER_POLICY_NATIVE)
    quadrant = render_page_png(
        str(_SAMPLE_PDF), 31, policy=RENDER_POLICY_NATIVE, clip=clip,
    )
    f, q = fitz.Pixmap(full), fitz.Pixmap(quadrant)
    assert q.width == pytest.approx(f.width * (clip[2] - clip[0]), abs=2)
    assert q.height == pytest.approx(f.height * (clip[3] - clip[1]), abs=2)
    assert q.width < f.width and q.height < f.height


@_needs_sample
def test_distinct_regions_render_distinct_bytes():
    top = render_page_png(str(_SAMPLE_PDF), 31, clip=resolve_zoom_region("top-half"))
    bottom = render_page_png(str(_SAMPLE_PDF), 31,
                             clip=resolve_zoom_region("bottom-half"))
    assert top != bottom
