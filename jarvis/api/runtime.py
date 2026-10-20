"""Shared runtime for the API server.

Owns exactly one :class:`~jarvis.core.agent.Jarvis`, the reminder scheduler and
the voice stack, and fans every agent event out to the connected WebSocket
clients. REST routes and the WS route both talk to this object, so there is a
single source of truth about what JARVIS is doing right now.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from ..automation.scheduler import ReminderScheduler
from ..core.agent import Jarvis, JarvisOptions
from ..core.events import EventBus
from ..core.types import AgentEvent, EventType
from ..voice.listener import ListenerConfig, VoiceListener
from ..voice.stt import SpeechToText
from ..voice.tts import TextToSpeech


@dataclass
class Runtime:
    agent: Jarvis
    bus: EventBus
    scheduler: ReminderScheduler
    stt: SpeechToText
    tts: TextToSpeech
    listener: VoiceListener | None = None
    started: bool = False
    _clients: list[Callable[[AgentEvent], Awaitable[None]]] = field(default_factory=list)

    # ------------------------------------------------------------------
    def subscribe(self, sender: Callable[[AgentEvent], Awaitable[None]]) -> Callable[[], None]:
        """Register a WebSocket sender. Returns an unsubscribe callable."""
        self._clients.append(sender)

        def unsubscribe() -> None:
            if sender in self._clients:
                self._clients.remove(sender)

        return unsubscribe

    async def _broadcast(self, event: AgentEvent) -> None:
        for sender in list(self._clients):
            try:
                await sender(event)
            except Exception:  # noqa: BLE001 — a dead client must not break the agent
                if sender in self._clients:
                    self._clients.remove(sender)

    # ------------------------------------------------------------------
    async def start(self) -> None:
        if self.started:
            return
        self.started = True
        self.bus.subscribe(self._broadcast)
        self.bus.subscribe(self._speak, events=[EventType.SPEECH])
        engine = await self.scheduler.start()
        self.bus.emit(EventType.STATUS, {"scheduler": engine, "runtime": "started"})
        if self.listener is not None and self.agent.settings.listen_on_start:
            await self.listener.start()

    async def stop(self) -> None:
        self.started = False
        await self.scheduler.stop()
        if self.listener is not None:
            await self.listener.stop()

    async def _speak(self, event: AgentEvent) -> None:
        """Server-side speech: only when a real speaker is attached."""
        if event.payload.get("audio_url"):
            return
        if self.tts.provider not in {"system", "openai"}:
            return
        result = await self.tts.speak(event.payload.get("text", ""))
        if result.audio_path:
            name = Path(result.audio_path).name
            await self.bus.publish(AgentEvent(
                type=EventType.SPEECH, session_id=event.session_id,
                payload={"text": event.payload.get("text", ""), "audio_url": f"/api/audio/{name}",
                         "provider": self.tts.provider},
            ))

    # ------------------------------------------------------------------
    def status(self) -> dict[str, Any]:
        return {
            "agent": self.agent.describe(),
            "scheduler": self.scheduler.describe(),
            "stt": self.stt.status(),
            "tts": self.tts.status(),
            "listener": self.listener.status() if self.listener else {"available": False},
            "clients": len(self._clients),
        }


def build_runtime(options: JarvisOptions | None = None) -> Runtime:
    """Create the agent, scheduler and voice stack, all wired together."""
    from ..config.settings import get_settings

    options = options or JarvisOptions()
    settings = options.settings or get_settings()
    bus = options.bus or EventBus(history=1000)
    options.bus = bus
    options.settings = settings

    agent = Jarvis(options)
    stt = SpeechToText(settings)
    tts = TextToSpeech(settings)
    scheduler = ReminderScheduler(agent.db, bus, notifier=agent.notify)
    agent.attach_scheduler(scheduler)

    listener: VoiceListener | None = None
    if settings.listen_on_start or stt.status().get("microphone"):
        async def on_command(text: str) -> None:
            await agent.process(text, speak=settings.speak_responses)

        listener = VoiceListener(settings, stt, on_command, bus=bus,
                                 config=ListenerConfig(wake_words=tuple(settings.wake_words)))

    return Runtime(agent=agent, bus=bus, scheduler=scheduler, stt=stt, tts=tts, listener=listener)


__all__ = ["Runtime", "build_runtime"]
