"""PDF extraction via pymupdf (``fitz``)."""

from __future__ import annotations

from app.services.extraction.base import (
    ExtractionResult,
    ExtractedUnit,
    LocationInfo,
    register,
    register_unavailable,
)

try:
    import pymupdf as fitz  # pymupdf >= 1.24 exposes ``pymupdf`` directly
    EXTRACTION_AVAILABLE = True
except ImportError:
    try:
        import fitz  # older pymupdf versions only expose ``fitz``

        EXTRACTION_AVAILABLE = True
    except ImportError:
        EXTRACTION_AVAILABLE = False

if not EXTRACTION_AVAILABLE:
    register_unavailable("pdf", "pymupdf")
else:

    @register("pdf")
    class PDFExtractor:
        """Extract text per page; flag image-only pages with a warning."""

        def extract(self, data: bytes, *, filename: str) -> ExtractionResult:
            warnings: list[str] = []
            try:
                document = fitz.open(stream=data, filetype="pdf")
            except Exception as exc:  # noqa: BLE001 - convert to result
                return ExtractionResult(error=f"Could not open PDF: {exc}")
            if document.page_count == 0:
                return ExtractionResult(error="PDF has no pages.")

            units: list[ExtractedUnit] = []
            for page_index in range(document.page_count):
                page = document.load_page(page_index)
                text = (page.get_text() or "").strip()
                if not text or len(text) < 3:
                    warnings.append(
                        f"page {page_index + 1} appears image-only; OCR is not available in v1"
                    )
                    continue
                units.append(
                    ExtractedUnit(
                        text=text,
                        location=LocationInfo(kind="page", page=page_index + 1),
                    )
                )

            if not units:
                return ExtractionResult(
                    error=(
                        "No text could be extracted from this PDF; it may be "
                        "image-only, and OCR is not available in v1."
                    ),
                    warnings=warnings,
                )
            return ExtractionResult(units=units, warnings=warnings)
