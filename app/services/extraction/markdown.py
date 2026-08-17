"""Markdown extraction: split on #/##/### headings into section units."""

from __future__ import annotations

import re

from app.services.extraction.base import (
    ExtractionResult,
    ExtractedUnit,
    LocationInfo,
    register,
)

_HEADING_RE = re.compile(r"^(#{1,3})\s+(.*)$")


@register("md")
@register("markdown")
class MarkdownExtractor:
    """Split markdown into section units at level-1/2/3 headings."""

    def extract(self, data: bytes, *, filename: str) -> ExtractionResult:
        text = data.decode("utf-8", errors="replace")
        lines = text.splitlines()

        units: list[ExtractedUnit] = []
        current_section: str | None = None
        body_lines: list[str] = []
        body_start: int | None = None
        body_end: int | None = None

        for line_no, line in enumerate(lines, start=1):
            match = _HEADING_RE.match(line)
            if match:
                self._flush(units, current_section, body_lines, body_start, body_end)
                current_section = match.group(2).strip()
                body_lines = []
                body_start = None
                body_end = None
                continue
            if not line.strip():
                continue
            if body_start is None:
                body_start = line_no
            body_end = line_no
            body_lines.append(line)

        self._flush(units, current_section, body_lines, body_start, body_end)

        if not units:
            return ExtractionResult(error="No text content found in this file.")
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
        units.append(
            ExtractedUnit(
                text="\n".join(body_lines),
                location=LocationInfo(
                    kind="section",
                    section=section or "Document",
                    start_line=body_start,
                    end_line=body_end,
                ),
            )
        )
