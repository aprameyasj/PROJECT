"""Brain factory + hybrid fallback.

`JARVIS_LLM_PROVIDER=auto` (the default) picks the API brain when a key exists
and the offline rule engine otherwise. When a key *is* configured we wrap it in
:class:`HybridBrain`: if the API times out or errors, the deterministic engine
answers instead of JARVIS going silent. That single decision is what makes the
agent usable on a plane, in CI, or during an outage.
"""

from __future__ import annotations

from typing import Any

from ..core.types import Message
from .base import Brain, Decision
from .offline import OfflineBrain
from .openai_provider import OpenAIBrain


class HybridBrain:
    """LLM first, rule engine as a safety net."""

    name = "hybrid"

    def __init__(self, primary: Brain, fallback: Brain) -> None:
        self.primary = primary
        self.fallback = fallback
        self.available = True
        self.fallbacks = 0

    def describe(self) -> dict[str, Any]:
        primary = self.primary.describe()
        return {
            "name": self.name,
            "model": primary.get("model"),
            "primary": primary,
            "fallback": self.fallback.describe(),
            "fallbacks_used": self.fallbacks,
            "available": True,
        }

    async def decide(self, messages: list[Message], *, tools: list[dict[str, Any]] | None = None,
                     catalogue: str = "", context: dict[str, Any] | None = None) -> Decision:
        decision = await self.primary.decide(messages, tools=tools or [], catalogue=catalogue, context=context)
        if decision.error is None and (decision.text or decision.tool_calls):
            decision.provider = f"{self.name}/{decision.provider}"
            return decision

        self.fallbacks += 1
        offline = await self.fallback.decide(messages, tools=tools or [], catalogue=catalogue, context=context)
        offline.provider = f"{self.name}/offline"
        offline.error = decision.error
        return offline

    async def plan(self, goal: str, *, catalogue: str, context: str = "") -> dict[str, Any]:
        result = await self.primary.plan(goal, catalogue=catalogue, context=context)
        if result.get("steps"):
            return result
        fallback = await self.fallback.plan(goal, catalogue=catalogue, context=context)
        if fallback.get("steps"):
            fallback["notes"] = (fallback.get("notes") or "") + " (LLM plan was empty; used the offline planner)"
            return fallback
        return result or fallback

    async def summarise(self, *, user_text: str, observations: list[str]) -> str:
        text = await self.primary.summarise(user_text=user_text, observations=observations)
        if text:
            return text
        return await self.fallback.summarise(user_text=user_text, observations=observations)


def build_brain(settings: Any, registry: Any = None) -> Brain:
    provider = str(getattr(settings, "llm_provider", "auto")).lower()

    if provider == "offline":
        return OfflineBrain(settings, registry)

    openai_brain = OpenAIBrain(settings)
    if not openai_brain.available:
        return OfflineBrain(settings, registry)

    offline = OfflineBrain(settings, registry)
    return HybridBrain(openai_brain, offline)


__all__ = ["build_brain", "HybridBrain", "OpenAIBrain", "OfflineBrain", "Decision"]
