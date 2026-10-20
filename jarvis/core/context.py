"""Context assembly — what the brain is allowed to see on each turn.

The context is deliberately small and structured:

    system prompt (persona + safety + live tool catalogue)
    long-term memory block (profile, projects, preferences)
    environment snapshot (time, backend, focused window, running apps)
    short-term memory (last N turns of this session)
    the user's utterance

Everything is regenerated per turn, so enabling/disabling a tool or learning a
new fact takes effect immediately without restarting JARVIS.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ..llm.prompts import build_system_prompt
from .types import Message


@dataclass
class Environment:
    """A cheap snapshot of the machine, refreshed at most every few seconds."""

    captured_at: float = 0.0
    data: dict[str, Any] = field(default_factory=dict)

    def to_text(self) -> str:
        if not self.data:
            return ""
        parts = []
        if self.data.get("time"):
            parts.append(f"Local time: {self.data['time']}")
        if self.data.get("backend"):
            parts.append(f"Action backend: {self.data['backend']}")
        if self.data.get("running_apps"):
            apps = ", ".join(self.data["running_apps"][:6])
            parts.append(f"Running apps: {apps}")
        if self.data.get("active_tab"):
            parts.append(f"Active browser tab: {self.data['active_tab']}")
        if self.data.get("focused_window"):
            parts.append(f"Focused window: {self.data['focused_window']}")
        if self.data.get("pending_reminders") is not None:
            parts.append(f"Pending reminders: {self.data['pending_reminders']}")
        if self.data.get("open_tasks"):
            parts.append(f"Open tasks: {self.data['open_tasks']}")
        return "\n".join(parts)


class ContextBuilder:
    def __init__(self, settings: Any, registry: Any, memory: Any, db: Any = None,
                 backend: Any = None, history_limit: int = 10) -> None:
        self.settings = settings
        self.registry = registry
        self.memory = memory
        self.db = db
        self.backend = backend
        self.history_limit = history_limit
        self._env_cache: Environment = Environment()
        self._env_ttl = 4.0

    # ------------------------------------------------------------------
    def environment(self, force: bool = False) -> Environment:
        import time

        now = time.time()
        if not force and self._env_cache.data and now - self._env_cache.captured_at < self._env_ttl:
            return self._env_cache

        data: dict[str, Any] = {"time": datetime.now().strftime("%A %d %B %Y %H:%M:%S")}
        if self.backend is not None:
            try:
                snapshot = self.backend.describe()
                data["backend"] = snapshot.get("name")
                data["running_apps"] = snapshot.get("running_apps", [])
                tabs = snapshot.get("tabs") or []
                active = snapshot.get("active_tab")
                if active:
                    data["active_tab"] = active
                elif tabs:
                    data["active_tab"] = tabs[-1]
                if snapshot.get("simulated"):
                    data["simulated"] = True
            except Exception:  # noqa: BLE001
                pass
            try:
                windows = self.backend.list_windows()
                focused = next((w for w in windows if w.get("focused")), windows[0] if windows else None)
                if focused:
                    data["focused_window"] = focused.get("title") or focused.get("app")
            except Exception:  # noqa: BLE001
                pass
        if self.db is not None:
            try:
                stats = self.db.stats()
                data["pending_reminders"] = stats.get("pending_reminders", 0)
            except Exception:  # noqa: BLE001
                pass
            try:
                tasks = self.db.list_tasks(status="todo", limit=5)
                data["open_tasks"] = [t["title"] for t in tasks]
            except Exception:  # noqa: BLE001
                pass

        self._env_cache = Environment(captured_at=now, data=data)
        return self._env_cache

    # ------------------------------------------------------------------
    async def system_prompt(self, *, extra: str = "") -> str:
        profile = self.memory.profile() if self.memory is not None else {}
        facts = self.db.get_facts(limit=40) if self.db is not None else []
        return build_system_prompt(
            settings=self.settings,
            catalogue=self.registry.prompt_catalogue(),
            profile=profile,
            facts=facts,
            extra=extra,
        )

    # ------------------------------------------------------------------
    def messages(self, session_id: str, user_text: str, system_prompt: str, *,
                 environment_note: bool = True) -> list[Message]:
        history: list[Message] = []
        if self.memory is not None:
            for entry in self.memory.window(session_id, limit=self.history_limit).messages[-self.history_limit:]:
                role = entry.get("role")
                content = entry.get("content") or ""
                if role in {"user", "assistant"} and content:
                    history.append(Message(role=role, content=content))  # type: ignore[arg-type]

        environment = self.environment()
        environment_block = environment.to_text() if environment_note else ""
        if environment_block:
            system_prompt = f"{system_prompt}\n\n# Right now\n{environment_block}"

        return [Message(role="system", content=system_prompt), *history, Message(role="user", content=user_text)]

    # ------------------------------------------------------------------
    def planner_context(self, session_id: str) -> str:
        environment = self.environment()
        blocks = [environment.to_text()]
        if self.memory is not None:
            try:
                profile = self.memory.profile()
                blocks.append("Profile: " + ", ".join(f"{k}={v}" for k, v in profile.items() if v))
            except Exception:  # noqa: BLE001
                pass
        recent = self.memory.window(session_id, limit=6).to_prompt(limit=6) if self.memory is not None else ""
        if recent:
            blocks.append("Recent conversation:\n" + recent)
        return "\n".join(b for b in blocks if b)

    def invalidate(self) -> None:
        self._env_cache = Environment()


__all__ = ["ContextBuilder", "Environment"]
