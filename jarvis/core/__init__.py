"""Core primitives for the JARVIS brain.

Heavy members (the agent, executor and planner) are imported lazily so that
light modules such as :mod:`jarvis.core.types` never create an import cycle
with :mod:`jarvis.llm`.
"""

from __future__ import annotations

from typing import Any

from .types import (
    AgentEvent,
    EventType,
    Message,
    PendingAction,
    Permission,
    Plan,
    PlanStep,
    TaskStatus,
    ToolCall,
    ToolResult,
    Turn,
)

_LAZY = {
    "Jarvis": ".agent",
    "JarvisOptions": ".agent",
    "Executor": ".executor",
    "ExecutionOutcome": ".executor",
    "PlanRun": ".executor",
    "Memory": ".memory",
    "Planner": ".planner",
    "SafetyGate": ".safety",
    "EventBus": ".events",
    "bus": ".events",
    "ContextBuilder": ".context",
}

__all__ = [
    "AgentEvent", "EventType", "Message", "PendingAction", "Permission", "Plan", "PlanStep",
    "TaskStatus", "ToolCall", "ToolResult", "Turn",
    "Jarvis", "JarvisOptions", "Executor", "ExecutionOutcome", "PlanRun", "Memory", "Planner",
    "SafetyGate", "EventBus", "bus", "ContextBuilder",
]


def __getattr__(name: str) -> Any:  # PEP 562 lazy attribute access
    module_name = _LAZY.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    module = import_module(module_name, __name__)
    value = getattr(module, name)
    globals()[name] = value
    return value
