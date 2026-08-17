"""DOCX extraction via python-docx: paragraphs grouped into sections.

Heading 1/2 paragraphs start a new section unit; every paragraph contributes a
line to the document line numbering used for line-range metadata.
"""

from __future__ import annotations

from io import BytesIO

from app.services.extraction.base import (
    ExtractionResult,
    ExtractedUnit,
    LocationInfo,
    register,
    register_unavailable,
)

try:
    from docx import Document

    EXTRACTION_AVAILABLE = True
except ImportError:
    EXTRACTION_AVAILABLE = False

if not EXTRACTION_AVAILABLE:
    register_unavailable("docx", "python-docx")
else:
    _SECTION_STYLES = {"heading 1", "heading 2"}

    @register("docx")
    class DocxExtractor:
        """Group non-empty paragraphs into section units under headings."""

        def extract(self, data: bytes, *, filename: str) -> ExtractionResult:
            try:
                document = Document(BytesIO(data))
            except Exception as exc:  # noqa: BLE001 - convert to result
                return ExtractionResult(error=f"Could not open DOCX: {exc}")

            units: list[ExtractedUnit] = []
            current_section: str | None = None
            body_lines: list[str] = []
            body_start: int | None = None
            body_end: int | None = None
            line_no = 0

            for paragraph in document.paragraphs:
                line_no += 1
                style_name = ""
                if paragraph.style is not None:
                    style_name = (paragraph.style.name or "").strip().lower()
                text = (paragraph.text or "").strip()

                if style_name in _SECTION_STYLES:
                    self._flush(
                        units, current_section, body_lines, body_start, body_end
                    )
                    current_section = text or "Untitled section"
                    body_lines = []
                    body_start = None
                    body_end = None
                    continue

                if not text:
                    continue
                if body_start is None:
                    body_start = line_no
                body_end = line_no
                body_lines.append(text)

            self._flush(units, current_section, body_lines, body_start, body_end)

            if not units:
                return ExtractionResult(error="No extractable text found in this DOCX.")
            return ExtractionResult(units=units)

        @staticmethod
        def _flush(
            units: list[ExtractedUnit],
            section: str | None,
            body_lines: list[str],
            body_start: int | None,
            body_end: int | None,
        ) -> None:
            if not body_lines:
                return
            text = "\n".join(body_lines)
            units.append(
                ExtractedUnit(
                    text=text,
                    location=LocationInfo(
                        kind="section",
                        section=section or "Document",
                        start_line=body_start,
                        end_line=body_end,
                    ),
                )
            )
