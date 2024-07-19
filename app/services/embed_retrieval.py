"""Local embedding fallback for retrieval.

When keyword scoring selects nothing (paraphrased queries, vocabulary
mismatch), chunks and query are embedded via a local Ollama embedding
model and matched by cosine similarity. Purely additive: keyword hits
always win; embeddings only rescue misses.
"""

from __future__ import annotations

import math
from typing import Any

import httpx

from app.config import Settings

_DEFAULT_EMBED_MODEL = "all-minilm"
_TIMEOUT_SECONDS = 20.0


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


class EmbedFallback:
    """Cosine-similarity rescue pass backed by a local embedding model."""

    def __init__(self, settings: Settings) -> None:
        self.base_url = (settings.searxng_url or "").strip()
        # Reuse the Ollama host the rest of the stack already assumes:
        # default local inference endpoint.
        self.embed_url = "http://localhost:11434/api/embed"
        self.model = _DEFAULT_EMBED_MODEL
        self._available: bool | None = None

    def _embed_sync(self, inputs: list[str]) -> list[list[float]] | None:
        try:
            response = httpx.post(
                self.embed_url,
                json={"model": self.model, "input": inputs},
                timeout=_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            data = response.json()
            embeddings = data.get("embeddings") or []
            if len(embeddings) != len(inputs):
                return None
            return [list(map(float, e)) for e in embeddings]
        except Exception:
            return None

    def sync_select(
        self,
        chunks: list[dict[str, Any]],
        query: str,
        *,
        budget: int,
        per_file_cap: int = 3,
        min_similarity: float = 0.35,
    ) -> list[dict[str, Any]]:
        """Embedding-based selection; mirrors Retriever.select's contract."""
        if budget <= 0 or not chunks:
            return []
        texts = [
            " ".join(
                part
                for part in (
                    c.get("section") or "",
                    c.get("text") or "",
                    c.get("unicode_text") or "",
                )
                if part
            )
            for c in chunks
        ]
        embeddings = self._embed_sync([query, *texts])
        if embeddings is None:
            self._available = False
            return []
        self._available = True
        query_vec = embeddings[0]
        scored: list[tuple[float, int, dict[str, Any]]] = []
        per_file: dict[str, int] = {}
        for index, chunk in enumerate(chunks):
            file_id = chunk.get("file_id") or ""
            if per_file.get(file_id, 0) >= per_file_cap:
                continue
            similarity = _cosine(query_vec, embeddings[index + 1])
            if similarity < min_similarity:
                continue
            scored.append((similarity, index, chunk))
            per_file[file_id] = per_file.get(file_id, 0) + 1
        scored.sort(key=lambda item: -item[0])
        selected: list[dict[str, Any]] = []
        for similarity, _, chunk in scored[:budget]:
            chunk.setdefault("_embed_similarity", round(similarity, 4))
            selected.append(chunk)
        return selected
