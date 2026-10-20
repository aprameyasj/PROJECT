"""LLM provider interface.

The brain only ever talks to a :class:`Brain` — either an LLM-backed one or the
offline rule engine. That abstraction is what lets JARVIS run with no API key at
all (and makes the whole agent unit-testable).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from ..core.types import Message, ToolCall


@dataclass
class Decision:
    """What the brain decided to do next."""

    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    intent: str = ""
    provider: str = "offline"
    model: str = "rule-engine"
    finish_reason: str = ""
    usage: dict[str, Any] = field(default_factory=dict)
    raw: Any = None
    #: confidence of the offline parser (0..1); LLM decisions report 1.0
    confidence: float = 1.0
    error: str | None = None

    @property
    def requires_tool(self) -> bool:
        return bool(self.tool_calls)

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "tool_calls": [c.to_dict() for c in self.tool_calls],
            "intent": self.intent,
            "provider": self.provider,
            "model": self.model,
            "finish_reason": self.finish_reason,
            "usage": self.usage,
            "confidence": self.confidence,
            "error": self.error,
        }


@runtime_checkable
class Brain(Protocol):
    """Anything that can turn a conversation into a :class:`Decision`."""

    name: str
    available: bool

    def describe(self) -> dict[str, Any]: ...

    async def decide(
        self,
        messages: list[Message],
        *,
        tools: list[dict[str, Any]],
        catalogue: str = "",
        context: dict[str, Any] | None = None,
    ) -> Decision: ...

    async def plan(self, goal: str, *, catalogue: str, context: str = "") -> dict[str, Any]: ...

    async def summarise(self, *, user_text: str, observations: list[str]) -> str: ...


__all__ = ["Brain", "Decision"]
