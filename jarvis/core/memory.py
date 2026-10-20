"""JARVIS memory: short-term (conversation), long-term (facts) and task memory.

    short-term  -> the last N messages of the current session
    long-term   -> stable facts about the user (profile, projects, preferences)
    task memory -> what a multi-step plan has done so far

Long-term retrieval is hybrid: embeddings when available, keyword scoring
otherwise. Both paths return the same shape so callers never branch.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable

from ..store.embeddings import Embedder, cosine, pack, unpack

CATEGORY_ALIASES = {
    "profile": ("profile", "me", "personal", "identity"),
    "preference": ("preference", "preferences", "like", "likes", "fav", "favourite", "favorite"),
    "project": ("project", "projects", "repo", "repos", "codebase"),
    "goal": ("goal", "goals", "target", "targets", "ambition"),
    "workflow": ("workflow", "routine", "process", "steps"),
    "general": ("general", "note", "notes", "fact", "facts"),
}

STOPWORDS = {
    "the", "a", "an", "and", "or", "is", "are", "was", "were", "be", "to", "of", "in", "on",
    "for", "with", "my", "your", "me", "i", "it", "that", "this", "do", "does", "you", "jarvis",
    "please", "what", "about", "know", "remember",
}


def normalise_category(category: str) -> str:
    lowered = (category or "general").strip().lower()
    for canonical, aliases in CATEGORY_ALIASES.items():
        if lowered in aliases:
            return canonical
    return lowered if lowered in CATEGORY_ALIASES else (lowered or "general")


def keywords(text: str) -> set[str]:
    tokens = re.findall(r"[a-z0-9][a-z0-9'\-]*", (text or "").lower())
    return {t for t in tokens if t not in STOPWORDS and len(t) > 1}


@dataclass
class RecallHit:
    key: str
    value: str
    category: str
    score: float
    source: str = "user"
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key, "value": self.value, "category": self.category,
            "score": round(self.score, 3), "source": self.source, "updated_at": self.updated_at,
        }


@dataclass
class ConversationWindow:
    session_id: str
    messages: list[dict[str, Any]] = field(default_factory=list)

    def to_prompt(self, limit: int = 12) -> str:
        lines = []
        for message in self.messages[-limit:]:
            role = {"user": "User", "assistant": "JARVIS"}.get(message.get("role", ""), message.get("role", ""))
            content = (message.get("content") or "").strip().replace("\n", " ")
            if content:
                lines.append(f"{role}: {content[:400]}")
        return "\n".join(lines)


def _clean_fact_value(value: str) -> str:
    """Trim a captured fact back to the fact itself.

    Captures are deliberately loose, so they can pick up the start of the next
    clause ("Aprameya and I prefer Python"); cut at sentence punctuation and at
    conjunctions that begin a new clause, but keep commas ("Bengaluru, India").
    """
    cleaned = (value or "").strip()
    cleaned = re.split(r"\s*[.!?;\n]\s*", cleaned)[0]
    cleaned = re.split(
        r"\s+(?:and|but|so|while|because|which|who|then|also)\s+"
        r"(?:i|i'?m|we|my|it|that|this|the|you|they|he|she)\b",
        cleaned,
        flags=re.IGNORECASE,
    )[0]
    cleaned = re.sub(r"^(?:a|an|the)\s+", "", cleaned.strip(), flags=re.IGNORECASE)
    return cleaned.strip(" .,;:!?\n\t\"'")


class Memory:
    """Everything JARVIS remembers, behind one small API."""

    def __init__(self, db: Any, settings: Any) -> None:
        self.db = db
        self.settings = settings
        self.embedder = Embedder(settings)
        self._short_term: dict[str, list[dict[str, Any]]] = {}
        self.window_size = 24

    # ------------------------------------------------------------------
    # long-term
    # ------------------------------------------------------------------
    async def remember(self, key: str, value: str, *, category: str = "general", source: str = "user",
                       confidence: float = 1.0) -> dict[str, Any]:
        category = normalise_category(category)
        embedding = None
        if self.embedder.enabled:
            vectors = await self.embedder.embed([f"{key}: {value}"])
            if vectors:
                embedding = pack(vectors[0])
        fact = self.db.upsert_fact(key, value, category=category, confidence=confidence, source=source,
                                   embedding=embedding)
        return fact

    async def recall(self, query: str, *, limit: int = 5, category: str | None = None) -> list[RecallHit]:
        query = (query or "").strip()
        rows = self.db.get_facts(category=normalise_category(category) if category else None, limit=500)
        if not rows:
            return []

        query_words = keywords(query)
        vector: list[float] = []
        if self.embedder.enabled:
            vectors = await self.embedder.embed([query])
            vector = vectors[0] if vectors else []

        scored: list[RecallHit] = []
        for row in rows:
            if not str(row.get("value", "")).strip():
                continue
            text = f"{row['key']} {row['value']}"
            row_words = keywords(text)
            overlap = len(query_words & row_words)
            keyword_score = overlap / max(1, len(query_words)) if query_words else 0.0
            semantic = cosine(vector, unpack(row.get("embedding"))) if vector else 0.0
            bonus = 0.15 if row.get("category") == "profile" else 0.0
            score = max(keyword_score, semantic) + bonus
            if query and score <= 0.05 and not semantic:
                continue
            scored.append(RecallHit(key=row["key"], value=row["value"], category=row.get("category", "general"),
                                    score=score, source=row.get("source", ""), updated_at=row.get("updated_at", "")))
        scored.sort(key=lambda hit: hit.score, reverse=True)
        if not scored and rows:
            scored = [RecallHit(key=r["key"], value=r["value"], category=r.get("category", "general"), score=0.0)
                      for r in rows[:limit]]
        return scored[: max(1, limit)]

    async def forget(self, query: str, category: str | None = None) -> int:
        hits = await self.recall(query, limit=5, category=category)
        removed = 0
        for hit in hits:
            if hit.score > 0.2 or not query:
                if self.db.delete_fact(hit.key, hit.category):
                    removed += 1
        return removed

    def profile(self) -> dict[str, Any]:
        facts = self.db.get_facts(category="profile", limit=100)
        profile = {fact["key"]: fact["value"] for fact in facts if fact["value"]}
        profile.setdefault("name", self.settings.user_name or "")
        return profile

    def facts_by_category(self) -> dict[str, list[dict[str, Any]]]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for fact in self.db.get_facts(limit=500):
            grouped.setdefault(fact.get("category", "general"), []).append(fact)
        return grouped

    async def context_block(self, *, limit: int = 12) -> str:
        """Compact long-term memory for the system prompt."""
        facts = self.db.get_facts(limit=limit)
        if not facts:
            return "(no long-term memory yet)"
        lines = []
        for fact in facts:
            if fact.get("value"):
                lines.append(f"- [{fact.get('category', 'general')}] {fact['key']}: {fact['value']}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # short-term
    # ------------------------------------------------------------------
    def push(self, session_id: str, role: str, content: str, **meta: Any) -> None:
        buffer = self._short_term.setdefault(session_id, [])
        buffer.append({"role": role, "content": content, "ts": datetime.now().isoformat(timespec="seconds"),
                       **meta})
        if len(buffer) > self.window_size * 2:
            del buffer[: len(buffer) - self.window_size]
        if self.db is not None:
            self.db.add_message(session_id, role, content, tool_calls=meta.get("tool_calls"),
                                meta={k: v for k, v in meta.items() if k != "tool_calls"})

    def window(self, session_id: str, limit: int | None = None) -> ConversationWindow:
        limit = limit or self.window_size
        local = self._short_term.get(session_id, [])
        if local:
            messages = local[-limit:]
        elif self.db is not None:
            messages = self.db.recent_messages(session_id, limit=limit)
        else:
            messages = []
        return ConversationWindow(session_id=session_id, messages=messages)

    def clear(self, session_id: str) -> None:
        self._short_term.pop(session_id, None)
        if self.db is not None:
            self.db.clear_session(session_id)

    # ------------------------------------------------------------------
    # entity extraction — "my project is called Smart Water Grid"
    # ------------------------------------------------------------------
    async def absorb(self, text: str, *, source: str = "conversation") -> list[dict[str, Any]]:
        """Opportunistically store durable facts mentioned in conversation."""
        stored: list[dict[str, Any]] = []
        # (pattern, key, category, flags) — name/project captures are
        # case-sensitive on purpose: with IGNORECASE, "[A-Z]" also matches
        # lowercase words and the capture runs past the end of the fact.
        patterns = [
            (r"(?i:\bmy name is)\s+(?P<value>[A-Z][\w'\-]+(?:\s+[A-Z][\w'\-]+)*)", "name", "profile", 0),
            (r"(?i:\bcall me)\s+(?P<value>[A-Z][\w'\-]+)", "name", "profile", 0),
            (r"\bmy email (?:is|address is)\s+(?P<value>[\w.+-]+@[\w-]+\.[\w.]+)", "email", "profile",
             re.IGNORECASE),
            (r"\bmy (?:phone|mobile|number) is\s+(?P<value>[\d\s+\-()]{7,})", "phone", "profile",
             re.IGNORECASE),
            (r"\bi (?:prefer|like|love|use)\s+(?P<value>[^.!?\n]{2,60})", "preference", "preference",
             re.IGNORECASE),
            (r"\bi(?:'m| am) working on\s+(?P<value>[^.!?\n]{2,60})", "current_project", "project",
             re.IGNORECASE),
            (r"\bmy project (?:is called|is named|is)\s+(?P<value>[^.!?\n]{2,60})", "project", "project",
             re.IGNORECASE),
            (r"\bmy goal is\s+(?P<value>[^.!?\n]{2,60})", "goal", "goal", re.IGNORECASE),
            (r"\bi live in\s+(?P<value>[^.!?\n]{2,40})", "location", "profile", re.IGNORECASE),
        ]
        for pattern, key, category, flags in patterns:
            match = re.search(pattern, text, flags)
            if not match:
                continue
            value = _clean_fact_value(match.group("value"))
            if not value or len(value) < 2:
                continue
            existing = self.db.get_fact(key, normalise_category(category))
            if existing and existing.lower() == value.lower():
                continue
            fact = await self.remember(key, value, category=category, source=source)
            stored.append({"key": key, "value": value, "category": fact.get("category")})
        return stored

    # ------------------------------------------------------------------
    def stats(self) -> dict[str, Any]:
        facts = self.db.get_facts(limit=1000)
        by_category: dict[str, int] = {}
        for fact in facts:
            by_category[fact.get("category", "general")] = by_category.get(fact.get("category", "general"), 0) + 1
        return {"facts": len(facts), "by_category": by_category, "sessions": len(self._short_term),
                "embedder": self.embedder.describe()}


__all__ = ["Memory", "RecallHit", "ConversationWindow", "normalise_category", "keywords"]
