from __future__ import annotations


from app.services.chunking import chunk_units
from app.services.extraction.base import ExtractedUnit, LocationInfo


def test_deterministic():
    units = [
        ExtractedUnit("one " * 40, LocationInfo(kind="page", page=1)),
        ExtractedUnit("two " * 40, LocationInfo(kind="page", page=2)),
    ]
    first = chunk_units(units, file_id="f1", chunk_chars=50, overlap=10)
    second = chunk_units(units, file_id="f1", chunk_chars=50, overlap=10)
    assert [(c.id, c.text, c.char_start, c.char_end) for c in first] == [
        (c.id, c.text, c.char_start, c.char_end) for c in second
    ]



def test_never_cross_unit_boundaries():
    units = [
        ExtractedUnit("AAAA " * 30),
        ExtractedUnit("BBBB " * 30),
    ]
    chunks = chunk_units(units, file_id="f", chunk_chars=50, overlap=10)
    assert len(chunks) >= 2
    for chunk in chunks:
        content = chunk.text
        assert ("AAAA" in content and "BBBB" not in content) or (
            "BBBB" in content and "AAAA" not in content
        )


def test_exact_overlap_without_breaks():
    text = "x" * 300
    chunks = chunk_units([ExtractedUnit(text)], file_id="f", chunk_chars=100, overlap=20)
    assert chunks[0].char_start == 0
    assert chunks[0].char_end == 100
    assert chunks[1].char_start == 80
    assert chunks[2].char_start == 160
    assert chunks[0].char_end - chunks[1].char_start == 20
    assert chunks[1].char_end - chunks[2].char_start == 20
    assert chunks[-1].char_end == 300


def test_snaps_to_paragraph_break():
    text = "y" * 90 + "\n\n" + "z" * 200
    chunks = chunk_units([ExtractedUnit(text)], file_id="f", chunk_chars=100, overlap=20)
    assert any(c.char_end == 90 for c in chunks), [c.char_end for c in chunks]


def test_short_text_single_chunk():
    chunks = chunk_units([ExtractedUnit("short")], file_id="f", chunk_chars=100, overlap=20)
    assert len(chunks) == 1
    assert chunks[0].text == "short"
    assert chunks[0].char_start == 0
    assert chunks[0].char_end == 5


def test_empty_text_no_chunks():
    chunks = chunk_units([ExtractedUnit("")], file_id="f", chunk_chars=100, overlap=20)
    assert chunks == []


def test_location_preserved():
    unit = ExtractedUnit("hello world " * 40, LocationInfo(kind="slide", slide=3))
    chunks = chunk_units([unit], file_id="f", chunk_chars=50, overlap=10)
    assert chunks[0].location.slide == 3
    assert chunks[0].location.kind == "slide"


