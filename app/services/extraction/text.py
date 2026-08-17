"""Plain-text extraction: a single unit with line ranges."""

from __future__ import annotations

from app.services.extraction.base import (
    ExtractionResult,
    ExtractedUnit,
    LocationInfo,
    register,
)


@register("txt")
class TextExtractor:
    """Extract plain text as one unit with its full line range."""

    def extract(self, data: bytes, *, filename: str) -> ExtractionResult:
        text = data.decode("utf-8", errors="replace")
        stripped = text.strip()
        if not stripped:
            return ExtractionResult(error="The text file is empty.")
        line_count = len(text.splitlines())
        return ExtractionResult(
            units=[
                ExtractedUnit(
                    text=stripped,
                    location=LocationInfo(
                        kind="lines", start_line=1, end_line=line_count or 1
                    ),
                )
            ]
        )
