from __future__ import annotations

from io import BytesIO

import pytest


def test_text_extractor():
    from app.services.extraction.text import TextExtractor

    result = TextExtractor().extract(
        b"line one\nline two\nline three", filename="notes.txt"
    )
    assert result.ok
    assert len(result.units) == 1
    assert result.units[0].text == "line one\nline two\nline three"
    assert result.units[0].location.start_line == 1
    assert result.units[0].location.end_line == 3


def test_text_extractor_empty_fails():
    from app.services.extraction.text import TextExtractor

    result = TextExtractor().extract(b"   \n", filename="empty.txt")
    assert not result.ok
    assert "empty" in result.error.lower()


def test_markdown_sections():
    from app.services.extraction.markdown import MarkdownExtractor

    source = (
        "# Introduction\n\n"
        "First body line.\n"
        "Second body line.\n\n"
        "## Deep dive\n\n"
        "Deeper content.\n\n"
        "### Sub topic\n\n"
        "Sub content."
    )
    result = MarkdownExtractor().extract(source.encode("utf-8"), filename="notes.md")
    assert result.ok
    sections = [u.location.section for u in result.units]
    assert sections == ["Introduction", "Deep dive", "Sub topic"]
    assert result.units[0].location.start_line == 3
    assert result.units[2].text == "Sub content."


def test_markdown_no_headings_single_unit():
    from app.services.extraction.markdown import MarkdownExtractor

    result = MarkdownExtractor().extract(b"just some text\nmore text", filename="n.md")
    assert len(result.units) == 1
    assert result.units[0].location.section == "Document"


def test_pdf_two_pages():
    pymupdf = pytest.importorskip("pymupdf")
    from app.services.extraction.pdf import PDFExtractor

    document = pymupdf.open()
    page1 = document.new_page()
    page1.insert_text((72, 72), "Calculus and integrals on page one")
    page2 = document.new_page()
    page2.insert_text((72, 72), "Derivatives and limits on page two")
    data = document.tobytes()

    result = PDFExtractor().extract(data, filename="math.pdf")
    assert result.ok
    assert len(result.units) == 2
    assert [u.location.page for u in result.units] == [1, 2]
    assert "Calculus" in result.units[0].text
    assert "Derivatives" in result.units[1].text


def test_pdf_image_only_warns():
    pymupdf = pytest.importorskip("pymupdf")
    from app.services.extraction.pdf import PDFExtractor

    document = pymupdf.open()
    document.new_page()
    document.new_page()
    document.new_page()
    data = document.tobytes()

    result = PDFExtractor().extract(data, filename="scanned.pdf")
    assert not result.ok
    assert any("image-only" in w for w in result.warnings)
    assert "OCR" in (result.error or "")


def test_pdf_outline_from_toc():
    pymupdf = pytest.importorskip("pymupdf")
    from app.services.extraction.pdf import PDFExtractor

    document = pymupdf.open()
    page1 = document.new_page()
    page1.insert_text((72, 72), "Intro body")
    page2 = document.new_page()
    page2.insert_text((72, 72), "Derivatives body")
    document.set_toc(
        [
            [1, "Introduction", 1],
            [2, "Integrals", 1],
            [1, "Derivatives", 2],
        ]
    )
    data = document.tobytes()

    result = PDFExtractor().extract(data, filename="math.pdf")
    assert result.ok
    assert result.outline is not None
    titles = [entry["title"] for entry in result.outline]
    assert titles == ["Introduction", "Integrals", "Derivatives"]
    assert result.outline[0]["page"] == 1
    assert result.outline[2]["level"] == 1


def test_pdf_outline_empty_without_toc():
    pymupdf = pytest.importorskip("pymupdf")
    from app.services.extraction.pdf import PDFExtractor

    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), "uniform body line one")
    page.insert_text((72, 110), "uniform body line two")
    data = document.tobytes()

    result = PDFExtractor().extract(data, filename="plain.pdf")
    assert result.ok
    assert result.outline == []  # no toc and no size-delta spans to detect


def test_pdf_outline_size_fallback():
    pymupdf = pytest.importorskip("pymupdf")
    from app.services.extraction.pdf import PDFExtractor

    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), "Big Section Heading", fontsize=24)
    page.insert_text((72, 120), "body line one", fontsize=11)
    page.insert_text((72, 150), "body line two", fontsize=11)
    data = document.tobytes()

    result = PDFExtractor().extract(data, filename="fallback.pdf")
    assert result.ok
    assert result.outline and result.outline[0]["title"] == "Big Section Heading"


def test_docx_sections():
    docx = pytest.importorskip("docx")
    from app.services.extraction.docx import DocxExtractor

    document = docx.Document()
    document.add_heading("Introduction", level=1)
    document.add_paragraph("First paragraph under intro.")
    document.add_paragraph("Second paragraph under intro.")
    document.add_heading("Details", level=2)
    document.add_paragraph("Detail paragraph.")
    buffer = BytesIO()
    document.save(buffer)

    result = DocxExtractor().extract(buffer.getvalue(), filename="notes.docx")
    assert result.ok
    assert [u.location.section for u in result.units] == ["Introduction", "Details"]
    assert "First paragraph" in result.units[0].text
    assert "Detail paragraph" in result.units[1].text
    assert result.units[0].location.start_line == 2
    assert result.units[0].location.end_line == 3


def test_pptx_slides():
    pptx = pytest.importorskip("pptx")
    from app.services.extraction.pptx import PptxExtractor

    presentation = pptx.Presentation()
    layout = presentation.slide_layouts[1]
    slide1 = presentation.slides.add_slide(layout)
    slide1.shapes.title.text = "Overview"
    slide1.placeholders[1].text = "Body of the first slide."
    slide2 = presentation.slides.add_slide(layout)
    slide2.shapes.title.text = "Conclusion"
    slide2.placeholders[1].text = "Body of the second slide."
    buffer = BytesIO()
    presentation.save(buffer)

    result = PptxExtractor().extract(buffer.getvalue(), filename="slides.pptx")
    assert result.ok
    assert len(result.units) == 2
    assert [u.location.slide for u in result.units] == [1, 2]
    assert "Overview" in result.units[0].text


def test_dispatch_unknown_extension():
    from app.errors import UnsupportedFormatError
    from app.services.extraction.base import get_extractor

    with pytest.raises(UnsupportedFormatError):
        get_extractor("exe")


def test_registry_has_all_supported():
    from app.services.extraction.base import EXTRACTORS

    assert {"pdf", "docx", "pptx", "md", "markdown", "txt", "tex"} <= set(EXTRACTORS)
