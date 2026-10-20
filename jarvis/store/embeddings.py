"""Vector embeddings for long-term memory (optional).

Enabled with `JARVIS_EMBEDDINGS=1`. When the model or key is missing, memory
falls back to keyword scoring, so nothing breaks.

Vectors are stored as little-endian float32 blobs in the `facts.embedding`
column — good enough for a few thousand memories, and portable to pgvector
later without changing the memory API.
"""

from __future__ import annotations

import math
import struct
from typing import Any, Iterable, Sequence


def pack(vector: Sequence[float]) -> bytes:
    return struct.pack(f"<{len(vector)}f", *vector)


def unpack(blob: bytes | None) -> list[float]:
    if not blob:
        return []
    count = len(blob) // 4
    return list(struct.unpack(f"<{count}f", blob))


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if not norm_a or not norm_b:
        return 0.0
    return dot / (norm_a * norm_b)


class Embedder:
    """OpenAI-compatible embeddings with a deterministic hash fallback."""

    def __init__(self, settings: Any) -> None:
        self.settings = settings
        self.enabled = bool(getattr(settings, "llm_available", False)) and _flag("JARVIS_EMBEDDINGS")
        self.model = _env("JARVIS_EMBEDDING_MODEL", "text-embedding-3-small")
        self.dimensions = int(_env("JARVIS_EMBEDDING_DIMENSIONS", "256"))
        self.calls = 0

    def describe(self) -> dict[str, Any]:
        return {"enabled": self.enabled, "model": self.model if self.enabled else "hash-fallback",
                "dimensions": self.dimensions, "calls": self.calls}

    async def embed(self, texts: Iterable[str]) -> list[list[float]]:
        items = [t for t in texts if t]
        if not items:
            return []
        if self.enabled:
            try:
                return await self._remote(items)
            except Exception:  # noqa: BLE001
                pass
        return [self._hash_vector(text) for text in items]

    async def _remote(self, texts: list[str]) -> list[list[float]]:
        import httpx

        url = f"{str(self.settings.llm_base_url).rstrip('/')}/embeddings"
        payload = {"model": self.model, "input": texts, "dimensions": self.dimensions}
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(url, headers={
                "Authorization": f"Bearer {self.settings.llm_api_key}",
                "Content-Type": "application/json",
            }, json=payload)
            response.raise_for_status()
            data = response.json()
        self.calls += 1
        return [item["embedding"] for item in sorted(data["data"], key=lambda d: d["index"])]

    def _hash_vector(self, text: str) -> list[float]:
        """Deterministic bag-of-character-trigrams vector (no network needed)."""
        vector = [0.0] * self.dimensions
        normalised = " ".join(text.lower().split())
        tokens = normalised.split()
        for index, token in enumerate(tokens):
            for gram in (token, *[token[i:i + 3] for i in range(max(1, len(token) - 2))]):
                bucket = (hash(gram) % self.dimensions + self.dimensions) % self.dimensions
                vector[bucket] += 1.0 + (0.5 if index < 3 else 0.0)
        norm = math.sqrt(sum(v * v for v in vector)) or 1.0
        return [v / norm for v in vector]


def _flag(name: str) -> bool:
    import os

    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _env(name: str, default: str) -> str:
    import os

    return os.environ.get(name, default)


def rank_by_similarity(query_vector: Sequence[float], candidates: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    scored = []
    for candidate in candidates:
        vector = candidate.get("vector") or []
        scored.append({**candidate, "score": cosine(query_vector, vector)})
    return sorted(scored, key=lambda item: item["score"], reverse=True)


__all__ = ["Embedder", "pack", "unpack", "cosine", "rank_by_similarity"]
