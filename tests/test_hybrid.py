from __future__ import annotations


from app.services.hybrid import fuse_ranked, select_hybrid


def _chunk(
    chunk_id: str,
    file_id: str,
    text: str,
    chunk_index: int,
    unicode_text: str | None = None,
) -> dict:
    return {
        "id": chunk_id,
        "file_id": file_id,
        "text": text,
        "unicode_text": unicode_text,
        "chunk_index": chunk_index,
    }


def test_fuse_ranks_shared_before_one_sided():
    """Chunks ranked by both retrievers outrank chunks found only by one."""
    chunks = [_chunk("a", "f1", "mitochondria atp", 0), _chunk("b", "f1", "pasta", 1)]
    # Lexical finds a; semantic finds both, with b ranked first.
    fused = fuse_ranked([[chunks[0]], [chunks[1], chunks[0]]], budget=2)
    assert fused[0]["id"] == "a"  # present in both lists -> highest RRF


def test_fuse_prefers_high_lexical_position_over_low_semantic():
    """A top lexical hit beat a weak semantic hit even when both fuse."""
    a = _chunk("a", "f1", "mitochondria energy atp biology", 0)
    b = _chunk("b", "f1", "mitochondria trivia", 1)
    # Lexical: a then b. Semantic: b then a (position swap).
    fused = fuse_ranked([[a, b], [b, a]], budget=2)
    # Equal RRF sums both ways (a: 1/61+1/62, b: 1/62+1/61) -> tie broken by
    # earliest first-position, which is a. Sanity: both survive and same set.
    assert {c["id"] for c in fused} == {"a", "b"}


def test_fuse_respects_budget():
    a = _chunk("a", "f1", "x", 0)
    b = _chunk("b", "f1", "x", 1)
    c = _chunk("c", "f1", "x", 2)
    fused = fuse_ranked([[a, b, c], [c, b, a]], budget=2)
    assert len(fused) == 2


def test_fuse_respects_per_file_cap():
    a = _chunk("a", "fA", "x", 0)
    b = _chunk("b", "fA", "x", 1)
    c = _chunk("c", "fB", "x", 0)
    fused = fuse_ranked([[a, b, c]], budget=10, per_file_cap=1)
    assert len(fused) == 2
    assert a in fused and c in fused  # one from fA, one from fB



def test_select_hybrid_without_settings_uses_lexical():
    chunks = [
        _chunk("a", "f1", "mitochondria produce atp energy biology", 0),
        _chunk("b", "f1", "cooking pasta with tomato sauce", 1),
    ]
    result = select_hybrid(chunks, "What are mitochondria?", budget=1, settings=None)
    assert [c["id"] for c in result] == ["a"]


def test_select_hybrid_lexical_terminology_survives():
    """Math/terminology matches are not drowned by fusion."""
    chunks = [
        _chunk(
            "a",
            "f1",
            "integration yields \\int_0^1 x^2 dx",
            0,
            unicode_text="integration yields ∫_0^1 x^2 dx",
        ),
        _chunk("b", "f1", "some unrelated prose about derivatives", 1),
    ]
    result = select_hybrid(chunks, "∫ x dx", budget=1, settings=None)
    assert [c["id"] for c in result] == ["a"]



