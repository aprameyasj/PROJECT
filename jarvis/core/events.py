"""Event bus — how the brain tells the world what it is doing.

Every meaningful moment (transcript, decision, tool call, result, confirmation
request, reminder firing, spoken reply) becomes an :class:`AgentEvent`. The
WebSocket endpoint forwards them to the React dashboard; the CLI prints them;
tests assert on them. Nothing in the core knows who is listening.
"""

from __future__ import annotations

import asyncio
import inspect
from collections import deque
from typing import Any, Awaitable, Callable, Iterable

from .types import AgentEvent, EventType

Handler = Callable[[AgentEvent], Any]


class EventBus:
    def __init__(self, history: int = 500) -> None:
        self._handlers: list[Handler] = []
        self._filters: dict[EventType, list[Handler]] = {}
        self.history: deque[AgentEvent] = deque(maxlen=history)
        self._loop: asyncio.AbstractEventLoop | None = None

    # ------------------------------------------------------------------
    def subscribe(self, handler: Handler, events: Iterable[EventType] | None = None) -> Callable[[], None]:
        """Register a handler. Returns an unsubscribe function."""
        if events is None:
            self._handlers.append(handler)

            def unsubscribe() -> None:
                if handler in self._handlers:
                    self._handlers.remove(handler)

            return unsubscribe

        for event_type in events:
            self._filters.setdefault(event_type, []).append(handler)

        def unsubscribe_typed() -> None:
            for event_type in events:
                bucket = self._filters.get(event_type, [])
                if handler in bucket:
                    bucket.remove(handler)

        return unsubscribe_typed

    # ------------------------------------------------------------------
    async def publish(self, event: AgentEvent) -> None:
        self.history.append(event)
        handlers = list(self._handlers) + list(self._filters.get(event.type, []))
        for handler in handlers:
            try:
                result = handler(event)
                if inspect.isawaitable(result):
                    await result
            except Exception:  # noqa: BLE001 — a bad listener must never break the agent
                pass

    def emit(self, event_type: EventType, payload: dict[str, Any] | None = None, session_id: str = "") -> AgentEvent:
        """Synchronous helper that schedules the publish on the running loop."""
        event = AgentEvent(type=event_type, payload=payload or {}, session_id=session_id)
        self.history.append(event)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop and loop.is_running():
            loop.create_task(self._dispatch(event))
        else:
            self._dispatch_sync(event)
        return event

    async def _dispatch(self, event: AgentEvent) -> None:
        handlers = list(self._handlers) + list(self._filters.get(event.type, []))
        for handler in handlers:
            try:
                result = handler(event)
                if inspect.isawaitable(result):
                    await result
            except Exception:  # noqa: BLE001
                pass

    def _dispatch_sync(self, event: AgentEvent) -> None:
        handlers = list(self._handlers) + list(self._filters.get(event.type, []))
        for handler in handlers:
            try:
                result = handler(event)
                if inspect.isawaitable(result):
                    try:
                        asyncio.run(result)
                    except RuntimeError:
                        pass
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------------
    def recent(self, limit: int = 50, event_type: EventType | None = None) -> list[dict[str, Any]]:
        events = list(self.history)
        if event_type is not None:
            events = [e for e in events if e.type is event_type]
        return [e.to_dict() for e in events[-limit:]]

    def clear(self) -> None:
        self.history.clear()


#: process-wide bus used by the API server and the CLI
bus = EventBus()


__all__ = ["EventBus", "bus", "EventType"]
