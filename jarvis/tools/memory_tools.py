"""Memory tools — how the model reads and writes long-term memory.

These are the tools behind:

    "Jarvis, remember that my project is called Smart Water Grid."
    "What do you know about me?"
    "Forget my phone number."
"""

from __future__ import annotations

from typing import Any, Literal

from ..core.memory import normalise_category
from ..core.types import Permission, ToolCategory, ToolResult
from .registry import ToolContext, tool


@tool(permission=Permission.SAFE, category=ToolCategory.MEMORY, mutating=True,
      examples=["Remember that my favourite IDE is VS Code"])
async def remember_fact(ctx: ToolContext, key: str = "", value: str = "", note: str = "",
                        category: Literal["profile", "preference", "project", "goal", "workflow", "general"] = "general") -> ToolResult:
    """Store something in long-term memory so JARVIS remembers it later.

    Args:
        key: Short label, e.g. "preferred_ide" or "project".
        value: The fact itself, e.g. "VS Code".
        note: Free-form sentence when there is no obvious key/value split.
        category: profile, preference, project, goal, workflow or general.
    """
    if not (key and value) and not note:
        return ToolResult(ok=False, message="Tell me what to remember.", speech="What should I remember?")

    if not (key and value) and note:
        key = _key_from_note(note)
        value = note

    fact = await ctx.memory.remember(key, value, category=normalise_category(category))
    return ToolResult(ok=True, message=f"Remembered {key} = {value}",
                      speech=f"Got it — I'll remember that {key.replace('_', ' ')} is {value}.",
                      data={"fact": fact})


def _key_from_note(note: str) -> str:
    words = [w for w in note.lower().replace(",", "").split()[:4] if len(w) > 2]
    return "_".join(words) or "note"


@tool(permission=Permission.SAFE, category=ToolCategory.MEMORY)
async def recall_fact(ctx: ToolContext, query: str = "", limit: int = 5,
                      category: str = "") -> ToolResult:
    """Look something up in long-term memory.

    Args:
        query: What to recall, e.g. "my projects" or "preferred language".
        limit: Maximum number of memories to return.
        category: Optional category filter (profile, preference, project, goal, general).
    """
    hits = await ctx.memory.recall(query or "everything", limit=max(1, min(int(limit), 25)),
                                   category=category or None)
    if not hits:
        return ToolResult(ok=True, message="Nothing stored yet.",
                          speech=f"I don't have anything stored about {query or 'that'} yet.",
                          data={"results": []})
    data = [hit.to_dict() for hit in hits]
    spoken = "; ".join(f"{hit.key.replace('_', ' ')}: {hit.value}" for hit in hits[:3])
    return ToolResult(ok=True, message=f"{len(hits)} memor(y/ies) matched",
                      speech=f"From memory — {spoken}.", data={"results": data, "query": query})


@tool(permission=Permission.CONFIRM, category=ToolCategory.MEMORY, mutating=True)
async def forget_fact(ctx: ToolContext, query: str, category: str = "") -> ToolResult:
    """Delete memories matching a query.

    Args:
        query: What to forget, e.g. "phone number".
        category: Optional category filter.
    """
    removed = await ctx.memory.forget(query, category=category or None)
    if not removed:
        return ToolResult(ok=False, message=f"Nothing matched “{query}”.",
                          speech=f"I didn't have anything stored about {query}.")
    return ToolResult(ok=True, message=f"Forgot {removed} item(s)",
                      speech=f"Done, I've forgotten {query}.", data={"removed": removed})


@tool(permission=Permission.SAFE, category=ToolCategory.MEMORY)
async def show_profile(ctx: ToolContext) -> ToolResult:
    """Show everything JARVIS currently knows about the user."""
    profile = ctx.memory.profile()
    grouped = ctx.memory.facts_by_category()
    stats = ctx.memory.stats()
    lines = []
    for category in sorted(grouped):
        lines.append(f"{category.title()}:")
        for fact in grouped[category][:12]:
            if fact.get("value"):
                lines.append(f"  - {fact['key']}: {fact['value']}")
    spoken = ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in list(profile.items())[:3] if v)
    return ToolResult(
        ok=True,
        message=f"{stats['facts']} stored facts across {len(grouped)} categories",
        speech=f"Here's what I know: {spoken}." if spoken else "I don't know much about you yet — tell me.",
        data={"profile": profile, "categories": grouped, "stats": stats, "text": "\n".join(lines)},
    )


@tool(permission=Permission.SAFE, category=ToolCategory.MEMORY, mutating=True)
async def set_user_name(ctx: ToolContext, name: str) -> ToolResult:
    """Set the name JARVIS calls the user.

    Args:
        name: The user's name.
    """
    if not name.strip():
        return ToolResult(ok=False, message="No name supplied.")
    fact = await ctx.memory.remember("name", name.strip(), category="profile", source="user")
    return ToolResult(ok=True, message=f"User name set to {name.strip()}",
                      speech=f"Nice to meet you, {name.strip()}.", data={"fact": fact})


@tool(permission=Permission.SAFE, category=ToolCategory.MEMORY)
async def search_memory(ctx: ToolContext, query: str, limit: int = 10) -> ToolResult:
    """Full-text search across every stored memory.

    Args:
        query: Text to search for.
        limit: Maximum results.
    """
    rows = ctx.db.search_facts(query, limit=max(1, min(int(limit), 50)))
    if not rows:
        return ToolResult(ok=True, message=f"No memory matches “{query}”.",
                          speech=f"Nothing in memory about {query}.", data={"results": []})
    return ToolResult(ok=True, message=f"{len(rows)} match(es)",
                      speech=f"I found {len(rows)} memor" + ("ies" if len(rows) != 1 else "y") + f" about {query}.",
                      data={"results": [{k: r[k] for k in ("key", "value", "category", "updated_at") if k in r}
                                        for r in rows]})


@tool(permission=Permission.SAFE, category=ToolCategory.MEMORY)
def memory_stats(ctx: ToolContext) -> ToolResult:
    """Report how much JARVIS remembers and how memory is configured."""
    stats: dict[str, Any] = ctx.memory.stats()
    stats["database"] = ctx.db.stats()
    return ToolResult(ok=True, message=f"{stats['facts']} facts stored",
                      speech=f"I'm holding {stats['facts']} memories.", data=stats)
