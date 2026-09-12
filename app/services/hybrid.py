"""Hybrid retrieval: reciprocal rank fusion of lexical + semantic selectors.

The app keeps two retrievers that rank the same chunks differently:

- ``Retriever`` (lexical, TF-IDF over words *and* math tokens), great for
  exact terminology, variable/math-symbol matches, and section headings.
- ``EmbedFallback`` (cosine similarity over a local embedding model), great
  for paraphrases and vocabulary mismatch the lexical pass would miss.

Instead of treating embeddings as a last-resort rescue (keyword wins, semantic
only on total miss), we run both and merge the two ranked lists with Reciprocal
Rank Fusion. Fusing with RRF keeps lexical specificity *and* semantic recall, so
we don't lose the math-token/terminology hits just because we also ranked
semantically, and vice versa.
"""

from __future__ import annotations

from typing import Any

from app.config import Settings
from app.services.embed_retrieval import EmbedFallback
from app.services.retrieval import Retriever

# Standard RRF constant (position-0 dominance tames ties). Matches common usage.
RRF_K = 60


def _key_of(chunk: dict[str, Any]) -> Any:
    """Stable identity for a chunk for cross-list alignment."""
    chunk_id = chunk.get("id")
    if chunk_id is not None:
        return ("id", chunk_id)
    return ("idx", chunk.get("file_id"), chunk.get("chunk_index"))


def fuse_ranked(
    ranked_lists: list[list[dict[str, Any]]],
    budget: int,
    per_file_cap: int = 3,
    k: int = RRF_K,
) -> list[dict[str, Any]]:
    """Merge several chunk rankings (best-first) into one via reciprocal rank fusion.

    RRF score for a chunk = sum over each list that contains it of 1 / (k + rank),
    where rank is 1-based position in that list. The final merged list is ordered
    by descending RRF score (ties broken toward the earliest position), then the
    usual ``budget`` and ``per_file_cap`` constraints are applied.
    """
    if budget <= 0 or not ranked_lists:
        return []
    per_file_cap = max(1, per_file_cap)

    scores: dict[Any, float] = {}
    first: dict[Any, tuple[int, int]] = {}
    chunk_map: dict[Any, dict[str, Any]] = {}
    for list_index, lst in enumerate(ranked_lists):
        for rank, chunk in enumerate(lst, start=1):
            key = _key_of(chunk)
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank)
            if key not in first:
                first[key] = (list_index, rank)
            chunk_map[key] = chunk

    ordered = sorted(
        scores.items(),
        key=lambda kv: (-kv[1], first[kv[0]][1], first[kv[0]][0]),
    )

    selected: list[dict[str, Any]] = []
    per_file: dict[str, int] = {}
    for key, _score in ordered:
        chunk = chunk_map[key]
        file_id = chunk.get("file_id") or ""
        if per_file.get(file_id, 0) >= per_file_cap:
            continue
        if len(selected) >= budget:
            break
        per_file[file_id] = per_file.get(file_id, 0) + 1
        selected.append(chunk)
    return selected


def select_hybrid(
    chunks: list[dict[str, Any]],
    query: str,
    *,
    budget: int,
    per_file_cap: int = 3,
    settings: Settings | None = None,
) -> list[dict[str, Any]]:
    """Retrieve by fusing lexical and (when available) semantic rankings.

    Falls back gracefully: if embeddings are unavailable or return nothing, the
    lexical ranking stands alone; if lexical finds nothing, embeddings rescue it.
    """
    if budget <= 0 or not chunks:
        return []
    # Let each retriever rank the full corpus (no early cap) so RRF positions
    # reflect true ordering; the cap is applied after fusion.
    big = len(chunks)

    lexical = Retriever().select(chunks, query, budget=big, per_file_cap=big)

    semantic: list[dict[str, Any]] = []
    if settings is not None:
        try:
            semantic = EmbedFallback(settings).sync_select(
                chunks, query, budget=big, per_file_cap=big
            )
        except Exception:
            semantic = []

    ranked = [lst for lst in (lexical, semantic) if lst]
    if not ranked:
        return []
    return fuse_ranked(ranked, budget, per_file_cap)