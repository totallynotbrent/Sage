"""Simple deterministic retrieval: token overlap scoring (tf*idf + position).

V1 does not use vectors; ranking is by weighted keyword overlap. Idf is derived
from the candidate corpus (document/chunk frequency), and early chunks get a
small position bonus. A per-file cap prevents one file from monopolizing the
budget.
"""

from __future__ import annotations

import math
import re
from typing import Any

#: Small stopword list for query tokenization.
STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "of", "to", "in", "on", "for",
    "with", "is", "are", "was", "were", "be", "been", "being", "this", "that",
    "these", "those", "it", "its", "what", "which", "who", "how", "why",
    "when", "as", "at", "by", "from", "than", "then", "so", "if", "about",
    "not", "do", "does", "did", "can", "could", "will", "would", "should",
    "have", "has", "had", "there", "their", "your", "you", "please", "me",
}

_TOKEN_RE = re.compile(r"[a-z0-9']+")


def tokenize(text: str) -> list[str]:
    """Lowercase, strip punctuation, drop stopwords."""
    return [
        token
        for token in _TOKEN_RE.findall(text.lower())
        if token not in STOPWORDS
    ]


class Retriever:
    """Selects the most relevant chunks for a query from a candidate set."""

    def select(
        self,
        chunks: list[dict[str, Any]],
        query: str,
        *,
        budget: int,
        per_file_cap: int = 3,
    ) -> list[dict[str, Any]]:
        """Return up to ``budget`` chunks ranked by relevance.

        ``chunks`` is a list of dicts with ``id``, ``file_id``, ``text``, and
        ``chunk_index``. An empty query returns the earliest chunks (position
        ordering). Results preserve the input dict references.
        """
        if budget <= 0 or not chunks:
            return []
        per_file_cap = max(1, per_file_cap)

        query_tokens = tokenize(query)
        tokens_per_chunk = [tokenize(chunk.get("text") or "") for chunk in chunks]

        # Inverse document frequency over the corpus.
        total = len(chunks)
        document_frequency: dict[str, int] = {}
        for tokens in tokens_per_chunk:
            for token in set(tokens):
                document_frequency[token] = document_frequency.get(token, 0) + 1

        def idf(token: str) -> float:
            df = document_frequency.get(token, 0)
            return math.log((1 + total) / (1 + df)) + 1.0

        if query_tokens:
            # Term frequency per chunk + idf weighting.
            term_frequency: list[dict[str, int]] = []
            for tokens in tokens_per_chunk:
                counts: dict[str, int] = {}
                for token in tokens:
                    counts[token] = counts.get(token, 0) + 1
                term_frequency.append(counts)

            scored: list[tuple[float, int, dict]] = []
            max_index = max(1, total - 1)
            for index, chunk in enumerate(chunks):
                counts = term_frequency[index]
                score = sum(
                    counts.get(token, 0) * idf(token) for token in query_tokens
                )
                if score > 0:
                    # Small position bonus for early chunks (breaks near-ties).
                    score += 0.01 * (1.0 - index / max_index)
                    scored.append((score, index, chunk))
        else:
            # Empty query: rely on position alone.
            scored = [(0.0, index, chunk) for index, chunk in enumerate(chunks)]

        # Position bonus for early chunks, then sort by (score desc, index asc).
        scored.sort(key=lambda item: (-item[0], item[1]))
        selected: list[dict[str, Any]] = []
        per_file: dict[str, int] = {}
        for score, index, chunk in scored:
            file_id = chunk.get("file_id") or ""
            if per_file.get(file_id, 0) >= per_file_cap:
                continue
            if len(selected) >= budget:
                break
            per_file[file_id] = per_file.get(file_id, 0) + 1
            selected.append(chunk)
        return selected
