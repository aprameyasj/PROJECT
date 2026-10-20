"""Core data structures shared by the brain, the tools and the API."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Literal


def _uid(prefix: str = "") -> str:
    short = uuid.uuid4().hex[:12]
    return f"{prefix}_{short}" if prefix else short


def now_ts() -> float:
    return time.time()


# ---------------------------------------------------------------------------
# Permissions / safety
# ---------------------------------------------------------------------------


class Permission(str, Enum):
    """Safety level attached to every tool.

    SAFE      -> executed immediately, no questions asked.
    CONFIRM   -> the agent pauses and asks the user before executing.
    FORBIDDEN -> never exposed to the model; blocked even if requested.
    """

    SAFE = "safe"
    CONFIRM = "confirm"
    FORBIDDEN = "forbidden"


class ToolCategory(str, Enum):
    COMPUTER = "computer"
    BROWSER = "browser"
    WEB = "web"
    FILES = "files"
    SYSTEM = "system"
    PRODUCTIVITY = "productivity"
    MEMORY = "memory"
    VISION = "vision"
    META = "meta"


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


# ---------------------------------------------------------------------------
# Tool call / result
# ---------------------------------------------------------------------------


@dataclass
class ToolCall:
    """A single tool invocation decided by the brain."""

    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: _uid("call"))
    #: who asked for it: "llm", "offline-intent", "plan", "workflow", "user"
    source: str = "llm"
    reasoning: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ToolResult:
    """Outcome of executing a :class:`ToolCall`."""

    ok: bool
    data: Any = None
    message: str = ""
    tool: str = ""
    call_id: str = ""
    error: str | None = None
    #: what the user should hear; falls back to `message`
    speech: str | None = None
    duration_ms: int = 0
    simulated: bool = False
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    #: nested results when a tool runs a sub-plan / workflow
    steps: list["ToolResult"] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "ok": self.ok,
            "tool": self.tool,
            "call_id": self.call_id,
            "message": self.message,
            "data": self.data,
            "error": self.error,
            "speech": self.speech,
            "duration_ms": self.duration_ms,
            "simulated": self.simulated,
            "artifacts": self.artifacts,
        }
        if self.steps:
            payload["steps"] = [s.to_dict() for s in self.steps]
        return payload

    @property
    def spoken(self) -> str:
        return self.speech or self.message or ("Done." if self.ok else "That did not work.")

    def to_observation(self) -> str:
        """Compact text form fed back into the model as the tool result."""
        import json

        body = json.dumps(self.data, default=str) if self.data is not None else ""
        head = f"[{self.tool}] {'OK' if self.ok else 'ERROR'}: {self.message or self.error or ''}"
        if not body or body in {"{}", "[]"}:
            return head.strip()
        if len(body) > 4000:
            body = body[:4000] + "…(truncated)"
        return f"{head}\n{body}"


# ---------------------------------------------------------------------------
# Pending confirmation (Level 2 safety)
# ---------------------------------------------------------------------------


@dataclass
class PendingAction:
    token: str
    session_id: str
    call: ToolCall
    permission: Permission
    prompt: str
    created_at: float = field(default_factory=now_ts)
    expires_at: float = 0.0
    plan_id: str | None = None
    plan_index: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "token": self.token,
            "session_id": self.session_id,
            "tool": self.call.name,
            "arguments": self.call.arguments,
            "permission": self.permission.value,
            "prompt": self.prompt,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "plan_id": self.plan_id,
            "plan_index": self.plan_index,
        }


# ---------------------------------------------------------------------------
# Plans (multi-step tasks)
# ---------------------------------------------------------------------------


@dataclass
class PlanStep:
    call: ToolCall
    status: TaskStatus = TaskStatus.PENDING
    result: ToolResult | None = None
    #: if True, a failing step aborts the whole plan
    critical: bool = True
    delay_seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": self.call.name,
            "arguments": self.call.arguments,
            "status": self.status.value,
            "critical": self.critical,
            "result": self.result.to_dict() if self.result else None,
        }


@dataclass
class Plan:
    title: str
    steps: list[PlanStep]
    id: str = field(default_factory=lambda: _uid("plan"))
    goal: str = ""
    status: TaskStatus = TaskStatus.PENDING
    created_at: float = field(default_factory=now_ts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "goal": self.goal,
            "status": self.status.value,
            "steps": [s.to_dict() for s in self.steps],
        }


# ---------------------------------------------------------------------------
# Events (streamed to the dashboard over WebSocket)
# ---------------------------------------------------------------------------


class EventType(str, Enum):
    SESSION_START = "session_start"
    TRANSCRIPT = "transcript"          # speech -> text
    USER_MESSAGE = "user_message"
    THINKING = "thinking"              # model reasoning / plan preview
    PLAN = "plan"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    CONFIRMATION_REQUIRED = "confirmation_required"
    CONFIRMATION_RESOLVED = "confirmation_resolved"
    AGENT_MESSAGE = "agent_message"
    SPEECH = "speech"                  # text to be spoken by the client
    REMINDER_FIRED = "reminder_fired"
    NOTIFICATION = "notification"
    ERROR = "error"
    STATUS = "status"


@dataclass
class AgentEvent:
    type: EventType
    payload: dict[str, Any] = field(default_factory=dict)
    session_id: str = ""
    ts: float = field(default_factory=now_ts)
    id: str = field(default_factory=lambda: _uid("ev"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type.value,
            "session_id": self.session_id,
            "ts": self.ts,
            "payload": self.payload,
        }


# ---------------------------------------------------------------------------
# The thing the API returns for one user utterance
# ---------------------------------------------------------------------------


@dataclass
class Turn:
    """One complete Listen -> Understand -> Plan -> Act -> Respond cycle."""

    session_id: str
    user_text: str
    response: str = ""
    speech: str = ""
    intent: str = ""
    ok: bool = True
    steps: list[dict[str, Any]] = field(default_factory=list)
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)
    plan: Plan | None = None
    pending: PendingAction | None = None
    simulated: bool = False
    provider: str = ""
    model: str = ""
    duration_ms: int = 0
    created_at: float = field(default_factory=now_ts)
    id: str = field(default_factory=lambda: _uid("turn"))
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "user_text": self.user_text,
            "response": self.response,
            "speech": self.speech,
            "intent": self.intent,
            "ok": self.ok,
            "steps": self.steps,
            "tool_calls": [c.to_dict() for c in self.tool_calls],
            "tool_results": [r.to_dict() for r in self.tool_results],
            "plan": self.plan.to_dict() if self.plan else None,
            "pending": self.pending.to_dict() if self.pending else None,
            "simulated": self.simulated,
            "provider": self.provider,
            "model": self.model,
            "duration_ms": self.duration_ms,
            "created_at": self.created_at,
            "error": self.error,
        }


@dataclass
class Message:
    role: Literal["system", "user", "assistant", "tool"]
    content: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None
    name: str | None = None

    def to_openai(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"role": self.role}
        if self.content is not None:
            payload["content"] = self.content
        if self.tool_calls:
            payload["tool_calls"] = self.tool_calls
        if self.tool_call_id:
            payload["tool_call_id"] = self.tool_call_id
        if self.name:
            payload["name"] = self.name
        return payload


# ---------------------------------------------------------------------------
# (de)serialisation helpers
# ---------------------------------------------------------------------------


def plan_from_dict(payload: dict[str, Any]) -> Plan:
    """Rebuild a Plan from its dict form (used to resume paused plans)."""
    steps: list[PlanStep] = []
    for raw in payload.get("steps", []) or []:
        status = raw.get("status", "pending")
        try:
            step_status = TaskStatus(status)
        except ValueError:
            step_status = TaskStatus.PENDING
        steps.append(
            PlanStep(
                call=ToolCall(name=raw.get("tool", ""), arguments=raw.get("arguments") or {}, source="plan"),
                status=step_status,
                critical=bool(raw.get("critical", True)),
            )
        )
    try:
        plan_status = TaskStatus(payload.get("status", "pending"))
    except ValueError:
        plan_status = TaskStatus.PENDING
    return Plan(
        title=payload.get("title", ""),
        steps=steps,
        id=payload.get("id") or _uid("plan"),
        goal=payload.get("goal", ""),
        status=plan_status,
    )


Plan.from_dict = staticmethod(plan_from_dict)  # type: ignore[attr-defined]
