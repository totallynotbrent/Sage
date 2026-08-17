"""Deterministic chunking of extracted units.

Chunks never cross unit boundaries (a chunk belongs to exactly one page,
slide, section, or line range). Windows within a unit have an overlap that
snaps to the nearest paragraph/whitespace break within a ±20% tolerance.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.services.extraction.base import LocationInfo


@dataclass
class Chunk:
    """One chunk of extracted text with full source metadata."""

    id: str
    file_id: str
    chunk_index: int
    text: str
    location: LocationInfo | None
    char_start: int
    char_end: int


def chunk_units(
    units: list,
    *,
    file_id: str,
    chunk_chars: int = 1500,
    overlap: int = 200,
) -> list[Chunk]:
    """Split every extracted unit into overlapping windows.

    Deterministic: identical inputs always produce identical chunks. Each chunk
    id is ``f"{file_id}:{unit_index}:{chunk_index}"``.
    """
    if chunk_chars < 1:
        raise ValueError("chunk_chars must be >= 1")
    if overlap < 0:
        raise ValueError("overlap must be >= 0")
    overlap = min(overlap, max(0, chunk_chars - 1))

    chunks: list[Chunk] = []
    for unit_index, unit in enumerate(units):
        text = unit.text or ""
        windows = _split_text(text, chunk_chars, overlap)
        for window_index, (start, end) in enumerate(windows):
            chunk_id = f"{file_id}:{unit_index}:{window_index}"
            chunks.append(
                Chunk(
                    id=chunk_id,
                    file_id=file_id,
                    chunk_index=len(chunks),
                    text=text[start:end],
                    location=unit.location,
                    char_start=start,
                    char_end=end,
                )
            )
    return chunks


def _split_text(text: str, size: int, overlap: int) -> list[tuple[int, int]]:
    """Return ``(start, end)`` character windows for one unit's text."""
    length = len(text)
    if length == 0:
        return []
    if length <= size:
        return [(0, length)]

    step = size - overlap
    if step < 1:
        step = 1

    windows: list[tuple[int, int]] = []
    start = 0
    while start < length:
        end = min(start + size, length)
        if end < length:
            end = _snap_end(text, start, end, size)
        if end <= start:
            end = min(start + step, length)
        windows.append((start, end))
        if end >= length:
            break
        next_start = end - overlap
        if next_start <= start:
            next_start = start + step
        start = next_start
    return windows


def _snap_end(text: str, start: int, end: int, size: int) -> int:
    """Move ``end`` to the nearest paragraph/newline break within ±20% of size."""
    tolerance = max(1, int(size * 0.2))
    lo = max(start + 1, end - tolerance)
    hi = min(len(text), end + tolerance)

    # Prefer paragraph breaks ("\n\n"), then single newlines.
    for marker in ("\n\n", "\n"):
        best = -1
        best_distance = float("inf")
        position = text.find(marker, lo)
        while position != -1 and position <= hi:
            distance = abs(position - end)
            if distance < best_distance:
                best_distance = distance
                best = position
            position = text.find(marker, position + 1)
        if best != -1:
            return best
    return end
