from __future__ import annotations

from app.services.extraction.base import (
    ExtractionResult,
    ExtractedUnit,
    LocationInfo,
    register,
    register_unavailable,
)

try:
    import pymupdf as fitz
    EXTRACTION_AVAILABLE = True
except ImportError:
    try:
        import fitz

        EXTRACTION_AVAILABLE = True
    except ImportError:
        EXTRACTION_AVAILABLE = False

if not EXTRACTION_AVAILABLE:
    register_unavailable("pdf", "pymupdf")
else:

    def _anchor_filtered(toc: list) -> list[dict]:
        # normalize pymupdf toc entries to compact outline dicts
        out = []
        for entry in toc:
            if len(entry) < 3:
                continue
            level, title, page = entry[0], entry[1], entry[2]
            if isinstance(title, str) and title.strip():
                out.append({"level": level, "title": title.strip(), "page": page})
        return out

    def _heading_outline(document) -> list[dict]:
        # fallback when the pdf has no outline bookmarks: size-based heading spans
        body_sizes: list[float] = []
        spans: list[tuple[float, int, str]] = []
        for page_index in range(min(document.page_count, 200)):
            page = document.load_page(page_index)
            try:
                raw = page.get_text("dict")
            except Exception:  # noqa: BLE001 - skip a render failure; heuristic
                continue
            for block in raw.get("blocks", []):
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        size = float(span.get("size") or 0)
                        text = (span.get("text") or "").strip()
                        if not text:
                            continue
                        body_sizes.append(size)
                        spans.append((size, page_index + 1, text))
        if not body_sizes or not spans:
            return []
        body_sizes.sort()
        baseline = body_sizes[len(body_sizes) // 2]
        threshold = baseline * 1.2
        headings: list[dict] = []
        seen: set[str] = set()
        for size, page, text in spans:
            if size < threshold:
                continue
            if len(text) > 120 or len(text) < 2:
                continue
            key = text.lower()
            if key in seen:
                continue
            seen.add(key)
            headings.append({"level": 1, "title": text, "page": page})
            if len(headings) >= 200:
                break
        return headings

    @register("pdf")
    class PDFExtractor:

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

            toc = []
            try:
                toc = document.get_toc() or []
            except Exception:  # noqa: BLE001 - toc read may fail; fall back
                toc = []
            outline = _anchor_filtered(toc) if toc else _heading_outline(document)

            if not units:
                return ExtractionResult(
                    error=(
                        "No text could be extracted from this PDF; it may be "
                        "image-only, and OCR is not available in v1."
                    ),
                    warnings=warnings,
                )
            return ExtractionResult(units=units, warnings=warnings, outline=outline)