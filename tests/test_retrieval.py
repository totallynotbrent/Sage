"""Retrieval: ranking, budget, per-file cap, empty-query ordering."""

from __future__ import annotations

from app.services.retrieval import Retriever, tokenize


def _chunk(chunk_id: str, file_id: str, text: str, chunk_index: int) -> dict:
    return {"id": chunk_id, "file_id": file_id, "text": text, "chunk_index": chunk_index}


def test_tokenize_basic():
    assert tokenize("What are Mitochondria?") == ["mitochondria"]
    assert tokenize("the AND or but") == []


def test_ranking_prefers_relevant_chunk():
    chunks = [
        _chunk("a", "f1", "mitochondria produce atp energy biology", 0),
        _chunk("b", "f1", "recipes for cooking pasta with tomato sauce and mitochondria trivia", 1),
    ]
    result = Retriever().select(chunks, "What are mitochondria?", budget=2)
    assert result[0]["id"] == "a"
    assert result[1]["id"] == "b"


def test_budget_is_respected():
    chunks = [
        _chunk(str(i), f"f{i % 2}", f"mitochondria topic number {i}", i)
        for i in range(10)
    ]
    result = Retriever().select(chunks, "mitochondria", budget=3, per_file_cap=10)
    assert len(result) == 3


def test_per_file_cap():
    chunks = [
        _chunk("a1", "fileA", "mitochondria energy cell biology", 0),
        _chunk("a2", "fileA", "mitochondria dna genome", 1),
        _chunk("a3", "fileA", "mitochondria membrane", 2),
        _chunk("a4", "fileA", "mitochondria atp", 3),
        _chunk("b1", "fileB", "mitochondria dynamics", 0),
        _chunk("b2", "fileB", "mitochondria fission", 1),
    ]
    result = Retriever().select(chunks, "mitochondria", budget=10, per_file_cap=2)
    from collections import Counter

    counts = Counter(c["file_id"] for c in result)
    assert counts["fileA"] == 2
    assert counts["fileB"] == 2


def test_empty_query_returns_earliest():
    chunks = [
        _chunk("c0", "f", "first chunk about nothing", 0),
        _chunk("c1", "f", "second chunk", 1),
        _chunk("c2", "f", "third chunk", 2),
    ]
    result = Retriever().select(chunks, "", budget=2)
    assert [c["id"] for c in result] == ["c0", "c1"]


def test_no_match_returns_nothing():
    chunks = [
        _chunk("c0", "f", "cooking recipes", 0),
        _chunk("c1", "f", "pasta sauce", 1),
    ]
    result = Retriever().select(chunks, "quantum physics", budget=5)
    assert result == []


def test_budget_zero_or_empty():
    chunks = [_chunk("c0", "f", "mitochondria", 0)]
    assert Retriever().select(chunks, "mitochondria", budget=0) == []
    assert Retriever().select([], "mitochondria", budget=5) == []
