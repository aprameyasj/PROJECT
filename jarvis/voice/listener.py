"""Microphone listener with an optional wake word.

Runs the loop you actually want on a real machine:

    mic -> energy VAD -> chunk of speech -> STT -> "hey JARVIS, close this tab"
        -> wake word stripped -> agent.process(text) -> TTS

Everything optional is optional: without `sounddevice`/`numpy` the listener
reports itself unavailable and the dashboard's browser microphone takes over
(which is what happens in the sandboxed live preview).
"""

from __future__ import annotations

import asyncio
import queue
import re
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from ..core.events import EventBus
from ..core.types import AgentEvent, EventType
from .stt import SpeechToText


@dataclass
class ListenerConfig:
    sample_rate: int = 16000
    block_seconds: float = 0.4
    silence_seconds: float = 1.1
    max_utterance_seconds: float = 20.0
    rms_threshold: float = 0.012
    require_wake_word: bool = True
    wake_words: tuple[str, ...] = ("jarvis", "hey jarvis", "hi jarvis")


class VoiceListener:
    def __init__(self, settings: Any, stt: SpeechToText, on_command: Callable[[str], Awaitable[None]],
                 bus: EventBus | None = None, config: ListenerConfig | None = None) -> None:
        self.settings = settings
        self.stt = stt
        self.on_command = on_command
        self.bus = bus or EventBus()
        self.config = config or ListenerConfig(wake_words=tuple(getattr(settings, "wake_words", ("jarvis",))))
        self._task: asyncio.Task[None] | None = None
        self._running = False
        self.last_error: str | None = None
        self.heard: list[str] = []
        self.armed = not self.config.require_wake_word

    # ------------------------------------------------------------------
    def status(self) -> dict[str, Any]:
        return {
            "running": self._running,
            "available": _audio_available(),
            "wake_word_required": self.config.require_wake_word,
            "wake_words": list(self.config.wake_words),
            "armed": self.armed,
            "last_error": self.last_error,
            "heard": self.heard[-10:],
            "stt": self.stt.status(),
        }

    # ------------------------------------------------------------------
    async def start(self) -> bool:
        if self._running:
            return True
        if not _audio_available():
            self.last_error = (
                "No audio capture available (install `sounddevice` + `numpy`, and make sure a microphone "
                "exists). Use the dashboard microphone instead — it transcribes in the browser."
            )
            return False
        self._running = True
        self._task = asyncio.create_task(self._loop())
        await self.bus.publish(AgentEvent(type=EventType.STATUS, payload={"listener": "started",
                                                                         **self.status()}))
        return True

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None

    # ------------------------------------------------------------------
    async def _loop(self) -> None:
        import numpy as np  # type: ignore
        import sounddevice as sd  # type: ignore

        config = self.config
        block = int(config.sample_rate * config.block_seconds)
        audio_queue: "queue.Queue[Any]" = queue.Queue()

        def callback(indata, frames, _time_info, status) -> None:  # noqa: ANN001
            if status:
                pass
            audio_queue.put(bytes(indata))

        try:
            with sd.RawInputStream(samplerate=config.sample_rate, blocksize=block, dtype="int16",
                                   channels=1, callback=callback):
                while self._running:
                    frames: list[bytes] = []
                    speaking = False
                    silence = 0.0
                    started = time.time()

                    while self._running:
                        try:
                            chunk = audio_queue.get(timeout=0.5)
                        except queue.Empty:
                            continue
                        rms = float(np.sqrt(np.mean(np.frombuffer(chunk, dtype=np.int16).astype("float32") ** 2))
                                      / 32768.0)
                        if rms > config.rms_threshold:
                            speaking = True
                            silence = 0.0
                            frames.append(chunk)
                        elif speaking:
                            silence += config.block_seconds
                            frames.append(chunk)
                            if silence >= config.silence_seconds:
                                break
                        if time.time() - started > config.max_utterance_seconds:
                            break

                    if not frames:
                        continue
                    await self._handle_utterance(b"".join(frames))
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"{type(exc).__name__}: {exc}"
            self._running = False
            await self.bus.publish(AgentEvent(type=EventType.ERROR, payload={"stage": "listener",
                                                                             "error": self.last_error}))

    async def _handle_utterance(self, audio: bytes) -> None:
        transcript = await self.stt.transcribe_bytes(audio, filename="mic.wav")
        text = transcript.text.strip()
        if not text:
            return
        self.heard.append(text)
        await self.bus.publish(AgentEvent(type=EventType.TRANSCRIPT, payload={"text": text,
                                                                              "provider": transcript.provider,
                                                                              "confidence": transcript.confidence}))

        cleaned, wake = _strip_wake_word(text, self.config.wake_words)
        if self.config.require_wake_word and not self.armed:
            if not wake:
                return            # ignored: not addressed to JARVIS
            self.armed = True
        if not cleaned:
            self.armed = not self.config.require_wake_word
            await self.bus.publish(AgentEvent(type=EventType.NOTIFICATION,
                                              payload={"level": "listener", "message": "Yes?"}))
            return

        self.armed = not self.config.require_wake_word
        try:
            await self.on_command(cleaned)
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"command failed: {exc}"
            await self.bus.publish(AgentEvent(type=EventType.ERROR, payload={"stage": "command",
                                                                             "error": self.last_error}))


def _strip_wake_word(text: str, wake_words: tuple[str, ...] | list[str]) -> tuple[str, bool]:
    lowered = text.lower().strip()
    for wake in sorted(wake_words, key=len, reverse=True):
        pattern = rf"^(?:hey |hi |ok |okay )?{re.escape(wake.lower())}[\s,,:.\-]*"
        match = re.match(pattern, lowered)
        if match:
            return text[match.end():].strip(), True
        if wake.lower() in lowered:
            index = lowered.index(wake.lower()) + len(wake)
            return text[index:].strip(" ,.:-"), True
    return text.strip(), False


def _audio_available() -> bool:
    import importlib.util

    if importlib.util.find_spec("sounddevice") is None or importlib.util.find_spec("numpy") is None:
        return False
    try:
        import sounddevice  # type: ignore

        return any(d.get("max_input_channels", 0) > 0 for d in sounddevice.query_devices())
    except Exception:  # noqa: BLE001
        return False


__all__ = ["VoiceListener", "ListenerConfig"]
