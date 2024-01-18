from __future__ import annotations

from dataclasses import dataclass

from app.services.extraction.base import LocationInfo


@dataclass
class Chunk:

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
    tolerance = max(1, int(size * 0.2))
    lo = max(start + 1, end - tolerance)
    hi = min(len(text), end + tolerance)

    for marker in ("\n\n", "\n"):
        best = -1
        best_distance = float("inf")
        bound = hi + len(marker)
        position = text.find(marker, lo, bound)
        while position != -1:
            distance = abs(position - end)
            if distance < best_distance:
                best_distance = distance
                best = position
            position = text.find(marker, position + 1, bound)
        if best != -1:
            return best
    return end
