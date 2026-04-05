from tools.pdf_viewer import render_pages_to_images, count_pdf_pages


def test_count_pdf_pages():
    count = count_pdf_pages("data/FINCO-Audited-Financial-Statement-2021.pdf")
    assert count == 37


def test_render_single_page(tmp_path):
    images = render_pages_to_images(
        "data/FINCO-Audited-Financial-Statement-2021.pdf",
        start=1,
        end=1,
        output_dir=str(tmp_path),
    )
    assert len(images) == 1
    assert images[0].exists()
    assert images[0].stat().st_size > 0


def test_render_page_range(tmp_path):
    images = render_pages_to_images(
        "data/FINCO-Audited-Financial-Statement-2021.pdf",
        start=12,
        end=14,
        output_dir=str(tmp_path),
    )
    assert len(images) == 3
    for img in images:
        assert img.exists()
        assert img.stat().st_size > 0


def test_render_all_pages(tmp_path):
    images = render_pages_to_images(
        "data/FINCO-Audited-Financial-Statement-2021.pdf",
        output_dir=str(tmp_path),
    )
    assert len(images) == 37


def test_page_images_are_png(tmp_path):
    images = render_pages_to_images(
        "data/FINCO-Audited-Financial-Statement-2021.pdf",
        start=1,
        end=1,
        output_dir=str(tmp_path),
    )
    assert images[0].suffix == ".png"


def test_invalid_page_range_raises(tmp_path):
    import pytest

    with pytest.raises(ValueError):
        render_pages_to_images(
            "data/FINCO-Audited-Financial-Statement-2021.pdf",
            start=40,
            end=45,
            output_dir=str(tmp_path),
        )
